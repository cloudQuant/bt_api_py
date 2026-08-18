"""Exchange bundle diagnostic command (Task 2.1).

Usage:
    python -m bt_api_py.doctor --bundle core-reference
    python -m bt_api_py.doctor --bundle core-reference --format json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from bt_api_py._plugin_catalog import PluginCatalog


def _human_summary(result: dict[str, Any]) -> str:
    lines = [f"bundle: {result['name']} — {result['description']}"]
    for venue in result["venues"]:
        version = venue.get("version") or "n/a"
        version_flag = "ok" if venue["version_ok"] else "mismatch"
        lines.append(
            f"  [{venue['status']:>9}] {venue['exchange']:<16} "
            f"package={venue['package']:<16} v{version:<10} {version_flag} "
            f"cert={venue['certification']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose exchange bundle install status.")
    parser.add_argument("--bundle", default="core-reference", help="bundle name to diagnose")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="output format")
    args = parser.parse_args(argv)

    catalog = PluginCatalog()
    try:
        result = catalog.resolve_bundle(args.bundle)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(_human_summary(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
