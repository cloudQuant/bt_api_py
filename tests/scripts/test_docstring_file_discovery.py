"""Offline contracts for the two docstring tools' file discovery helpers."""

from __future__ import annotations

import ast
import collections.abc
import importlib.util
import sys
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATHS = (
    ROOT / "scripts" / "analyze_docstrings.py",
    ROOT / "scripts" / "fill_missing_docstrings.py",
)
DEFAULT_EXCLUDED_DIRS = {
    "docs",
    "__pycache__",
    ".git",
    ".tox",
    "build",
    "dist",
    "egg-info",
    ".eggs",
    "venv",
    "env",
    "node_modules",
}

DEFAULT_INCLUDED = (
    ".hidden_dir/nested/inside.py",
    ".hidden_dir/visible.py",
    ".root_hidden.py",
    "alpha/.hidden.py",
    "alpha/a.py",
    "alpha/nested/nested.py",
    "custom_skip/custom.py",
    "root_z.py",
    "zeta/z.py",
)
CUSTOM_INCLUDED = (
    ".eggs/ignored.py",
    ".git/ignored.py",
    ".hidden_dir/nested/inside.py",
    ".hidden_dir/visible.py",
    ".root_hidden.py",
    ".tox/ignored.py",
    "__pycache__/ignored.py",
    "alpha/.hidden.py",
    "alpha/a.py",
    "alpha/nested/nested.py",
    "build/ignored.py",
    "dist/ignored.py",
    "docs/ignored.py",
    "egg-info/ignored.py",
    "env/ignored.py",
    "node_modules/ignored.py",
    "root_z.py",
    "venv/ignored.py",
    "zeta/z.py",
)


def _load_module(script_path: Path):
    module_name = f"docstring_tool_{script_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _populate_tree(root: Path) -> None:
    relative_paths = (
        "root_z.py",
        ".root_hidden.py",
        "zeta/z.py",
        "alpha/a.py",
        "alpha/.hidden.py",
        "alpha/nested/nested.py",
        "alpha/nested/generated.egg-info/ignored.py",
        ".hidden_dir/visible.py",
        ".hidden_dir/nested/inside.py",
        "custom_skip/custom.py",
        "docs/ignored.py",
        "__pycache__/ignored.py",
        ".git/ignored.py",
        ".tox/ignored.py",
        "build/ignored.py",
        "dist/ignored.py",
        "egg-info/ignored.py",
        ".eggs/ignored.py",
        "venv/ignored.py",
        "env/ignored.py",
        "node_modules/ignored.py",
        "package.egg-info/ignored.py",
    )
    for relative_path in relative_paths:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


@pytest.mark.parametrize("script_path", SCRIPT_PATHS, ids=("analyzer", "filler"))
@pytest.mark.parametrize(
    ("exclude_dirs", "expected_paths"),
    ((None, DEFAULT_INCLUDED), (["custom_skip"], CUSTOM_INCLUDED)),
    ids=("default-exclusions", "custom-exclusions"),
)
def test_find_python_files_preserves_exclusions_hidden_paths_and_sorted_output(
    tmp_path: Path,
    monkeypatch,
    script_path: Path,
    exclude_dirs: list[str] | None,
    expected_paths: tuple[str, ...],
) -> None:
    _populate_tree(tmp_path)
    module = _load_module(script_path)
    if script_path == SCRIPT_PATHS[1] and exclude_dirs is None:
        annotation = get_type_hints(module.find_python_files)["exclude_dirs"]
        iterable_annotation = next(
            item for item in get_args(annotation) if get_origin(item) is collections.abc.Iterable
        )
        assert get_args(iterable_annotation) == (str,)

    real_walk = module.os.walk
    visited_roots: list[Path] = []

    def deterministic_walk(root):
        for dirpath, dirnames, filenames in real_walk(root):
            dirnames.sort(reverse=True)
            filenames.sort(reverse=True)
            visited_roots.append(Path(dirpath))
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(module.os, "walk", deterministic_walk)

    actual = module.find_python_files(str(tmp_path), exclude_dirs=exclude_dirs)
    expected = sorted(str(tmp_path / relative_path) for relative_path in expected_paths)
    visited = {path.relative_to(tmp_path).as_posix() for path in visited_roots}

    assert actual == expected
    assert actual == sorted(actual)
    assert ".hidden_dir/visible.py" in {
        Path(path).relative_to(tmp_path).as_posix() for path in actual
    }
    assert ".hidden_dir" in visited
    assert ".hidden_dir/nested" in visited
    assert "package.egg-info" not in visited
    assert "alpha/nested/generated.egg-info" not in visited

    if exclude_dirs is None:
        assert DEFAULT_EXCLUDED_DIRS.isdisjoint(visited)
        assert "custom_skip" in visited
    else:
        assert "custom_skip" not in visited
        assert DEFAULT_EXCLUDED_DIRS - {".git", ".tox", ".eggs"} <= visited


