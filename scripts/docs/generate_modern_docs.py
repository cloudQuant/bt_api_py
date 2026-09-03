#!/usr/bin/env python3
"""Retired entry point for the pre-contract modern documentation generator."""

from __future__ import annotations

import sys


def main() -> int:
    """Explain the supported documentation path without producing stale content."""

    print(
        "This legacy generator is retired because it was not runnable and its output "
        "predated the current runtime/support contract. Use "
        "scripts/generate_exchange_support_docs.py for evidence-driven support docs.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
