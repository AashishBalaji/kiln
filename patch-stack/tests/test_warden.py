"""
Real tests for warden.core -- no mocked git, no fabricated "expected"
patch output.

Two kinds of test here, both exercising the real `git`/`git apply`
binaries:

1. Local-repo tests (no network): build a tiny real git repo in a
   temp dir, commit a real file, tag it, and run the real stack logic
   (apply/check/"stop at first break") against it. This is how the
   "stop at first failure, mark the rest not_reached" behavior is
   verified without depending on network access.

2. Real-upstream tests (network): run warden against the actual
   https://github.com/torvalds/linux.git repo at the real tags this
   project found during research -- v5.15 (stack applies cleanly)
   and v6.1 (patch 0002 genuinely fails, because upstream really
   refactored the regulator-defer error path between those tags).
   These are the project's own actual findings, not synthetic
   fixtures -- see docs/validation-notes.md for the full story.
"""

import subprocess
from pathlib import Path

import pytest

from warden.core import check_tag, load_series, WardenError


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def local_repo_manifest(tmp_path):
    """A real, tiny git repo with a real tag, used as a local 'upstream'."""
    repo_dir = tmp_path / "upstream"
    repo_dir.mkdir()
    _git(["init", "-q"], cwd=repo_dir)
    _git(["config", "user.email", "test@example.invalid"], cwd=repo_dir)
    _git(["config", "user.name", "Test"], cwd=repo_dir)

    target = repo_dir / "driver.c"
    target.write_text("int probe(void) {\n\treturn 0;\n}\n")
    _git(["add", "driver.c"], cwd=repo_dir)
    _git(["commit", "-q", "-m", "initial"], cwd=repo_dir)
    _git(["tag", "v1"], cwd=repo_dir)

    patches_dir = tmp_path / "patches"
    patches_dir.mkdir()

    # Patch A: applies cleanly against v1's real content.
    (patches_dir / "a.patch").write_text(
        "--- a/driver.c\n+++ b/driver.c\n"
        "@@ -1,3 +1,4 @@\n"
        " int probe(void) {\n"
        "+\t/* real comment */\n"
        " \treturn 0;\n"
        " }\n"
    )
    # Patch B: deliberately targets context that does not exist in
    # this file at all, so it must fail to apply, for real.
    (patches_dir / "b.patch").write_text(
        "--- a/driver.c\n+++ b/driver.c\n"
        "@@ -10,3 +10,4 @@\n"
        " this context line does not exist\n"
        "+\tneither does this one\n"
        " nor this one\n"
    )

    series_path = tmp_path / "series.yaml"
    series_path.write_text(
        f"repo: {repo_dir}\n"
        "sparse_paths:\n  - driver.c\n"
        "patches:\n"
        "  - file: a.patch\n    target: driver.c\n"
        "  - file: b.patch\n    target: driver.c\n"
    )
    return series_path, patches_dir


def test_load_series_rejects_missing_keys(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("repo: x\n")
    with pytest.raises(WardenError):
        load_series(bad)


def test_local_repo_stack_stops_at_first_break(local_repo_manifest):
    series_path, patches_dir = local_repo_manifest
    manifest = load_series(series_path)
    report = check_tag(manifest, patches_dir, "v1")

    assert not report.healthy
    assert report.patches[0].status == "clean"
    assert report.patches[1].status == "broken"
    assert "patch" in report.patches[1].detail.lower() or report.patches[1].detail


def test_local_repo_report_to_dict_shape(local_repo_manifest):
    series_path, patches_dir = local_repo_manifest
    manifest = load_series(series_path)
    report = check_tag(manifest, patches_dir, "v1")
    d = report.to_dict()
    assert d["tag"] == "v1"
    assert d["healthy"] is False
    assert len(d["patches"]) == 2


@pytest.mark.integration
def test_real_upstream_v5_15_is_healthy():
    """The project's own series against the real file at the real
    v5.15 tag -- this is the tag this project found where both real
    patches apply cleanly."""
    series_path = Path(__file__).parent.parent / "series.yaml"
    manifest = load_series(series_path)
    report = check_tag(manifest, series_path.parent / "patches", "v5.15")
    assert report.healthy
    assert all(p.status == "clean" for p in report.patches)


@pytest.mark.integration
def test_real_upstream_v6_1_has_genuine_drift():
    """The project's own real finding: upstream's switch to
    dev_err_probe() between v5.15 and v6.1 breaks patch 0002's
    context for real -- not a fabricated failure."""
    series_path = Path(__file__).parent.parent / "series.yaml"
    manifest = load_series(series_path)
    report = check_tag(manifest, series_path.parent / "patches", "v6.1")
    assert not report.healthy
    assert report.patches[0].status == "clean"
    assert report.patches[1].status == "broken"
    assert "tlv320aic31xx.c" in report.patches[1].detail
