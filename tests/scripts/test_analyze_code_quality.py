"""Offline contracts for Python source traversal in the quality analyzers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATHS = (
    ROOT / "scripts" / "analyze_code_quality.py",
    ROOT / "scripts" / "analysis" / "analyze_code_quality.py",
)
EXCLUDED_DIRS = {"__pycache__", ".git", "build", "dist", "htmlcov"}


def _load_module(script_path: Path):
    module_name = f"analyze_code_quality_{script_path.parent.name}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("script_path", SCRIPT_PATHS, ids=("root-copy", "analysis-copy"))
def test_find_python_files_preserves_walk_order_and_prunes_excluded_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script_path: Path
) -> None:
    tree_files = (
        ".root-hidden.py",
        "root-a.py",
        "root-z.py",
        "notes.txt",
        ".private/.hidden.py",
        ".private/private.py",
        "keep_a/.hidden.py",
        "keep_a/a.py",
        "keep_a/nested/nested.py",
        "keep_b/b.py",
        "__pycache__/ignored.py",
        ".git/ignored.py",
        "build/nested/ignored.py",
        "dist/ignored.py",
        "htmlcov/ignored.py",
    )
    for relative_path in tree_files:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    module = _load_module(script_path)
    real_walk = module.os.walk
    visited_roots: list[Path] = []

    def deterministic_walk(root: str):
        for current_root, dirs, files in real_walk(root):
            dirs.sort(reverse=True)
            files.sort(reverse=True)
            visited_roots.append(Path(current_root))
            yield current_root, dirs, files

    monkeypatch.setattr(module.os, "walk", deterministic_walk)

    actual = module.find_python_files(str(tmp_path))

    assert actual == [
        str(tmp_path / "root-z.py"),
        str(tmp_path / "root-a.py"),
        str(tmp_path / "keep_b" / "b.py"),
        str(tmp_path / "keep_a" / "a.py"),
        str(tmp_path / "keep_a" / "nested" / "nested.py"),
        str(tmp_path / ".private" / "private.py"),
    ]
    assert [
        "." if root == tmp_path else root.relative_to(tmp_path).as_posix() for root in visited_roots
    ] == [".", "keep_b", "keep_a", "keep_a/nested", ".private"]
    assert not any(root.name in EXCLUDED_DIRS for root in visited_roots)
