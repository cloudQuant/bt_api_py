#!/usr/bin/env python
from __future__ import annotations

import pathlib
import re

FORBIDDEN = re.compile(
    r"^\s*(from|import)\s+bt_api_(binance|okx|ctp|ib_web|mt5)\b",
    re.MULTILINE,
)
CORE_DIR = pathlib.Path("bt_api_py")


def main() -> int:
    failures: list[str] = []
    for py_file in CORE_DIR.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for match in FORBIDDEN.finditer(text):
            line_number = text[: match.start()].count("\n") + 1
            failures.append(f"{py_file}:{line_number}: {match.group().strip()}")

    if failures:
        print("Core package must not import plugin packages:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("core-plugin-isolation OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
