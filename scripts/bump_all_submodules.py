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
import shutil
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


def submodule_directory(path_text: str) -> Path:
    """Resolve a configured submodule path without allowing it to escape ROOT."""
    root = ROOT.resolve()
    candidate = (root / path_text).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Submodule path escapes repository root: {path_text}") from error
    return candidate


def git_executable() -> str:
    """Return the resolved Git executable or fail before executing a subprocess."""
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("Git executable was not found on PATH")
    return str(Path(executable).resolve())


def run_git(repository: Path, *arguments: str) -> None:
    """Run a fixed Git operation inside a validated submodule directory."""
    repository = submodule_directory(str(repository.relative_to(ROOT)))
    if not repository.is_dir():
        raise FileNotFoundError(f"Submodule source tree is unavailable: {repository}")
    subprocess.run(  # noqa: S603 - Git path, repository, and argument list are validated above.
        [git_executable(), "-C", str(repository), *arguments],
        check=True,
    )


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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只打印 bump 清单，不修改")
    mode.add_argument("--apply", action="store_true", help="确认后执行版本修改、提交和打标")
    args = parser.parse_args()

    paths = list_submodule_paths()
    plan: list[tuple[str, Path, str, str]] = []  # (path, repository, old, new)

    for path in paths:
        repository = submodule_directory(path)
        pyproject = repository / "pyproject.toml"
        version = current_version(pyproject)
        if version is None:
            print(f"[skip] {path}: 无静态 version 字段")
            continue
        old = ".".join(map(str, version))
        new = ".".join(map(str, bump_patch(version)))
        plan.append((path, repository, old, new))

    print(f"共 {len(plan)} 个仓需要 bump（dry-run={args.dry_run}）：")
    for path, _repository, old, new in plan:
        print(f"  - {path}: {old} -> {new}")

    if args.dry_run or not plan:
        return

    for path, repository, old, new in plan:
        pyproject = repository / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        updated, count = VERSION_RE.subn(f'version = "{new}"', text, count=1)
        if count != 1:
            print(f"[error] {path}: 替换失败")
            continue
        pyproject.write_text(updated, encoding="utf-8")
        run_git(repository, "add", "pyproject.toml")
        run_git(repository, "commit", "-m", f"chore: bump version to {new}")
        run_git(repository, "tag", f"v{new}")
        print(f"[bumped] {path}: {old} -> {new}")


if __name__ == "__main__":
    main()