def test_ast_collect_classifies_missing_docstrings_and_filters_private_names() -> None:
    module = _load_module(SCRIPT_PATHS[1])
    tree = ast.parse(
        """
class PublicClass:
    def public_method(self):
        pass

    def _private_method(self):
        pass

    def __str__(self):
        pass

    def __init__(self):
        pass

    def __new__(cls):
        pass

    def __call__(self):
        pass

    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_value, traceback):
        pass

def public_function():
    pass

def _private_function():
    pass

async def async_function():
    pass
"""
    )

    missing = module.ast_collect(tree)

    assert len(missing["module"]) == 1
    assert isinstance(missing["module"][0][0], ast.Module)
    assert [node.name for node, _ in missing["classes"]] == ["PublicClass"]
    assert [node.name for node, _ in missing["methods"]] == [
        "public_method",
        "__init__",
        "__new__",
        "__call__",
        "__enter__",
        "__exit__",
    ]
    assert [node.name for node, _ in missing["functions"]] == [
        "public_function",
        "async_function",
    ]


def test_apply_docstrings_rewrites_inline_class_and_function(tmp_path: Path) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    source = '"""Existing module docs."""\n\nclass InlineClass: pass\n\ndef inline_function(): return 1\n'
    path = tmp_path / "inline.py"
    path.write_text(source, encoding="utf-8")

    assert module.apply_docstrings(str(path)) is True

    updated = path.read_text(encoding="utf-8")
    tree = ast.parse(updated)
    inline_class = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    inline_function = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
    assert ast.get_docstring(inline_class) == "Class InlineClass"
    assert ast.get_docstring(inline_function) == "inline_function function"
    assert 'class InlineClass:\n    """Class InlineClass"""\n    pass' in updated
    assert 'def inline_function():\n    """inline_function function"""\n    return 1' in updated


@pytest.mark.parametrize(
    ("source", "definition_type", "expected_name", "expected_docstring"),
    (
        ("def run(): return 1\n", ast.FunctionDef, "run", "run function"),
        ("class InlineClass: pass\n", ast.ClassDef, "InlineClass", "Class InlineClass"),
    ),
    ids=("inline-function", "inline-class"),
)
def test_apply_docstrings_keeps_module_and_inline_definition_at_same_index(
    tmp_path: Path,
    source: str,
    definition_type,
    expected_name: str,
    expected_docstring: str,
) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    path = tmp_path / "same_index.py"
    path.write_text(source, encoding="utf-8")

    assert module.apply_docstrings(str(path)) is True

    updated = path.read_text(encoding="utf-8")
    tree = ast.parse(updated)
    definition = next(node for node in tree.body if isinstance(node, definition_type))
    assert updated.splitlines()[0] == '"""Module documentation"""'
    assert ast.get_docstring(tree) == "Module documentation"
    assert definition.name == expected_name
    assert ast.get_docstring(definition) == expected_docstring


@pytest.mark.parametrize(
    ("source", "definition_type", "expected_name", "expected_docstring"),
    (
        (
            'def run(\n    value,\n): return {"value": (lambda item: item)(value)}\n',
            ast.FunctionDef,
            "run",
            "run function",
        ),
        (
            'class Example(\n    metaclass=type("Meta", (), {"x": 1}),\n): pass\n',
            ast.ClassDef,
            "Example",
            "Class Example",
        ),
    ),
    ids=("multiline-function-header", "multiline-class-header"),
)
def test_apply_docstrings_rewrites_inline_suite_after_multiline_header(
    tmp_path: Path,
    source: str,
    definition_type,
    expected_name: str,
    expected_docstring: str,
) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    path = tmp_path / "multiline_header.py"
    path.write_text(source, encoding="utf-8")

    assert module.apply_docstrings(str(path)) is True

    updated = path.read_text(encoding="utf-8")
    tree = ast.parse(updated)
    definition = next(node for node in tree.body if isinstance(node, definition_type))
    assert ast.get_docstring(tree) == "Module documentation"
    assert definition.name == expected_name
    assert ast.get_docstring(definition) == expected_docstring


