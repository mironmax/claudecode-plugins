"""CLI: python -m eval [--root DIR] [--since T] [--until T] [--variant NAME]... [--json]"""

import argparse
import json
import sys
from pathlib import Path

from core.constants import get_storage_root

from .data import parse_time
from .report import build_report, format_text
from .variants import REGISTRY


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m eval",
        description="Replay logged recall decisions against the graphs and score "
                    "retrieval by endorsements. Read-only on every input.")
    ap.add_argument("--root", type=Path, default=None,
                    help="storage root (default: KG_STORAGE_ROOT or ~/.knowledge-graph)")
    ap.add_argument("--no-root", action="store_true",
                    help="descriptive report from explicit log files only, no replay")
    ap.add_argument("--recall", type=Path, help="recall.jsonl to read instead of the root's")
    ap.add_argument("--useful", type=Path, help="useful.jsonl to read instead of the root's")
    ap.add_argument("--since", help="window start: epoch seconds or ISO date/time (UTC)")
    ap.add_argument("--until", help="window end, exclusive")
    ap.add_argument("--variant", action="append", default=[],
                    help=f"variant to replay besides the baseline; repeatable. "
                         f"Registered: {', '.join(sorted(REGISTRY))}; or module:function")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    root = None if args.no_root else (args.root or get_storage_root()).expanduser()
    if root is not None and not root.is_dir():
        print(f"storage root not found: {root}", file=sys.stderr)
        return 2
    try:
        rep = build_report(root, args.recall, args.useful,
                           parse_time(args.since), parse_time(args.until),
                           variants=args.variant)
    except (KeyError, ValueError, ImportError, AttributeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(rep, indent=1, default=str) if args.json else format_text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
