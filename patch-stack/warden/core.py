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
                report.patches.append(
                    PatchResult(
                        file=patch_file, target=target, status="broken", detail=detail
                    )
                )
    return report
