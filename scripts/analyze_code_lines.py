#!/usr/bin/env python3
"""
代码行数分析脚本
分析项目中的代码行数，按文件类型和目录统计
"""

import os
from pathlib import Path
from collections import defaultdict
from typing import Dict, Tuple


def should_skip(path: Path) -> bool:
    """判断是否应该跳过该路径"""
    skip_dirs = {
        ".git",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
        ".venv",
        "venv",
        "env",
        ".env",
        "dist",
        "build",
        ".egg-info",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".agents",
        ".claude",
        ".cursor",
        ".gemini",
        ".windsurf",
        "_bmad",
        "_bmad-output",
        ".benchmarks",
        ".kiro",
    }

    # 检查路径的任何部分是否在跳过列表中
    for part in path.parts:
        if part in skip_dirs or part.startswith("."):
            return True
    return False


def count_lines(file_path: Path) -> Tuple[int, int, int]:
    """
    统计文件行数
    返回: (总行数, 代码行数, 空行数)
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            total = len(lines)
            blank = sum(1 for line in lines if line.strip() == "")
            code = total - blank
            return total, code, blank
    except Exception as e:
        print(f"警告: 无法读取文件 {file_path}: {e}")
        return 0, 0, 0


def analyze_project(root_dir: str = ".") -> Dict:
    """分析项目代码行数"""
    root_path = Path(root_dir).resolve()

    stats = {
        "by_extension": defaultdict(lambda: {"files": 0, "total": 0, "code": 0, "blank": 0}),
        "by_directory": defaultdict(lambda: {"files": 0, "total": 0, "code": 0, "blank": 0}),
        "total": {"files": 0, "total": 0, "code": 0, "blank": 0},
    }

    # 只统计这些代码文件类型
    code_extensions = {
        ".py",
        ".pyx",
        ".pxd",  # Python
        ".js",
        ".jsx",
        ".ts",
        ".tsx",  # JavaScript/TypeScript
        ".java",
        ".kt",  # Java/Kotlin
        ".c",
        ".cpp",
        ".cc",
        ".h",
        ".hpp",  # C/C++
        ".go",  # Go
        ".rs",  # Rust
        ".rb",  # Ruby
        ".php",  # PHP
        ".swift",  # Swift
        ".m",
        ".mm",  # Objective-C
        ".sh",
        ".bash",  # Shell
        ".sql",  # SQL
        ".yaml",
        ".yml",  # YAML
        ".json",  # JSON
        ".xml",  # XML
        ".md",  # Markdown
        ".txt",  # Text
    }

    for file_path in root_path.rglob("*"):
        if not file_path.is_file():
            continue

        if should_skip(file_path.relative_to(root_path)):
            continue

        ext = file_path.suffix.lower()
        if ext not in code_extensions:
            continue

        total, code, blank = count_lines(file_path)

        if total == 0:
            continue

        # 按扩展名统计
        stats["by_extension"][ext]["files"] += 1
        stats["by_extension"][ext]["total"] += total
        stats["by_extension"][ext]["code"] += code
        stats["by_extension"][ext]["blank"] += blank

        # 按目录统计
        rel_path = file_path.relative_to(root_path)
        if len(rel_path.parts) > 1:
            top_dir = rel_path.parts[0]
        else:
            top_dir = "(root)"

        stats["by_directory"][top_dir]["files"] += 1
        stats["by_directory"][top_dir]["total"] += total
        stats["by_directory"][top_dir]["code"] += code
        stats["by_directory"][top_dir]["blank"] += blank

        # 总计
        stats["total"]["files"] += 1
        stats["total"]["total"] += total
        stats["total"]["code"] += code
        stats["total"]["blank"] += blank

    return stats


def print_stats(stats: Dict):
    """打印统计结果"""
    print("=" * 80)
    print("代码行数统计报告")
    print("=" * 80)

    # 总计
    print("\n【总计】")
    print(f"文件数: {stats['total']['files']:,}")
    print(f"总行数: {stats['total']['total']:,}")
    print(f"代码行: {stats['total']['code']:,}")
    print(f"空白行: {stats['total']['blank']:,}")

    # 按文件类型统计
    print("\n【按文件类型统计】")
    print(f"{'类型':<10} {'文件数':>8} {'总行数':>12} {'代码行':>12} {'空白行':>12}")
    print("-" * 80)

    sorted_ext = sorted(stats["by_extension"].items(), key=lambda x: x[1]["code"], reverse=True)

    for ext, data in sorted_ext:
        print(
            f"{ext:<10} {data['files']:>8,} {data['total']:>12,} "
            f"{data['code']:>12,} {data['blank']:>12,}"
        )

    # 按目录统计
    print("\n【按目录统计】")
    print(f"{'目录':<30} {'文件数':>8} {'总行数':>12} {'代码行':>12} {'空白行':>12}")
    print("-" * 80)

    sorted_dir = sorted(stats["by_directory"].items(), key=lambda x: x[1]["code"], reverse=True)

    for dir_name, data in sorted_dir:
        print(
            f"{dir_name:<30} {data['files']:>8,} {data['total']:>12,} "
            f"{data['code']:>12,} {data['blank']:>12,}"
        )

    print("=" * 80)


if __name__ == "__main__":
    import sys

    root = sys.argv[1] if len(sys.argv) > 1 else "."
    print(f"正在分析目录: {Path(root).resolve()}\n")

    stats = analyze_project(root)
    print_stats(stats)