def test_apply_docstrings_preserves_nested_same_index_class_and_method_docs(
    tmp_path: Path,
) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    path = tmp_path / "nested_same_index.py"
    path.write_text("class Outer:\n    def inner(self): return 1\n", encoding="utf-8")

    assert module.apply_docstrings(str(path)) is True

    updated = path.read_text(encoding="utf-8")
    tree = ast.parse(updated)
    outer = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    inner = next(node for node in outer.body if isinstance(node, ast.FunctionDef))
    assert ast.get_docstring(tree) == "Module documentation"
    assert ast.get_docstring(outer) == "Class Outer"
    assert ast.get_docstring(inner) == "inner method"


def test_apply_docstrings_inserts_docstrings_before_multiline_bodies(tmp_path: Path) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    path = tmp_path / "multiline.py"
    path.write_text(
        '"""Existing module docs."""\n\n'
        "class Example:\n"
        "    def run(self):\n"
        '        return "ok"\n\n'
        "def process():\n"
        "    return True\n",
        encoding="utf-8",
    )

    assert module.apply_docstrings(str(path)) is True

    updated = path.read_text(encoding="utf-8")
    tree = ast.parse(updated)
    example = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    process = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
    run = next(node for node in example.body if isinstance(node, ast.FunctionDef))
    assert ast.get_docstring(example) == "Class Example"
    assert ast.get_docstring(run) == "run method"
    assert ast.get_docstring(process) == "process function"
    assert 'class Example:\n    """Class Example"""\n    def run(self):' in updated
    assert 'def run(self):\n        """run method"""\n        return' in updated


def test_apply_docstrings_preserves_tab_indentation_for_functions_and_methods(
    tmp_path: Path,
) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    source = (
        "def top_level():\n"
        "\treturn 1\n\n"
        "class Outer:\n"
        "\tdef nested(self):\n"
        "\t\treturn 2\n"
        "\tdef inline(self): return 3\n"
    )
    path = tmp_path / "tabs.py"
    path.write_text(source, encoding="utf-8")

    assert module.apply_docstrings(str(path)) is True

    updated = path.read_text(encoding="utf-8")
    tree = ast.parse(updated)
    top_level = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
    outer = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    nested = next(node for node in outer.body if isinstance(node, ast.FunctionDef))
    inline = next(
        node for node in outer.body if isinstance(node, ast.FunctionDef) and node.name == "inline"
    )
    assert ast.get_docstring(top_level) == "top_level function"
    assert ast.get_docstring(outer) == "Class Outer"
    assert ast.get_docstring(nested) == "nested method"
    assert ast.get_docstring(inline) == "inline method"
    assert '\n\t"""Class Outer"""\n' in updated
    assert '\n\t\t"""nested method"""\n' in updated


def test_apply_docstrings_writes_async_function_and_method_docstrings(tmp_path: Path) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    path = tmp_path / "async_defs.py"
    path.write_text(
        "async def async_top_level(): return 1\n\n"
        "class AsyncContainer:\n"
        "    async def async_method(self):\n"
        "        return 2\n",
        encoding="utf-8",
    )

    assert module.apply_docstrings(str(path)) is True

    updated = path.read_text(encoding="utf-8")
    tree = ast.parse(updated)
    async_top_level = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef))
    async_container = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    async_method = next(
        node for node in async_container.body if isinstance(node, ast.AsyncFunctionDef)
    )
    assert ast.get_docstring(tree) == "Module documentation"
    assert ast.get_docstring(async_top_level) == "async_top_level function"
    assert ast.get_docstring(async_container) == "Class AsyncContainer"
    assert ast.get_docstring(async_method) == "async_method method"


