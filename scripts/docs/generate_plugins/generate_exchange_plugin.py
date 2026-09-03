#!/usr/bin/env python3
"""Retired entry point for the incomplete exchange-plugin scaffold generator."""

from __future__ import annotations

import sys


def main() -> int:
    """Fail closed until a reviewed scaffold matches the current plugin contract."""

    print(
        "This legacy plugin generator is retired because it was not runnable and "
        "cannot produce a verified current plugin contract. Start from "
        "_templates/README.md and submit the resulting plugin for review.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
