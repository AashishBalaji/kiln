# Build validation notes — what was actually run, and what wasn't

This project runs inside a sandbox with a fixed, non-expandable disk
allowance (~12GB free at the start of this work). That constraint shaped
which parts of the Yocto pipeline could be verified with a real `bitbake`
run versus which had to be verified by other real means (recipe parsing,
checksum verification against actual fetched sources, native-only builds).
This file states plainly which is which — no result below is assumed or
estimated.

## What was run for real, and succeeded

1. **Layer and recipe parsing**, `bitbake -p` with `meta-infotainment`
   added to `bblayers.conf`: 922 `.bb` files parsed, 0 errors. This proves
   `layer.conf`, `cjson_1.7.19.bb`, and `audio-zone-manager_1.0.bb` are all
   syntactically and semantically valid Yocto recipes/layers — bitbake's
   own parser, not a lint tool, checked them.

2. **`LIC_FILES_CHKSUM` verification**: rather than guess the MD5 of
   cJSON's `LICENSE` file, the real `v1.7.19`-tagged source was cloned and
   `md5sum`'d directly (`218947f77e8cb8e2fa02918dc41c50d0`). The `MIT`
   common-license checksum in `audio-zone-manager_1.0.bb` was checked the
   same way, against Poky's own `meta/files/common-licenses/MIT`.

3. **A real, complete `bitbake cjson-native` build** (278/278 tasks
   succeeded, 0 failures): this genuinely fetched cJSON from GitHub via
   the `git://` fetcher with the pinned `SRCREV`, ran its real CMake
   build, and populated a native sysroot. The actual build artifacts exist
   on disk: `libcjson.so.1.7.19`, `libcjson.a`, and the installed
   `cjson/cJSON.h` header. This is the real, end-to-end proof that the
   recipe's `SRC_URI`/`SRCREV`/`inherit cmake`/`EXTRA_OECMAKE` logic
   works — not just that it parses.

   Getting here required building bitbake's entire *native* toolchain
   bootstrap first (binutils-native, gcc toolchain pieces, perl-native,
   openssl-native, cmake-native, ninja-native, etc. — 278 tasks total),
   which this sandbox's disk could absorb (final usage: ~3GB for the full
   native stack).

## What was attempted for real, and found infeasible here — not silently skipped

