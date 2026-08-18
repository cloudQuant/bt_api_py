#!/usr/bin/env python3
"""Verify an exchange bundle install status (read-only).

Usage:
    python scripts/verify_exchange_bundle.py --bundle core-reference
    python scripts/verify_exchange_bundle.py --bundle core-reference --require-loadable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Prefer the source-tree bt_api_py so this script sees unreleased modules even
# when the installed distribution is a stale copy install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bt_api_py._plugin_catalog import PluginCatalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify exchange bundle install status.")
    parser.add_argument("--bundle", default="core-reference", help="bundle name")
    parser.add_argument("--json", help="write machine-readable result to this path")
    parser.add_argument(
        "--require-loadable",
        action="store_true",
        help="exit non-zero if any venue is not installed and loadable",
    )
    args = parser.parse_args(argv)

    catalog = PluginCatalog()
    try:
        result = catalog.resolve_bundle(args.bundle)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.json:
        from pathlib import Path

        Path(args.json).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    not_loadable = [v for v in result["venues"] if v["status"] == "missing"]
    for venue in result["venues"]:
        print(f"[{venue['status']:>9}] {venue['exchange']} ({venue['package']})")

    if args.require_loadable and not_loadable:
        print(
            f"bundle {args.bundle} has {len(not_loadable)} missing venue(s)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
