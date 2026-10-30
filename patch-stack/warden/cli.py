"""
warden.cli -- command-line entry point.

    warden check --tag v6.6
    warden matrix --tags v5.15,v6.1,v6.6,v6.12,v6.17
    warden matrix --tags v5.15,v6.1 --json

Exit code is non-zero if any patch in the stack is "broken" on any
checked tag -- this is what a CI job gates on.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import load_series, check_tag, WardenError


def _print_report(report) -> None:
    status_word = "HEALTHY" if report.healthy else "DRIFTED"
    print(f"\n=== {report.tag}: {status_word} ===")
    for p in report.patches:
        marker = {"clean": "OK", "broken": "FAIL", "not_reached": "SKIP"}[p.status]
        print(f"  [{marker:4}] {p.file}")
        if p.detail:
            for line in p.detail.splitlines()[:4]:
                print(f"           {line}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="warden")
    parser.add_argument(
        "--series", default="series.yaml", type=Path,
        help="path to the series manifest (default: series.yaml)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")

    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="check the stack against one tag")
    p_check.add_argument("--tag", required=True)

    p_matrix = sub.add_parser("matrix", help="check the stack against several tags")
    p_matrix.add_argument("--tags", required=True, help="comma-separated git tags")

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

    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2))
    else:
        for report in reports:
            _print_report(report)
        print()
        healthy_count = sum(1 for r in reports if r.healthy)
        print(f"{healthy_count}/{len(reports)} tags healthy")

    return 1 if any_broken else 0


if __name__ == "__main__":
    sys.exit(main())
