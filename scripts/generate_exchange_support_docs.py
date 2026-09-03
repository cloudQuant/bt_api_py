#!/usr/bin/env python3
"""Generate conservative support summaries from evidence-driven metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "docs" / "data" / "exchange_support_matrix.json"
TARGETS = (
    (ROOT / "README.md", "EXCHANGE_SUPPORT_OVERVIEW"),
    (ROOT / "docs" / "index.md", "EXCHANGE_SUPPORT_OVERVIEW"),
    (ROOT / "docs" / "project-overview.md", "EXCHANGE_SUPPORT_OVERVIEW"),
)


def load_data(path: Path = DATA_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def render(data: dict[str, Any]) -> str:
    policy = dict(data["policy"])
    rows = []
    for entry in data["entries"]:
        rows.append(
            "| {name} | `{tier}` | {scope} | {limitations} |".format(
                name=entry["name"],
                tier=entry["tier"],
                scope=entry["scope"],
                limitations=entry["limitations"],
            )
        )
    return "\n".join(
        [
            "## Support status",
            "",
            "The entries below are evidence tiers, not a count of production-ready exchanges.",
            "",
            "| Scope | Tier | Evidence boundary | Current limitation |",
            "| --- | --- | --- | --- |",
            *rows,
            "",
            "Blocking CI supports Python "
            + ", ".join(f"`{version}`" for version in policy["blocking_python"])
            + "; Python "
            + ", ".join(f"`{version}`" for version in policy["canary_python"])
            + " is canary-only.",
            "",
            "See `docs/operations/support-status-policy.md` for the evidence and expiry rules.",
        ]
    )


def replace_marker_block(text: str, marker: str, body: str) -> str:
    begin = f"<!-- BEGIN GENERATED:{marker} -->"
    end = f"<!-- END GENERATED:{marker} -->"
    if begin not in text or end not in text:
        raise ValueError(f"Missing marker block {marker}")
    prefix, rest = text.split(begin, maxsplit=1)
    _old, suffix = rest.split(end, maxsplit=1)
    return f"{prefix}{begin}\n{body}\n{end}{suffix}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    body = render(load_data())
    dirty = False
    for path, marker in TARGETS:
        original = path.read_text(encoding="utf-8")
        updated = replace_marker_block(original, marker, body)
        if updated != original:
            dirty = True
            if not args.check:
                path.write_text(updated, encoding="utf-8")
    if args.check and dirty:
        print("Exchange support documentation is out of date.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
