#!/usr/bin/env python3
"""扫描超过行数上限的源文件（F-11/B-18 巨型文件治理）。

用法:
    python scripts/check_file_sizes.py          # 仅报告
    python scripts/check_file_sizes.py --limit 800

退出码:
    0  所有源文件都在上限内
    1  存在超限源文件（供 CI 失败用）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_LIMIT = 800
SOURCE_ROOTS = ("bt_api_py", "bt_api")

# 编译产物目录（不纳入源文件行数治理）
EXCLUDED_DIRS = {"build", "dist", "__pycache__"}
# SWIG 自动生成代码（CTP 结构体），不可手工拆分，豁免
EXCLUDED_NAME_PREFIXES = ("ctp_structs_",)
EXCLUDED_NAMES = {"ctp_constants.py"}


def _iter_source_files(root: Path, root_name: str) -> list[Path]:
    base = root / root_name
    if not base.exists():
        return []
    files: list[Path] = []
    for py in base.rglob("*.py"):
        parts = set(py.parts)
        if parts & EXCLUDED_DIRS:
            continue
        if py.name.startswith(EXCLUDED_NAME_PREFIXES) or py.name in EXCLUDED_NAMES:
            continue
        # 测试文件不纳入源文件行数限制
        if "tests" in py.parts:
            continue
        files.append(py)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check oversized source files")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    oversized: list[tuple[int, Path]] = []
    for root_name in SOURCE_ROOTS:
        for py in _iter_source_files(root, root_name):
            lines = sum(1 for _ in py.open(encoding="utf-8"))
            if lines > args.limit:
                oversized.append((lines, py.relative_to(root)))

    if oversized:
        oversized.sort(reverse=True)
        print(f"Found {len(oversized)} source file(s) over {args.limit} lines:")
        for lines, path in oversized:
            print(f"  {lines:6d}  {path}")
        return 1

    print(f"All source files within {args.limit} lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
