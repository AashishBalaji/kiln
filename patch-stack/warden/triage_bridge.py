"""
warden.triage_bridge -- route a real `git apply` failure through the
project's own triage-tool, instead of the two tools reporting
"something went wrong" in two different, unrelated shapes.

A patch-stack `PatchResult.detail` on a "broken" status IS, verbatim,
real `git apply --check` stderr -- the same kind of text
triage-tool/bitbake_triage.py already classifies (it has its own
`patch_stack_drift` category for exactly this format, added once this
project's stack genuinely drifted against a real upstream tag -- see
docs/validation-notes.md). Rather than duplicate that classification
logic here, this module imports the real triage tool and runs it on
the real detail text.

This is deliberately best-effort: the triage tool lives one directory
over (`../triage-tool`), found relative to this file so it works
whether warden.cli is run from `patch-stack/` or installed elsewhere
in the repo checkout. If it can't be found or imported, callers get
`None` back and carry on without triage output rather than failing --
the drift check itself doesn't depend on this.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_TRIAGE_TOOL_DIR = Path(__file__).resolve().parents[2] / "triage-tool"

_classify = None
if _TRIAGE_TOOL_DIR.is_dir():
    _dir_str = str(_TRIAGE_TOOL_DIR)
    if _dir_str not in sys.path:
        sys.path.insert(0, _dir_str)
    try:
        from bitbake_triage import classify as _classify  # noqa: E402
    except ImportError:
        _classify = None


def available() -> bool:
    return _classify is not None


def classify_detail(detail: str) -> Optional[dict]:
    """Run the real triage tool's classifier on a real git-apply error.

    Returns the matching Finding as a dict, or None if triage-tool
    isn't importable or found nothing to classify in `detail`.
    """
    if _classify is None or not detail:
        return None
    for line_no, line in enumerate(detail.splitlines(), start=1):
        finding = _classify(line, line_no)
        if finding is not None:
            return finding.to_dict()
    return None
