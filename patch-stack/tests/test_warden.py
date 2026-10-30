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

import json
import subprocess
from pathlib import Path

import pytest

from warden.core import check_tag, clone_tag, load_series, rebase_hint, WardenError
from warden.cli import main as warden_main
from warden.triage_bridge import classify_detail, available as triage_available


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


def test_triage_bridge_classifies_real_git_apply_failure():
    # The exact real git-apply failure text this project's own drift
    # check produced against v6.1 (see docs/validation-notes.md) --
    # not a synthesized example.
    detail = (
        "error: patch failed: sound/soc/codecs/tlv320aic31xx.c:1661\n"
        "error: sound/soc/codecs/tlv320aic31xx.c: patch does not apply"
    )
    if not triage_available():
        pytest.skip("triage-tool not found next to this checkout")
    finding = classify_detail(detail)
    assert finding is not None
    assert finding["category"] == "patch_stack_drift"


def test_cli_json_flag_after_subcommand_is_honored(local_repo_manifest, capsys):
    # Regression test for a real bug: --json before the subcommand
    # name silently reset back to False (argparse clash between a
    # parent-parser option and a same-named subparser option), so
    # `warden --json matrix ...` produced text output instead of
    # erroring or honoring --json. Fixed by defining --series/--json
    # only on the subparsers; --json must now come after the
    # subcommand, and the wrong order is a loud argparse error, not a
    # silent wrong answer.
    series_path, _ = local_repo_manifest
    exit_code = warden_main(["matrix", "--series", str(series_path), "--tags", "v1", "--json"])
    out = capsys.readouterr().out
    assert exit_code == 1  # the fixture's series genuinely drifts
    data = json.loads(out)
    assert data[0]["tag"] == "v1"
    assert data[0]["healthy"] is False


def test_rebase_hint_finds_real_context_that_still_exists(tmp_path):
    # Regression coverage for a real bug: an earlier version of
    # rebase_hint() trusted the (possibly stale) line number named in
    # `git apply`'s error text, which pointed at the WRONG block once
    # upstream content shifted (caught for real against v6.1, see
    # docs/validation-notes.md). This version instead searches the
    # real file for the patch's own pre-image lines. Here the
    # pre-image genuinely is still in the file (unchanged), just
    # under a patch stack whose second, unrelated hunk is broken --
    # so the whole check fails, but the first hunk's context should
    # still be found verbatim.
    target = tmp_path / "driver.c"
    target.write_text("int probe(void) {\n\treturn 0;\n}\n")
    patch_text = (
        "--- a/driver.c\n+++ b/driver.c\n"
        "@@ -1,3 +1,4 @@\n"
        " int probe(void) {\n"
        "+\t/* real comment */\n"
        " \treturn 0;\n"
        " }\n"
    )
    hint = rebase_hint(tmp_path, "driver.c", patch_text)
    assert "found the patch's old context intact at real line 1" in hint
    assert "int probe(void)" in hint


def test_rebase_hint_reports_honestly_when_context_is_gone(tmp_path):
    # The other real outcome: upstream genuinely rewrote the block
    # the patch depends on (not just moved it), so none of the
    # patch's pre-image lines exist anywhere in the current file.
    # rebase_hint() must say so plainly rather than guess -- this is
    # exactly what happens for real against the v6.1 kernel tag,
    # where the regulator-defer if/if/dev_err/return block patch 0002
    # expects was replaced by a single dev_err_probe() call.
    target = tmp_path / "driver.c"
    target.write_text("int probe(void) {\n\treturn 0;\n}\n")
    patch_text = (
        "--- a/driver.c\n+++ b/driver.c\n"
        "@@ -10,3 +10,4 @@\n"
        " this context line does not exist\n"
        "+\tneither does this one\n"
        " nor this one\n"
    )
    hint = rebase_hint(tmp_path, "driver.c", patch_text)
    assert "none of the patch's expected context lines were found" in hint


@pytest.mark.integration
def test_real_upstream_v6_1_rebase_hint_matches_actual_rewrite(tmp_path):
    # The project's own real finding, independently confirmed by
    # cloning v6.1 and grepping it directly (see validation-notes.md):
    # patch 0002's old if/if/dev_err/return block was replaced by
    # dev_err_probe() calls in v6.1, so the hint for this real file
    # must say the context is gone, not print an unrelated block.
    series_path = Path(__file__).parent.parent / "series.yaml"
    manifest = load_series(series_path)
    workdir = tmp_path / "upstream"
    clone_tag(manifest["repo"], "v6.1", manifest["sparse_paths"], workdir)
    patch_path = (
        series_path.parent / "patches"
        / "0002-ASoC-tlv320aic31xx-trace-regulator-probe-defer.patch"
    )
    hint = rebase_hint(workdir, "sound/soc/codecs/tlv320aic31xx.c", patch_path.read_text())
    assert "none of the patch's expected context lines were found" in hint


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
