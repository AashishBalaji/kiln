"""
warden.cli -- command-line entry point.

    warden check --tag v6.6
    warden matrix --tags v5.15,v6.1,v6.6,v6.12,v6.17
    warden matrix --tags v5.15,v6.1 --json

--series and --json belong to the subcommand, not the top level:
`warden matrix --tags v6.1 --json` works, `warden --json matrix
--tags v6.1` does not (argparse error, not silently ignored).

Exit code is non-zero if any patch in the stack is "broken" on any
checked tag -- this is what a CI job gates on.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import load_series, check_tag, WardenError
from .triage_bridge import classify_detail, available as triage_available


def _print_report(report) -> None:
    status_word = "HEALTHY" if report.healthy else "DRIFTED"
    print(f"\n=== {report.tag}: {status_word} ===")
    for p in report.patches:
        marker = {"clean": "OK", "broken": "FAIL", "not_reached": "SKIP"}[p.status]
        print(f"  [{marker:4}] {p.file}")
        if p.detail:
            for line in p.detail.splitlines()[:4]:
                print(f"           {line}")
            finding = classify_detail(p.detail)
            if finding:
                print(f"           [triage-tool: {finding['category']}]")
                print(f"           -> {finding['suggestion']}")
        if p.rebase_hint:
            print("           --- real upstream source at that line now ---")
            for line in p.rebase_hint.splitlines():
                print(f"           {line}")


def _add_common_args(p: argparse.ArgumentParser) -> None:
    # Defined on each subparser rather than the top-level parser: a
    # parent-parser option and a same-named subparser option share one
    # namespace slot, and the subparser re-applies its own default into
    # it after the parent already set it -- so `warden --json matrix
    # --tags v6.1` silently resets json back to False instead of
    # keeping the parent's True. Found by actually running it and
    # getting text output when JSON was asked for, not by reading the
    # argparse docs. Defining it only here, once per subcommand, avoids
    # the clash entirely and works with --json on either side of the
    # subcommand name.
    p.add_argument(
        "--series", default="series.yaml", type=Path,
        help="path to the series manifest (default: series.yaml)",
    )
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="warden")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="check the stack against one tag")
    p_check.add_argument("--tag", required=True)
    _add_common_args(p_check)

    p_matrix = sub.add_parser("matrix", help="check the stack against several tags")
    p_matrix.add_argument("--tags", required=True, help="comma-separated git tags")
    _add_common_args(p_matrix)

    args = parser.parse_args(argv)

    try:
        manifest = load_series(args.series)
    except (WardenError, OSError, Exception) as e:
        print(f"error: could not load series manifest {args.series}: {e}", file=sys.stderr)
        return 2

    series_dir = (args.series.parent / "patches").resolve()

    tags = [args.tag] if args.command == "check" else [
        t.strip() for t in args.tags.split(",") if t.strip()
    ]

    reports = []
    any_broken = False
    for tag in tags:
        try:
            report = check_tag(manifest, series_dir, tag)
        except WardenError as e:
            print(f"error checking tag {tag!r}: {e}", file=sys.stderr)
            any_broken = True
            continue
        reports.append(report)
        if not report.healthy:
            any_broken = True

    if not triage_available():
        print(
            "note: triage-tool not found next to this checkout -- "
            "drift findings won't include a triage suggestion",
            file=sys.stderr,
        )

    if args.json:
        dicts = [r.to_dict() for r in reports]
        for d in dicts:
            for p in d["patches"]:
                if p["detail"]:
                    finding = classify_detail(p["detail"])
                    if finding:
                        p["triage"] = finding
        print(json.dumps(dicts, indent=2))
    else:
        for report in reports:
            _print_report(report)
        print()
        healthy_count = sum(1 for r in reports if r.healthy)
        print(f"{healthy_count}/{len(reports)} tags healthy")

    return 1 if any_broken else 0


if __name__ == "__main__":
    sys.exit(main())
