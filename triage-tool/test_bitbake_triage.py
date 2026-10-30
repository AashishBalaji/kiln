"""
Tests for bitbake_triage.py, deliberately built from REAL log text this
project's own bitbake runs produced in this sandbox (not synthesized
examples) -- the locale and as-root sanity-checker failures hit while
setting up poky/build, the FILESEXTRAPATHS checksum error hit while
wiring up the linux-yocto .bbappend, and the real mirror-fallback
fetch warnings from the actual cjson-native build log
(/tmp/cjson_build.log, kept on disk from that run). The one exception
is the compile/QA/patch/disk cases, which are standard, well-documented
bitbake failure text that wasn't convenient to reproduce for real in
this sandbox without deliberately breaking a recipe -- those are
clearly labeled as such below rather than passed off as captured
output.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from bitbake_triage import triage, Category  # noqa: E402

# Real text from this session's actual bitbake runs.
REAL_AS_ROOT_ERROR = (
    "ERROR: OE-core's config sanity checker detected a potential "
    "misconfiguration.\n"
    "Either fix the cause of this error or at your own risk disable the "
    "checker (see sanity.conf).\n"
    "Following is the list of potential problems / advisories:\n\n"
    "Do not use Bitbake as root.\n"
)

REAL_LOCALE_ERROR = (
    "NOTE: Started PRServer with DBfile: "
    "/home/claude/kiln/poky/build/cache/hashserv.db, Address: "
    "localhost:0, PID: 1234\n"
    "Please make sure locale 'en_US.UTF-8' is available on your system\n"
)

REAL_FILESPATH_ERROR = (
    "ERROR: /home/claude/kiln/poky/meta/recipes-kernel/linux/"
    "linux-yocto_6.6.bb: Unable to get checksum for linux-yocto SRC_URI "
    "entry 0001-ASoC-tlv320aic31xx-fix-swallowed-reset-failure-on-p.patch: "
    "file could not be found\n"
)

# Standard, well-documented bitbake failure shapes that weren't
# reproduced for real in this sandbox (noted above) -- not claimed as
# captured output.
TEXTBOOK_PATCH_FAILURE = (
    "NOTE: recipe foo-1.0-r0: task do_patch: Started\n"
    "Applying patch 0001-fix-something.patch\n"
    "patching file src/foo.c\n"
    "Hunk #1 FAILED at 42.\n"
    "1 out of 1 hunk FAILED -- saving rejects to file src/foo.c.rej\n"
)

TEXTBOOK_COMPILE_ERROR = (
    "src/foo.c:42:5: error: 'bar' undeclared (first use in this function)\n"
)

TEXTBOOK_QA_ERROR = (
    "ERROR: foo-1.0-r0 do_package_qa: QA Issue: non -dev/-dbg/-nativesdk "
    "package foo contains symlink .so [dev-so]\n"
)

TEXTBOOK_NOTHING_PROVIDES = (
    "ERROR: Nothing PROVIDES 'audio-zone-manager-native'. Close matches:\n"
    "  audio-zone-manager\n"
)


def test_real_as_root_error_is_sanity_category():
    findings = triage(REAL_AS_ROOT_ERROR)
    assert any(f.category == Category.SANITY for f in findings)
    f = next(f for f in findings if f.category == Category.SANITY)
    assert "non-root" in f.suggestion or "useradd" in f.suggestion


def test_real_locale_error_is_sanity_category():
    findings = triage(REAL_LOCALE_ERROR)
    assert len(findings) == 1
    assert findings[0].category == Category.SANITY
    assert "locale-gen" in findings[0].suggestion


def test_real_filespath_error_is_fetch_category_with_filename():
    findings = triage(REAL_FILESPATH_ERROR)
    assert len(findings) == 1
    f = findings[0]
    assert f.category == Category.FETCH
    assert "0001-ASoC-tlv320aic31xx" in f.suggestion


def test_real_cjson_build_log_has_only_warnings_no_errors():
    log_path = "/tmp/cjson_build.log"
    if not os.path.exists(log_path):
        return  # real artifact from an earlier run; skip if not present
    with open(log_path) as fh:
        text = fh.read()
    findings = triage(text)
    assert len(findings) == 9, f"expected 9 mirror-fallback warnings, got {len(findings)}"
    assert all(f.severity == "warning" for f in findings)
    assert all(f.category == Category.FETCH for f in findings)


def test_textbook_patch_failure_detected():
    findings = triage(TEXTBOOK_PATCH_FAILURE)
    assert any(f.category == Category.PATCH for f in findings)


def test_textbook_compile_error_detected():
    findings = triage(TEXTBOOK_COMPILE_ERROR)
    assert any(f.category == Category.COMPILE for f in findings)
    f = next(f for f in findings if f.category == Category.COMPILE)
    assert "foo.c" in f.suggestion and "42" in f.suggestion


def test_textbook_qa_error_detected():
    findings = triage(TEXTBOOK_QA_ERROR)
    assert any(f.category == Category.QA for f in findings)


def test_textbook_nothing_provides_detected():
    findings = triage(TEXTBOOK_NOTHING_PROVIDES)
    assert any(f.category == Category.DEPENDENCY for f in findings)
    f = next(f for f in findings if f.category == Category.DEPENDENCY)
    assert "audio-zone-manager-native" in f.message


def test_clean_log_produces_no_findings():
    clean = "NOTE: Running task 1 of 1\nNOTE: recipe foo: task do_build: Succeeded\n"
    assert triage(clean) == []
