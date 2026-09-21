"""Offline contracts for the code-quality checker's source traversal."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATHS = (
    ROOT / "scripts" / "check_code_quality.py",
    ROOT / "scripts" / "analysis" / "check_code_quality.py",
)
DEFAULT_EXCLUDED_DIRS = {"__pycache__", ".git", "node_modules", "venv", ".venv"}


def _load_module(script_path: Path):
    module_name = f"check_code_quality_{script_path.parent.name}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _populate_tree(root: Path) -> None:
    relative_paths = (
        ".root-hidden.py",
        "root_error.py",
        "root_z.py",
        "root_a.py",
        "z_dir/z.py",
        "z_dir/.nested-hidden.py",
        "z_dir/nested/z_nested.py",
        "a_dir/a.py",
        "a_dir/nested/a_nested.py",
        "custom_skip/custom.py",
        "__pycache__/ignored.py",
        ".git/ignored.py",
        "node_modules/ignored.py",
        "venv/ignored.py",
        ".venv/ignored.py",
        ".hidden_dir/hidden.py",
    )
    for relative_path in relative_paths:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def _install_deterministic_walk(module, monkeypatch, tmp_path: Path) -> list[Path]:
    real_walk = module.os.walk
    visited_roots: list[Path] = []

    def deterministic_walk(root: str):
        for current_root, dirs, files in real_walk(root):
            dirs.sort(reverse=True)
            files.sort(reverse=True)
            visited_roots.append(Path(current_root))
            yield current_root, dirs, files

    monkeypatch.setattr(module.os, "walk", deterministic_walk)
    return visited_roots


def _relative_paths(paths: list[Path], root: Path) -> list[str]:
    return [path.relative_to(root).as_posix() for path in paths]


@pytest.mark.parametrize("script_path", SCRIPT_PATHS, ids=("root-copy", "analysis-copy"))
def test_check_directory_preserves_default_pruning_order_and_error_containment(
    tmp_path: Path, monkeypatch, capsys, script_path: Path
) -> None:
    _populate_tree(tmp_path)
    module = _load_module(script_path)
    visited_roots = _install_deterministic_walk(module, monkeypatch, tmp_path)
    checker = module.CodeQualityChecker(verbose=True)
    checked_files: list[str] = []
    updated_files: list[str] = []

    expected_files = [
        "root_z.py",
        "root_error.py",
        "root_a.py",
        ".root-hidden.py",
        "z_dir/z.py",
        "z_dir/.nested-hidden.py",
        "z_dir/nested/z_nested.py",
        "custom_skip/custom.py",
        "a_dir/a.py",
        "a_dir/nested/a_nested.py",
    ]

    def check_file(filepath: str):
        relative_path = Path(filepath).relative_to(tmp_path).as_posix()
        checked_files.append(relative_path)
        if relative_path == "root_error.py":
            raise RuntimeError("intentional check failure")
        return module.FileInfo(filepath=filepath)

    def update_report(filepath: str, file_info) -> None:
        assert file_info.filepath == filepath
        updated_files.append(Path(filepath).relative_to(tmp_path).as_posix())
        checker.report.total_files += 1

    monkeypatch.setattr(checker, "check_file", check_file)
    monkeypatch.setattr(checker, "_update_report", update_report)

    report = checker.check_directory(str(tmp_path))

    assert report is checker.report
    assert report.total_files == len(expected_files) - 1
    assert checked_files == expected_files
    assert updated_files == [path for path in expected_files if path != "root_error.py"]
    assert _relative_paths(visited_roots, tmp_path) == [
        ".",
        "z_dir",
        "z_dir/nested",
        "custom_skip",
        "a_dir",
        "a_dir/nested",
    ]
    assert not any(root.name in DEFAULT_EXCLUDED_DIRS for root in visited_roots)
    assert ".hidden_dir" not in _relative_paths(visited_roots, tmp_path)
    assert capsys.readouterr().out.splitlines() == [
        f"Found {len(expected_files)} Python files to check",
        "",
        f"Error checking {tmp_path / 'root_error.py'}: intentional check failure",
    ]


@pytest.mark.parametrize("script_path", SCRIPT_PATHS, ids=("root-copy", "analysis-copy"))
def test_custom_exclusions_replace_defaults_but_hidden_dirs_stay_pruned(
    tmp_path: Path, monkeypatch, script_path: Path
) -> None:
    _populate_tree(tmp_path)
    module = _load_module(script_path)
    visited_roots = _install_deterministic_walk(module, monkeypatch, tmp_path)
    checker = module.CodeQualityChecker()
    checked_files: list[str] = []

    def check_file(filepath: str):
        checked_files.append(Path(filepath).relative_to(tmp_path).as_posix())
        return module.FileInfo(filepath=filepath)

    monkeypatch.setattr(checker, "check_file", check_file)
    monkeypatch.setattr(checker, "_update_report", lambda *_args: None)

    checker.check_directory(str(tmp_path), exclude_dirs=["custom_skip"])

    assert checked_files == [
        "root_z.py",
        "root_error.py",
        "root_a.py",
        ".root-hidden.py",
        "z_dir/z.py",
        "z_dir/.nested-hidden.py",
        "z_dir/nested/z_nested.py",
        "venv/ignored.py",
        "node_modules/ignored.py",
        "a_dir/a.py",
        "a_dir/nested/a_nested.py",
        "__pycache__/ignored.py",
    ]
    assert _relative_paths(visited_roots, tmp_path) == [
        ".",
        "z_dir",
        "z_dir/nested",
        "venv",
        "node_modules",
        "a_dir",
        "a_dir/nested",
        "__pycache__",
    ]
    assert "custom_skip/custom.py" not in checked_files
    assert not any(root.name.startswith(".") for root in visited_roots if root != tmp_path)


@pytest.mark.parametrize("script_path", SCRIPT_PATHS, ids=("root-copy", "analysis-copy"))
def test_check_directory_rejects_missing_input_before_scanning(
    script_path: Path, tmp_path: Path
) -> None:
    module = _load_module(script_path)
    checker = module.CodeQualityChecker()
    scan_calls: list[str] = []
    checker.check_file = lambda filepath: scan_calls.append(filepath)

    with pytest.raises(FileNotFoundError, match="Directory not found"):
        checker.check_directory(str(tmp_path / "missing"))

    assert scan_calls == []