4. **A real target build**, `bitbake audio-zone-manager` for the
   configured `MACHINE = "qemux86-64"`, was started (not simulated). Its
   task graph resolved to **881 tasks** — roughly 3x the native-only
   build — because it was the first *target*-architecture build attempted
   in this session: building for `qemux86-64` (as opposed to building
   native host tools) requires bootstrapping an entirely separate
   cross-compilation toolchain (`binutils-cross-x86_64`,
   `gcc-cross-x86_64`, `linux-libc-headers`, `glibc-initial`, `glibc`)
   before a single line of `audio-zone-manager.c` or `cjson`'s target
   variant gets compiled.

   This build was stopped partway through (at task ~292/881, still inside
   the native-tool prerequisites, before the cross-toolchain/glibc stage
   had even started) after disk headroom dropped from 9.4GB to 6.6GB free
   in a few minutes of that run alone. Continuing into the gcc-cross/glibc
   bootstrap — historically several more GB by itself — risked exhausting
   this sandbox's fixed disk allowance mid-build, which would have failed
   destructively rather than cleanly.

   **This is the same constraint already identified before any of
   `meta-infotainment` was written** (a full Yocto image build needs
   40–100GB+ and wasn't attempted for that reason) — this run confirms the
   same cross-toolchain bootstrap cost applies even to building a single
   small C recipe for a real target machine, not only to whole images. The
   `-native` build class extension exists precisely so a recipe's build
   logic (fetch, configure, compile, install) can be proven without
   paying that cost, and that is the verification this project relies on
   for `cjson`.

   `audio-zone-manager` itself has no `-native` variant (it depends on
   `systemd` unit installation and `/etc` config paths that are
   target-only concepts), so its build logic is verified by: (a) the
   `bitbake -p` parse above, (b) `cjson-native`'s real build proving the
   shared `cmake`+third-party-library pattern it reuses, and (c) a local,
   non-bitbake compile of `main.c` against the host's own `cjson`
   (documented separately) to confirm the C source itself is correct —
   not by a full `qemux86-64` target build, which this sandbox cannot
   complete.

## Why this is written down instead of left implicit

The standing rule for this whole portfolio of projects is: real
verification over untested claims, and genuine blockers get surfaced, not
quietly worked around. A reader of this project should be able to tell,
recipe by recipe, exactly what was proven by a real bitbake run versus
what was proven by other real means and why — rather than assuming
either "it builds" (unverified) or "it's fake" (untrue — the native build
and the parse are both real).

## Real functional verification of `audio-zone-manager`'s own C source

Since a full `qemux86-64` bitbake build of the service itself isn't
possible here (see above), its actual C logic was verified the direct
way: compiled and run for real.

1. `gcc -Wall -Wextra -std=c11 main.c -lcjson` against the host's own
   `libcjson-dev` package. First attempt failed to compile — two real
   bugs caught by the compiler: `errno` used without `#include <errno.h>`,
   and `strcasecmp` used without `#include <strings.h>` (an implicit-
   declaration warning that is a hard error on stricter toolchains). Both
   fixed in `files/main.c`; the rebuild compiled cleanly with zero
   warnings.
2. The resulting binary was actually run against the real
   `files/zones.json` sample config (loaded via real `cJSON_Parse`, not
   a stub), and its Unix-domain-socket control protocol was exercised
   live over `/tmp/audio-zone-manager.sock`:
   - `GET front` → `OK front volume=60 muted=0` (the config's
     `default_volume`, correctly loaded)
   - `SET rear 75` → `OK rear volume=75 muted=0` (state mutated)
   - `LIST` → `OK front=60/0 rear=75/0 trunk=0/1` (mutation persisted and
     visible zone-wide; `trunk`'s `muted: true` from the config correctly
     reflected)

This is the same class of real verification as `cjson-native`'s bitbake
build above — it just uses the host toolchain directly, since that is
the fastest real way to prove the C source and its cJSON usage are
correct, independent of whether a full Yocto target build can complete
in this sandbox.

## Real GitLab CI verification via `gitlab-ci-local`

Per explicit choice (no GitLab.com project for this one -- verify the
pipeline locally against real `.gitlab-ci.yml` semantics instead), the
pipeline was run for real with `gitlab-ci-local` (npm package, uses
Docker to actually execute each job exactly as GitLab Runner would).

**What ran, and passed, for real, inside real Docker containers
(`python:3.12-slim`):**
- `lint-recipes` -- the real `ci/check_recipes.py` structural check
  against the real `meta-infotainment` layer: passed.
- `lint-triage-tool` -- real `flake8` run against the real triage tool
  source: passed (after a real fix, see below).
- `test-triage-tool` -- the real 9-test `pytest` suite from the triage
  tool, run inside the container: all 9 passed.

**A real environment issue hit and fixed along the way**: `pip install`
inside the container initially failed with a TLS error
(`certificate verify failed: self-signed certificate in certificate
chain`) -- this sandbox's own outbound-HTTPS path runs through a local
agent-proxy on the host's loopback, which a Docker container's default
network cannot reach. Fixed for the verification run by passing
`--network host` (so the container's `127.0.0.1` resolves to the
sandbox host, where the proxy actually listens) and the same
`HTTPS_PROXY`/`HTTP_PROXY` values this shell already uses, as
`gitlab-ci-local --variable` flags. This is a property of this one
sandbox's network setup, not of the pipeline definition -- a real
GitLab Runner has no such proxy in the way, so `.gitlab-ci.yml` itself
is left as the plain, portable job definition; the extra `--network`/
`--variable` flags are a verification-time detail of this sandbox,
recorded here rather than baked into the committed pipeline.

**The `yocto-parse` and `yocto-build-cjson-native` jobs were not run**
through `gitlab-ci-local` (they are `when: manual`, stated in
`.gitlab-ci.yml` itself): running them would mean cloning Poky and
rebuilding the native toolchain bootstrap a second time inside a fresh
container, on top of the ~16GB this sandbox's disk has already spent
proving those exact same steps once, directly, earlier in this project.
Their script bodies are the identical command sequences already run
directly and verified (`bitbake -p`; `bitbake cjson-native`) -- they are
real, correct job definitions, not placeholders, but re-executing them
through Docker a second time in this disk-constrained sandbox was not
attempted, for the same disk-budget reason documented above.
