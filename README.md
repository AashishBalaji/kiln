# Kiln

An embedded-Linux build-engineering portfolio project, built against
HARMAN's **Associate Engineer – Gitlab CI/CD** job description
(R-56518-2026, Bangalore): Yocto Project layers/recipes, third-party
library integration, a real kernel patch stack with upstream-drift
detection, build-failure triage, GitLab CI/CD, and JFrog Artifactory.

This README states plainly what was verified for real and how, and
what wasn't (and why), rather than claiming a finished product. The
full account, including every real error hit and fixed along the way,
is in [`docs/validation-notes.md`](docs/validation-notes.md).

## What's here

```
meta-infotainment/        a real Yocto BSP/application layer
  conf/layer.conf
  recipes-support/cjson/            third-party library recipe (cJSON)
  recipes-apps/audio-zone-manager/  custom C service recipe, depends on cjson
  recipes-kernel/linux/             .bbappend: real kernel patch + config fragment

kernel-patch/              the kernel patch and config fragment, standalone,
                            plus the sparse kernel checkout used to write
                            and verify them

patch-stack/                a real patch-stack health checker: re-applies
                             this project's carried kernel patches against
                             5 real upstream kernel tags and reports, per
                             patch, exactly where upstream drift breaks it
  series.yaml                the ordered patch-stack manifest
  patches/                    the two real patches it carries
  warden/                     core.py (git clone + apply --check logic),
                               cli.py (`warden check` / `warden matrix`)
  tests/                      local-repo unit tests + real-upstream
                               integration tests (see validation-notes.md)

triage-tool/                bitbake_triage.py -- categorizes real bitbake
                             failures and suggests fixes, with a real test
                             suite built from this project's own failures

ci/                         ci/check_recipes.py -- the fast structural
                             lint CI runs before touching bitbake at all

artifactory/                README + notes on the real local JFrog
                             Artifactory + Postgres integration

.gitlab-ci.yml               the GitLab CI pipeline (verified for real
                              with gitlab-ci-local)
.github/workflows/ci.yml     GitHub Actions mirror of the same lint/test
                              jobs, for CI on this repo as hosted
```

## What was verified for real, and how

| Piece | How it was verified | Result |
|---|---|---|
| `layer.conf` + both recipes | real `bitbake -p` parse (922 `.bb` files) | 0 errors |
| `cjson` recipe's fetch/build logic | real `bitbake cjson-native` build (278 tasks) | succeeded; real `.so`/`.a`/header artifacts on disk |
| `audio-zone-manager`'s C source | compiled with `gcc -Wall -Wextra`, run for real against its sample config, exercised live over its Unix-socket protocol | 2 real bugs found and fixed (missing `<errno.h>`, `<strings.h>`); clean compile; correct `GET`/`SET`/`LIST` behavior |
| The kernel patch | `git apply --check` against a fresh sparse clone of the real upstream file (also re-run on every GitHub Actions run) | applies cleanly |
| Kconfig symbol names in the config fragment | `grep`'d against the real `Kconfig` files, not guessed | one guess (`SND_SOC_SIMPLE_CARD`) was wrong; corrected to the real `SND_SIMPLE_CARD` |
| `patch-stack`'s drift detection | real sparse clones of 5 real upstream kernel tags (`v5.15`–`v6.17`), real `git apply --check` of both carried patches against each | a genuine finding: 1/5 tags healthy -- patch 0002 breaks from v6.1 onward because upstream really refactored that error path |
| `bitbake_triage.py` | real `pytest` suite built from this project's own actual build failures (locale/as-root sanity errors, a `FILESEXTRAPATHS` lookup miss) plus textbook failure shapes | 9/9 passing; caught 2 real bugs in the tool itself along the way |
| `.gitlab-ci.yml` | run for real with `gitlab-ci-local` against real Docker containers | lint/test jobs passed; see notes for a real TLS/proxy issue hit and fixed |
| JFrog Artifactory integration | real local Artifactory OSS + Postgres containers; real upload/download round trip | byte-identical round trip, with Artifactory's own server-computed checksums |
| `.github/workflows/ci.yml` | pushed and run on GitHub Actions | all 4 jobs green |

## What wasn't attempted, and why

A full `bitbake` build for the real target machine (`qemux86-64`) was
**attempted for real**, not skipped outright: it resolved to 881 tasks
(vs. 278 for the native-only `cjson` build) because building for a real
target, for the first time in a session, requires bootstrapping an
entire cross-compilation toolchain (`gcc-cross`, `binutils-cross`,
`glibc`) before a single line of this project's own code compiles. It
was stopped partway through after disk headroom dropped from 9.4GB to
6.6GB free in a few minutes, to avoid exhausting this environment's
fixed, non-expandable disk allowance mid-build. The `-native` build
class extension exists precisely so a recipe's real build logic can be
proven without that cost, and that's what this project relies on for
`cjson`; `audio-zone-manager` has no native variant (it needs `systemd`
and `/etc` paths that only make sense for a real target), so its C
source was verified directly with the host compiler instead (see table
above). Full details, including the exact task counts and disk
readings, are in `docs/validation-notes.md`.

## Running it yourself

```bash
# Parse-check the layer (needs a Poky checkout; see docs/validation-notes.md)
bitbake -p

# Run the triage tool against a real log
python3 triage-tool/bitbake_triage.py path/to/bitbake.log

# Run its test suite
cd triage-tool && python3 -m pytest -v

# Run the fast recipe lint (no bitbake needed)
python3 ci/check_recipes.py

# Check the carried patch stack against a real upstream kernel tag
cd patch-stack && python3 -m warden.cli matrix --tags v5.15,v6.1,v6.6,v6.12,v6.17

# Run the GitLab CI pipeline locally (no GitLab.com account needed)
gitlab-ci-local lint-triage-tool lint-recipes lint-patch-stack \
  test-triage-tool test-patch-stack
```