@pytest.mark.parametrize(
    "encoding_cookie",
    ("# -*- coding: utf-8 -*-", "# coding=utf-8"),
    ids=("colon-cookie", "equals-cookie"),
)
def test_apply_docstrings_places_module_docstring_after_file_headers(
    tmp_path: Path, encoding_cookie: str
) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    path = tmp_path / "headers.py"
    path.write_text(
        f"#!/usr/bin/env python3\n{encoding_cookie}\ndef run():\n    return True\n",
        encoding="utf-8",
    )

    assert module.apply_docstrings(str(path)) is True

    updated = path.read_text(encoding="utf-8")
    lines = updated.splitlines()
    assert lines[:3] == [
        "#!/usr/bin/env python3",
        encoding_cookie,
        '"""Module documentation"""',
    ]
    tree = ast.parse(updated)
    run = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
    assert ast.get_docstring(tree) == "Module documentation"
    assert ast.get_docstring(run) == "run function"


def test_apply_docstrings_keeps_second_line_cookie_after_ordinary_comment(
    tmp_path: Path,
) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    path = tmp_path / "ordinary_comment_header.py"
    path.write_text(
        "# Copyright 2026 Example\n# coding=utf-8\ndef run():\n    return True\n",
        encoding="utf-8",
    )

    assert module.apply_docstrings(str(path)) is True

    updated = path.read_text(encoding="utf-8")
    assert updated.splitlines()[:3] == [
        "# Copyright 2026 Example",
        "# coding=utf-8",
        '"""Module documentation"""',
    ]
    tree = ast.parse(updated)
    assert ast.get_docstring(tree) == "Module documentation"


def test_apply_docstrings_keeps_first_line_cookie_before_module_docstring(
    tmp_path: Path,
) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    path = tmp_path / "first_line_cookie.py"
    path.write_text("# coding=utf-8\ndef run():\n    return True\n", encoding="utf-8")

    assert module.apply_docstrings(str(path)) is True

    updated = path.read_text(encoding="utf-8")
    assert updated.splitlines()[:2] == ["# coding=utf-8", '"""Module documentation"""']
    assert ast.get_docstring(ast.parse(updated)) == "Module documentation"


def test_apply_docstrings_dry_run_reports_change_without_writing(tmp_path: Path) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    source = '"""Existing module docs."""\n\ndef run():\n    return True\n'
    path = tmp_path / "dry_run.py"
    path.write_text(source, encoding="utf-8")

    assert module.apply_docstrings(str(path), dry_run=True) is True
    assert path.read_text(encoding="utf-8") == source


def test_apply_docstrings_writes_parseable_docstrings_and_reports_change(
    tmp_path: Path,
) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    path = tmp_path / "write.py"
    path.write_text("def run():\n    return True\n", encoding="utf-8")

    assert module.apply_docstrings(str(path)) is True

    updated = path.read_text(encoding="utf-8")
    tree = ast.parse(updated)
    run = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
    assert ast.get_docstring(tree) == "Module documentation"
    assert ast.get_docstring(run) == "run function"


def test_apply_docstrings_returns_false_without_writing_for_syntax_error(
    tmp_path: Path,
) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    source = "def invalid(:\n    pass\n"
    path = tmp_path / "syntax_error.py"
    path.write_text(source, encoding="utf-8")

    assert module.apply_docstrings(str(path)) is False
    assert path.read_text(encoding="utf-8") == source


def test_apply_docstrings_returns_false_without_writing_when_nothing_is_missing(
    tmp_path: Path,
) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    source = (
        '"""Module docs."""\n\n'
        "class Example:\n"
        '    """Class docs."""\n\n'
        "    def run(self):\n"
        '        """Method docs."""\n'
        "        return True\n\n"
        "def process():\n"
        '    """Function docs."""\n'
        "    return True\n"
    )
    path = tmp_path / "complete.py"
    path.write_text(source, encoding="utf-8")

    assert module.apply_docstrings(str(path)) is False
    assert path.read_text(encoding="utf-8") == source


def test_apply_docstrings_does_not_write_when_generated_output_validation_fails(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_module(SCRIPT_PATHS[1])
    source = "def run():\n    return True\n"
    path = tmp_path / "rejected_output.py"
    path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(module, "_generated_source_is_valid", lambda _source: False)

    assert module.apply_docstrings(str(path)) is False
    assert path.read_text(encoding="utf-8") == source
