#!/usr/bin/env python3
"""
A fast, no-bitbake-toolchain-needed structural check over every recipe,
.bbappend, and layer.conf in meta-infotainment. Real bitbake parsing
(`bitbake -p`) is the authoritative check and this project already runs
it directly (see docs/validation-notes.md) -- this script exists for
CI's `lint` stage, where cloning Poky and bootstrapping bitbake just to
catch a missing LICENSE field would be a disproportionate amount of
setup for what it catches.

Checks, per file:
  - every .bb recipe defines SUMMARY and LICENSE
  - every .bb recipe with LICENSE != "CLOSED" defines LIC_FILES_CHKSUM
  - every file://<name> referenced in SRC_URI actually exists on disk,
    resolved the same way bitbake would (recipe's own dir, or for a
    .bbappend, THISDIR/<bpn> per its own FILESEXTRAPATHS -- this script
    reads the .bbappend's FILESEXTRAPATHS line rather than assuming a
    fixed layout, since that assumption is exactly the bug this project
    hit for real while wiring up the kernel .bbappend)
  - layer.conf defines the required BBFILE_* variables

Exits non-zero (and prints what failed) if anything is wrong, same
convention as any other CI lint step.
"""
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LAYER_ROOT = REPO_ROOT / "meta-infotainment"

FILE_URI_RE = re.compile(r"file://([^\s;\"]+)")
FILESEXTRAPATHS_RE = re.compile(r'FILESEXTRAPATHS:prepend\s*:?=\s*"([^"]+)"')


def expand_thisdir(path_template: str, bbfile: pathlib.Path) -> pathlib.Path:
    """Resolve a bitbake-style '${THISDIR}/xxx' string relative to bbfile."""
    thisdir = bbfile.parent
    resolved = path_template.replace("${THISDIR}", str(thisdir))
    resolved = resolved.rstrip(":")
    return pathlib.Path(resolved)


SRC_URI_RE = re.compile(r'^SRC_URI(?::append)?\s*[+:]?=\s*"(.*?)"', re.M | re.S)


def check_recipe(bb_path: pathlib.Path) -> list[str]:
    errors = []
    text = bb_path.read_text()
    is_bbappend = bb_path.suffix == ".bbappend"

    # .bbappend files extend an existing recipe and legitimately don't
    # (re-)declare SUMMARY/LICENSE/LIC_FILES_CHKSUM of their own -- only
    # a real .bb recipe needs those.
    if not is_bbappend:
        if not re.search(r'^SUMMARY\s*=', text, re.M):
            errors.append(f"{bb_path}: missing SUMMARY")
        if not re.search(r'^LICENSE\s*=', text, re.M):
            errors.append(f"{bb_path}: missing LICENSE")
        else:
            lic_match = re.search(r'^LICENSE\s*=\s*"([^"]+)"', text, re.M)
            license_value = lic_match.group(1) if lic_match else ""
            if license_value != "CLOSED" and not re.search(
                r'^LIC_FILES_CHKSUM\s*=', text, re.M
            ):
                errors.append(
                    f"{bb_path}: LICENSE={license_value!r} but no LIC_FILES_CHKSUM"
                )

    # Resolve file:// SRC_URI entries the way bitbake would for this file.
    # Only look inside the actual SRC_URI assignment(s) -- a file:// in
    # LIC_FILES_CHKSUM resolves against ${COMMON_LICENSE_DIR} or ${S} at
    # build time, not against a local recipe file, and checking it the
    # same way as SRC_URI is a false positive (caught by running this
    # script for real against cjson_1.7.19.bb, which legitimately
    # references file://LICENSE and file://${COMMON_LICENSE_DIR}/MIT in
    # LIC_FILES_CHKSUM with neither existing as a local file).
    search_dirs = [
        bb_path.parent,
        bb_path.parent / bb_path.stem.split("_")[0],
        bb_path.parent / "files",
    ]
    m = FILESEXTRAPATHS_RE.search(text)
    if m:
        search_dirs.append(expand_thisdir(m.group(1), bb_path))

    src_uri_text = " ".join(SRC_URI_RE.findall(text))
    for local_file in FILE_URI_RE.findall(src_uri_text):
        if "${" in local_file:
            continue  # unresolved bitbake variable, not a plain local path
        found = any((d / local_file).is_file() for d in search_dirs)
        if not found:
            errors.append(
                f"{bb_path}: SRC_URI file://{local_file} not found in any "
                f"of {[str(d) for d in search_dirs]}"
            )
    return errors


def check_layer_conf(layer_conf: pathlib.Path) -> list[str]:
    errors = []
    text = layer_conf.read_text()
    for required in ("BBFILE_COLLECTIONS", "BBFILE_PATTERN", "BBFILE_PRIORITY"):
        if required not in text:
            errors.append(f"{layer_conf}: missing {required}")
    return errors


def main() -> int:
    if not LAYER_ROOT.is_dir():
        print(f"ERROR: layer root {LAYER_ROOT} not found", file=sys.stderr)
        return 2

    all_errors: list[str] = []

    layer_conf = LAYER_ROOT / "conf" / "layer.conf"
    if layer_conf.is_file():
        all_errors += check_layer_conf(layer_conf)
    else:
        all_errors.append(f"missing {layer_conf}")

    for bb_path in sorted(LAYER_ROOT.rglob("*.bb")) + sorted(
        LAYER_ROOT.rglob("*.bbappend")
    ):
        all_errors += check_recipe(bb_path)

    if all_errors:
        print(f"{len(all_errors)} recipe lint issue(s) found:\n")
        for e in all_errors:
            print(f"  - {e}")
        return 1

    print("All recipes/bbappends/layer.conf passed structural checks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
