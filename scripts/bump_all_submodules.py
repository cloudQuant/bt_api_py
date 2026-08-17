#!/usr/bin/env python3
"""bt_api 生态统一版本 bump 脚本（B-24）。

读母仓库 .gitmodules，逐仓 bump patch 版本（x.y.z -> x.y.z+1），
并 commit + tag。--dry-run 只打印 bump 清单（验收用）。

用法:
    python scripts/bump_all_submodules.py --dry-run
    python scripts/bump_all_submodules.py --apply
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GITMODULES = ROOT / ".gitmodules"

SUBMODULE_RE = re.compile(r'\[submodule "(?P<path>[^"]+)"\]')
PATH_RE = re.compile(r"path = (?P<path>\S+)")
VERSION_RE = re.compile(r'^version = "(\d+)\.(\d+)\.(\d+)"$', re.MULTILINE)


def list_submodule_paths() -> list[str]:
    """解析 .gitmodules，返回子模块 path 列表。"""
    text = GITMODULES.read_text(encoding="utf-8")
    return PATH_RE.findall(text)


def current_version(pyproject: Path) -> tuple[int, int, int] | None:
    """读取 pyproject.toml 的静态 version 字段。"""
    if not pyproject.exists():
        return None
    text = pyproject.read_text(encoding="utf-8")
    match = VERSION_RE.search(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bump_patch(version: tuple[int, int, int]) -> tuple[int, int, int]:
    """patch 版本 +1。"""
    major, minor, patch = version
    return major, minor, patch + 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印 bump 清单，不修改")
    args = parser.parse_args()

    paths = list_submodule_paths()
    plan: list[tuple[str, str, str]] = []  # (path, old, new)

    for path in paths:
        pyproject = ROOT / path / "pyproject.toml"
        version = current_version(pyproject)
        if version is None:
            print(f"[skip] {path}: 无静态 version 字段")
            continue
        old = ".".join(map(str, version))
        new = ".".join(map(str, bump_patch(version)))
        plan.append((path, old, new))

    print(f"共 {len(plan)} 个仓需要 bump（dry-run={args.dry_run}）：")
    for path, old, new in plan:
        print(f"  - {path}: {old} -> {new}")

    if args.dry_run or not plan:
        return

    for path, old, new in plan:
        pyproject = ROOT / path / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        updated, count = VERSION_RE.subn(f'version = "{new}"', text, count=1)
        if count != 1:
            print(f"[error] {path}: 替换失败")
            continue
        pyproject.write_text(updated, encoding="utf-8")
        subprocess.run(["git", "-C", str(ROOT / path), "add", "pyproject.toml"], check=True)
        subprocess.run(
            ["git", "-C", str(ROOT / path), "commit", "-m", f"chore: bump version to {new}"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(ROOT / path), "tag", f"v{new}"],
            check=True,
        )
        print(f"[bumped] {path}: {old} -> {new}")


if __name__ == "__main__":
    main()
