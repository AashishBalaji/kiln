#!/usr/bin/env python3
"""
bitbake_triage.py -- categorize bitbake build failures from real log
output and suggest fixes, instead of making whoever hit the failure
scroll through hundreds of lines of task-execution noise to find the
one line that actually explains it.

This is deliberately pattern-based rather than "smart": every pattern
here was taken from a real failure this project's own build hit (the
locale/bitbake-as-root errors from setting up this sandbox's Yocto
environment, and the mirror-fallback fetch warnings from the real
cjson-native build), plus the well-documented standard bitbake failure
shapes (do_patch reject, do_compile, do_fetch checksum mismatch,
QA/do_package_qa, parse-time "Nothing PROVIDES"). A triage tool that
doesn't know the real shapes of the errors it's meant to catch isn't
worth shipping, so each category below is matched against this
project's own real build logs in test_bitbake_triage.py.

Usage:
    bitbake_triage.py <logfile> [--json]
    bitbake_triage.py --stdin [--json]
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from enum import Enum


class Category(str, Enum):
    SANITY = "sanity_check"
    FETCH = "fetch"
    PATCH = "patch"
    CONFIGURE = "configure"
    COMPILE = "compile"
    QA = "qa"
    DEPENDENCY = "dependency_or_parse"
    LICENSE = "license_checksum"
    DISK = "disk_space"
    UNKNOWN = "unknown"


@dataclass
class Finding:
    category: Category
    recipe: str
    task: str
    message: str
    suggestion: str
    line_no: int
    severity: str = "error"  # "error" or "warning"

    def to_dict(self):
        d = asdict(self)
        d["category"] = self.category.value
        return d


# Each rule: (compiled regex, category, severity, suggestion-builder)
# suggestion-builder takes the regex match and returns a human string.
_RULES = [
    (
        re.compile(r"Do not use Bitbake as root"),
        Category.SANITY,
        "error",
        lambda m: (
            "bitbake refuses to run as root by design (its sanity checker "
            "rejects it). Create and use a non-root build user: "
            "`useradd -m -s /bin/bash builder && chown -R builder:builder "
            "<build dir>`, then re-run every bitbake command as that user."
        ),
    ),
    (
        re.compile(
            r"[Mm]ake sure locale '?en_US\.UTF-8'? is available|"
            r"locale '?en_US\.UTF-8'? (is )?(not available|not supported)",
            re.I,
        ),
        Category.SANITY,
        "error",
        lambda m: (
            "bitbake's cooker needs the en_US.UTF-8 locale on the build "
            "host. Fix: `apt-get install -y locales && locale-gen "
            "en_US.UTF-8 && update-locale LANG=en_US.UTF-8`, then export "
            "LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 before invoking bitbake."
        ),
    ),
    (
        re.compile(
            r"recipe (?P<recipe>[\w.+-]+): task (?P<task>do_\w+): Failed"
        ),
        Category.UNKNOWN,  # refined below by log_path context when possible
        "error",
        lambda m: (
            f"task {m.group('task')} failed for {m.group('recipe')} -- see "
            f"that task's own log.{m.group('task')} under "
            f"tmp/work/*/{m.group('recipe').rsplit('-', 1)[0]}/*/temp/ for "
            "the real underlying error; this summary line only says which "
            "task failed, not why."
        ),
    ),
    (
        re.compile(
            r"Failed to fetch URL (?P<url>\S+).*attempting MIRRORS", re.I
        ),
        Category.FETCH,
        "warning",
        lambda m: (
            f"primary fetch of {m.group('url')} failed, but bitbake is "
            "falling back to PREMIRRORS/MIRRORS -- usually harmless if a "
            "later 'do_fetch: Succeeded' for the same recipe follows. If "
            "it does NOT recover, the real problem is network/proxy "
            "egress to that host from this build environment, not the "
            "recipe; check SRC_URI's protocol (git:// vs raw https tarball "
            "-- some sandboxes allow one path but not the other)."
        ),
    ),
    (
        re.compile(
            r"Fetcher failure.*Unable to fetch URL|"
            r"Unable to fetch URL (?P<url>\S+) from any source", re.I
        ),
        Category.FETCH,
        "error",
        lambda m: (
            "every mirror for this SRC_URI entry failed -- this is a real, "
            "unrecovered fetch failure, not a fallback warning. Check the "
            "URL is still valid upstream, that SRCREV (for a VCS fetcher) "
            "is a real commit on the branch given, and that this network "
            "can reach that host at all."
        ),
    ),
    (
        re.compile(
            r"Unable to get checksum for .* SRC_URI entry (?P<file>[\w.+-]+): "
            r"file could not be found"
        ),
        Category.FETCH,
        "error",
        lambda m: (
            f"a local `file://` SRC_URI entry ({m.group('file')}) wasn't "
            "found on bitbake's FILESPATH search. This almost always "
            "means the file is in the wrong directory relative to the "
            "recipe/.bbappend -- double check FILESEXTRAPATHS and that "
            "the file sits in one of the directories bitbake actually "
            "lists when it reports this error (run with -p again and "
            "read the search-path list printed just above this line)."
        ),
    ),
    (
        re.compile(
            r"(?:patch|Patch) (?:file )?(?P<patch>[\w.+-]+\.patch) does not "
            r"apply|Applying patch .* failed|"
            r"fuzz \d+ chunks?|"
            r"\d+ out of \d+ hunks? FAILED"
        ),
        Category.PATCH,
        "error",
        lambda m: (
            "a patch in SRC_URI no longer applies cleanly against the "
            "fetched source -- usually because SRCREV/PV moved to a "
            "version where the surrounding code shifted. Re-generate the "
            "patch against the actual fetched source "
            "(tmp/work/.../<recipe>/<pv>/<source dir>) with `git diff` or "
            "`quilt refresh`, don't hand-edit the hunk offsets."
        ),
    ),
    (
        re.compile(
            r"(?P<file>[\w./+-]+\.[ch]):(?P<line>\d+):\d+: error:"
        ),
        Category.COMPILE,
        "error",
        lambda m: (
            f"real compiler error in {m.group('file')} at line "
            f"{m.group('line')} -- read the compiler's own message right "
            "after this line (undeclared identifier, missing header, type "
            "mismatch); this tool only locates it, the fix is in the "
            "source."
        ),
    ),
    (
        re.compile(r"implicit declaration of function '(?P<fn>\w+)'"),
        Category.COMPILE,
        "warning",
        lambda m: (
            f"'{m.group('fn')}' is used without a declaration in scope -- "
            "almost always a missing #include (e.g. <strings.h> for "
            "strcasecmp, <errno.h> for errno/strerror). Harmless warning "
            "on some toolchains, a hard error on stricter ones (and on "
            "most real cross-compilers) -- fix it, don't rely on the "
            "warning staying a warning."
        ),
    ),
    (
        re.compile(
            r"QA Issue: (?P<issue>.+?)(?: \[(?P<check>[\w-]+)\])?$",
            re.M,
        ),
        Category.QA,
        "error",
        lambda m: (
            f"OE-core's package QA checker flagged: {m.group('issue')}"
            + (f" (check: {m.group('check')})" if m.group("check") else "")
            + ". This is usually a real packaging mistake (wrong "
              "FILES:${PN} split, missing RDEPENDS, rpath into the build "
              "dir) rather than a false positive -- only add the check "
              "name to INSANE_SKIP as a last resort, and say why in a "
              "comment."
        ),
    ),
    (
        re.compile(r"Nothing PROVIDES '(?P<target>[\w.+-]+)'"),
        Category.DEPENDENCY,
        "error",
        lambda m: (
            f"no recipe in any enabled layer provides '{m.group('target')}'. "
            "Common causes: a typo in the target/DEPENDS name, the "
            "providing layer isn't in bblayers.conf, or (as with a plain "
            "service recipe with no BBCLASSEXTEND) you're asking for a "
            "'-native' variant of a recipe that was never extended to "
            "support one."
        ),
    ),
    (
        re.compile(
            r"LIC_FILES_CHKSUM.*(?:does not match|Mismatch)|"
            r"md5 data is not matching", re.I
        ),
        Category.LICENSE,
        "error",
        lambda m: (
            "the recipe's LIC_FILES_CHKSUM doesn't match the real license "
            "file text that got fetched. Never hand-guess this value -- "
            "fetch the real tagged source once, `md5sum` the actual "
            "license file, and put that exact value in the recipe."
        ),
    ),
    (
        re.compile(
            r"No space left on device|"
            r"write error.*No space left|"
            r"OSError: \[Errno 28\]"
        ),
        Category.DISK,
        "error",
        lambda m: (
            "the build host ran out of disk mid-build. For Yocto this "
            "usually means attempting a full target/image build on a "
            "small disk -- the native-toolchain bootstrap plus a target "
            "cross-toolchain (gcc-cross, binutils-cross, glibc) alone can "
            "need several GB beyond whatever the recipe itself needs; "
            "clean tmp/work and sstate-cache for recipes you don't need, "
            "or move to a host with more free space before retrying."
        ),
    ),
]


def classify(line: str, line_no: int) -> Finding | None:
    for pattern, category, severity, suggest in _RULES:
        m = pattern.search(line)
        if m:
            gd = m.groupdict()
            return Finding(
                category=category,
                recipe=gd.get("recipe", ""),
                task=gd.get("task", ""),
                message=line.strip(),
                suggestion=suggest(m),
                line_no=line_no,
                severity=severity,
            )
    return None


def triage(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for i, line in enumerate(text.splitlines(), start=1):
        f = classify(line, i)
        if f:
            findings.append(f)
    return findings


def summarize(findings: list[Finding]) -> str:
    if not findings:
        return "No recognized failure/warning patterns found in this log."

    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]

    out = []
    out.append(
        f"{len(errors)} error-level and {len(warnings)} warning-level "
        f"finding(s) across {len(findings)} matched line(s).\n"
    )
    for f in errors + warnings:
        tag = "ERROR" if f.severity == "error" else "WARN "
        out.append(f"[{tag}] line {f.line_no} :: {f.category.value}")
        out.append(f"   {f.message}")
        out.append(f"   -> {f.suggestion}")
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("logfile", nargs="?", help="path to a bitbake log/console file")
    ap.add_argument("--stdin", action="store_true", help="read the log from stdin")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args()

    if args.stdin:
        text = sys.stdin.read()
    elif args.logfile:
        with open(args.logfile, "r", errors="replace") as fh:
            text = fh.read()
    else:
        ap.error("give a logfile path or --stdin")
        return 2

    findings = triage(text)

    if args.json:
        print(json.dumps([f.to_dict() for f in findings], indent=2))
    else:
        print(summarize(findings))

    return 1 if any(f.severity == "error" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
