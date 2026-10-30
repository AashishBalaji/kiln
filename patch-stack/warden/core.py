"""
warden.core -- patch-stack health checking against real upstream kernel tags.

Given a series.yaml manifest (an ordered stack of out-of-tree patches
against one or more upstream files) and a git tag/commit, this module:

  1. Does a minimal sparse clone of the upstream repo at that tag
     (only the paths the patches actually touch).
  2. Applies the patch stack in order with `git apply --check`,
     stopping at the first patch that fails (the same way `quilt
     push` would stop a series) -- later patches are reported
     "not reached" rather than tested against a file state that
     was never actually produced.
  3. Returns a structured per-tag, per-patch report.

This never fabricates a result: every status below comes from a real
`git apply --check` against a real, freshly fetched file at that
real tag. There is no simulated or hand-written "expected" output.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import yaml


class WardenError(RuntimeError):
    pass


@dataclass
class PatchResult:
    file: str
    target: str
    status: str  # "clean" | "broken" | "not_reached"
    detail: str = ""
    # Best-effort: the real upstream file's current content around the
    # hunk git apply refused, when `detail` names a line number. Not a
    # 3-way merge or a rebase suggestion -- see rebase_hint() below for
    # exactly what this is and isn't.
    rebase_hint: str = ""


@dataclass
class TagReport:
    tag: str
    patches: list = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return all(p.status == "clean" for p in self.patches)

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "healthy": self.healthy,
            "patches": [asdict(p) for p in self.patches],
        }


def load_series(series_path: Path) -> dict:
    with open(series_path) as f:
        manifest = yaml.safe_load(f)
    required = {"repo", "sparse_paths", "patches"}
    missing = required - manifest.keys()
    if missing:
        raise WardenError(f"series manifest missing keys: {missing}")
    return manifest


def _run(cmd: list, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=120
    )


def clone_tag(repo: str, tag: str, sparse_paths: list, workdir: Path) -> None:
    """Minimal sparse clone of `repo` at `tag` into `workdir`."""
    clone = _run(
        [
            "git", "clone", "--filter=blob:none", "--no-checkout",
            "--depth", "1", "--branch", tag, repo, str(workdir),
        ]
    )
    if clone.returncode != 0:
        raise WardenError(
            f"could not clone {repo} at tag {tag!r}: {clone.stderr.strip()}"
        )
    _run(["git", "sparse-checkout", "init", "--cone"], cwd=workdir)
    _run(["git", "sparse-checkout", "set", *sparse_paths], cwd=workdir)
    co = _run(["git", "checkout"], cwd=workdir)
    if co.returncode != 0:
        raise WardenError(f"checkout failed for tag {tag!r}: {co.stderr.strip()}")


def check_patch(workdir: Path, patch_path: Path) -> tuple:
    """Real `git apply --check`. Returns (clean: bool, detail: str)."""
    result = _run(["git", "apply", "--check", str(patch_path)], cwd=workdir)
    if result.returncode == 0:
        return True, ""
    return False, result.stderr.strip() or result.stdout.strip()


def _hunk_pre_image_lines(patch_text: str) -> list:
    """Extract, in order, a unified-diff patch's *pre-image* lines --
    the context (' ') and removed ('-') lines of its first hunk, i.e.
    exactly the text the hunk expects to find before it changes
    anything. This is a single-hunk, single-file heuristic (true for
    both of this project's own real patches); a multi-hunk patch only
    gets its first hunk's pre-image, which is still enough to locate
    roughly where that hunk used to live.
    """
    lines: list = []
    in_hunk = False
    for raw in patch_text.splitlines():
        if raw.startswith("@@"):
            if in_hunk:
                break  # only the first hunk
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if raw.startswith("+"):
            continue
        if raw.startswith("-") or raw.startswith(" "):
            lines.append(raw[1:])
        elif raw == "":
            lines.append("")
        else:
            break
    return lines


def rebase_hint(workdir: Path, target: str, patch_text: str, context: int = 4) -> str:
    """Best-effort: locate where the patch's own pre-image (its
    context + removed lines, i.e. what it expects the file to already
    say) actually sits in the real, current upstream file, and show
    that real neighborhood -- so a human has a head start on a manual
    rebase without opening the file themselves.

    Earlier versions of this function trusted the line number named
    in `git apply`'s error text (e.g. "patch failed: file.c:1661") and
    just printed lines around it. That number is the hunk's position
    in the OLD file the patch was written against, not necessarily
    where the matching content now sits in the file actually being
    checked -- if anything earlier in the file gained or lost lines
    between those two versions, the number is stale and the "hint"
    silently points at the wrong block. Caught for real against the
    v6.1 kernel tag: it printed the regmap-init block (lines
    1657-1665) instead of the regulator-supply error path the patch
    actually touches, because upstream code earlier in the same
    function had shifted. This version instead searches the real
    file's actual text for the patch's own pre-image lines, so the
    hint is tied to real content, not a borrowed line number.

    This is deliberately not a 3-way merge or an automatic fix: `git
    apply -3` needs real blob SHAs in the patch's `index` line to
    reconstruct a merge base, which a hand-written patch doesn't
    carry. When the pre-image can't be found verbatim at all, that is
    itself a real, useful finding -- it means upstream didn't just
    move the surrounding code, it rewrote the block the patch depends
    on -- so this returns an explicit statement of that rather than a
    guess.
    """
    target_path = workdir / target
    if not target_path.is_file():
        return ""
    pre_image = _hunk_pre_image_lines(patch_text)
    if not pre_image:
        return ""
    real_lines = target_path.read_text(errors="replace").splitlines()
    n = len(pre_image)
    matches = [
        i for i in range(len(real_lines) - n + 1)
        if real_lines[i:i + n] == pre_image
    ]
    if len(matches) == 1:
        start_idx = matches[0]
        lo = max(0, start_idx - context)
        hi = min(len(real_lines), start_idx + n + context)
        body = "\n".join(f"{i + 1:>5}  {real_lines[i]}" for i in range(lo, hi))
        return f"found the patch's old context intact at real line {start_idx + 1}:\n{body}"
    if len(matches) > 1:
        return (
            f"the patch's context lines appear verbatim {len(matches)} times in "
            "the current file -- ambiguous, not showing a guess"
        )
    return (
        "none of the patch's expected context lines were found verbatim in the "
        "current file -- upstream likely restructured this block (not just moved "
        "it), so a textual rebase hint isn't reliable here; a manual look at the "
        "target file is needed"
    )


def apply_patch(workdir: Path, patch_path: Path) -> None:
    result = _run(["git", "apply", str(patch_path)], cwd=workdir)
    if result.returncode != 0:
        raise WardenError(f"apply unexpectedly failed after check passed: {result.stderr}")


def check_tag(manifest: dict, series_dir: Path, tag: str) -> TagReport:
    """Run the full patch stack against one real upstream tag."""
    report = TagReport(tag=tag)
    with tempfile.TemporaryDirectory(prefix="warden-") as tmp:
        workdir = Path(tmp)
        clone_tag(manifest["repo"], tag, manifest["sparse_paths"], workdir)

        stack_broken = False
        for entry in manifest["patches"]:
            patch_file = entry["file"]
            target = entry["target"]
            patch_path = series_dir / patch_file

            if stack_broken:
                report.patches.append(
                    PatchResult(
                        file=patch_file, target=target, status="not_reached",
                        detail="a prior patch in the series failed to apply",
                    )
                )
                continue

            clean, detail = check_patch(workdir, patch_path)
            if clean:
                apply_patch(workdir, patch_path)
                report.patches.append(
                    PatchResult(file=patch_file, target=target, status="clean")
                )
            else:
                stack_broken = True
                hint = rebase_hint(workdir, target, patch_path.read_text(errors="replace"))
                report.patches.append(
                    PatchResult(
                        file=patch_file, target=target, status="broken",
                        detail=detail, rebase_hint=hint,
                    )
                )
    return report
