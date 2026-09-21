#!/usr/bin/env python3
"""Fill missing docstrings for python files using AST-based insertion."""

from __future__ import annotations

import argparse
import ast
import io
import os
import re
import tokenize
from collections.abc import Iterable
from pathlib import Path
from typing import TypeAlias, TypedDict

FunctionNode: TypeAlias = ast.FunctionDef | ast.AsyncFunctionDef


class MissingDocstrings(TypedDict):
    module: list[tuple[ast.Module, int]]
    classes: list[tuple[ast.ClassDef, int]]
    methods: list[tuple[FunctionNode, int]]
    functions: list[tuple[FunctionNode, int]]


_CODING_COOKIE_RE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-_.a-zA-Z0-9]+", re.IGNORECASE)


def find_python_files(root_dir: str, exclude_dirs: Iterable[str] | None = None) -> list[str]:
    if exclude_dirs is None:
        exclude_dirs = {
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

    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs and not d.endswith(".egg-info")]
        files.extend(
            [os.path.join(dirpath, filename) for filename in filenames if filename.endswith(".py")]
        )

    return sorted(files)


def find_module_doc_insert_index(lines: list[str]) -> int:
    if lines and _CODING_COOKIE_RE.search(lines[0]):
        return 1
    if (
        len(lines) > 1
        and (not lines[0].strip() or lines[0].lstrip().startswith("#"))
        and _CODING_COOKIE_RE.search(lines[1])
    ):
        return 2
    if lines and lines[0].startswith("#!"):
        return 1
    return 0


def _line_indentation(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t\f"))]


def _find_definition_header_colons(source: str) -> dict[tuple[str, int], tuple[int, int]]:
    header_colons: dict[tuple[str, int], tuple[int, int]] = {}
    active_header: tuple[str, int] | None = None
    delimiter_depth = 0

    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if active_header is None:
            if token.type == tokenize.NAME and token.string in {"class", "def"}:
                active_header = (token.string, token.start[0])
                delimiter_depth = 0
            continue

        if token.type != tokenize.OP:
            continue
        if token.string in ("(", "[", "{"):
            delimiter_depth += 1
        elif token.string in ")]}":
            delimiter_depth -= 1
        elif token.string == ":" and delimiter_depth == 0:
            header_colons[active_header] = token.start
            active_header = None

    return header_colons


def _rewrite_inline_statement(line: str, colon_column: int, indent: str, text: str) -> str:
    source_line = line.rstrip("\r\n")
    prefix = source_line[: colon_column + 1]
    body = source_line[colon_column + 1 :].lstrip()
    doc = f'{indent}    """{text}"""\n'
    if body:
        return f"{prefix}\n{doc}{indent}    {body}\n"
    return f"{prefix}\n{doc}"


def build_docstring(indent: str, text: str) -> str:
    return f'{indent}"""{text}"""\n'


def ast_collect(tree: ast.Module) -> MissingDocstrings:
    """Return missing module, classes, methods and top-level functions."""

    important_dunders = {"__init__", "__new__", "__call__", "__enter__", "__exit__"}
    missing: MissingDocstrings = {
        "module": [],
        "classes": [],
        "methods": [],
        "functions": [],
    }

    if ast.get_docstring(tree) is None:
        missing["module"].append((tree, 1))

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if node.name and not node.name.startswith("_") and ast.get_docstring(node) is None:
                missing["classes"].append((node, node.lineno))
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and ((not item.name.startswith("_")) or item.name in important_dunders)
                    and ast.get_docstring(item) is None
                ):
                    missing["methods"].append((item, item.lineno))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.col_offset == 0:
            if not node.name.startswith("_") and ast.get_docstring(node) is None:
                missing["functions"].append((node, node.lineno))

    return missing


def _generated_source_is_valid(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return not any(ast_collect(tree).values())


def apply_docstrings(filepath: str, *, dry_run: bool = False) -> bool:
    path = Path(filepath)
    try:
        src = path.read_text(encoding="utf-8")
    except Exception:
        return False

    try:
        tree = ast.parse(src)
    except SyntaxError:
        print(f"Skip (syntax error): {filepath}")
        return False

    lines = src.splitlines(keepends=True)
    missing = ast_collect(tree)
    header_colons = _find_definition_header_colons(src)
    actions: list[tuple[int, str, str]] = []

    if missing["module"]:
        idx = find_module_doc_insert_index(lines)
        actions.append((idx, "insert", build_docstring("", "Module documentation")))

    for node, _ in missing["classes"]:
        if not node.body:
            continue
        first = node.body[0]
        definition_indent = _line_indentation(lines[node.lineno - 1])
        header_colon = header_colons.get(("class", node.lineno))
        if header_colon is not None and first.lineno == header_colon[0]:
            line_index = header_colon[0] - 1
            replacement = _rewrite_inline_statement(
                lines[line_index], header_colon[1], definition_indent, f"Class {node.name}"
            )
            actions.append((line_index, "replace", replacement))
            continue
        body_indent = _line_indentation(lines[first.lineno - 1])
        actions.append(
            (first.lineno - 1, "insert", build_docstring(body_indent, f"Class {node.name}"))
        )

    for node, _ in missing["methods"]:
        if not node.body:
            continue
        first = node.body[0]
        definition_indent = _line_indentation(lines[node.lineno - 1])
        header_colon = header_colons.get(("def", node.lineno))
        if header_colon is not None and first.lineno == header_colon[0]:
            line_index = header_colon[0] - 1
            replacement = _rewrite_inline_statement(
                lines[line_index], header_colon[1], definition_indent, f"{node.name} method"
            )
            actions.append((line_index, "replace", replacement))
            continue
        body_indent = _line_indentation(lines[first.lineno - 1])
        actions.append(
            (first.lineno - 1, "insert", build_docstring(body_indent, f"{node.name} method"))
        )

    for node, _ in missing["functions"]:
        if not node.body:
            continue
        first = node.body[0]
        definition_indent = _line_indentation(lines[node.lineno - 1])
        header_colon = header_colons.get(("def", node.lineno))
        if header_colon is not None and first.lineno == header_colon[0]:
            line_index = header_colon[0] - 1
            replacement = _rewrite_inline_statement(
                lines[line_index], header_colon[1], definition_indent, f"{node.name} function"
            )
            actions.append((line_index, "replace", replacement))
            continue
        body_indent = _line_indentation(lines[first.lineno - 1])
        actions.append(
            (
                first.lineno - 1,
                "insert",
                build_docstring(body_indent, f"{node.name} function"),
            )
        )

    if not actions:
        return False

    actions_by_index: dict[int, list[tuple[str, str]]] = {}
    for idx, mode, payload in actions:
        actions_by_index.setdefault(idx, []).append((mode, payload))

    for idx in sorted(actions_by_index, reverse=True):
        same_index_actions = actions_by_index[idx]

        # Apply replacements while the original source line is still at idx.
        for mode, payload in same_index_actions:
            if mode == "replace" and idx < len(lines):
                replacement_lines = payload.splitlines(keepends=True)
                if replacement_lines and not replacement_lines[-1].endswith("\n"):
                    replacement_lines[-1] += "\n"
                lines[idx : idx + 1] = replacement_lines

        # Insertions at the same index belong before the replaced/original line.
        # Reverse application keeps their original action order in the output.
        for mode, payload in reversed(same_index_actions):
            if mode == "insert":
                lines.insert(idx, payload)

    new_src = "".join(lines)
    if new_src != src:
        if not _generated_source_is_valid(new_src):
            print(f"Skip (generated source validation failed): {filepath}")
            return False
        if not dry_run:
            path.write_text(new_src, encoding="utf-8")
        return True

    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill missing docstrings in python files.")
    parser.add_argument("paths", nargs="*", default=["bt_api", "bt_api_py"])
    parser.add_argument("--dry-run", action="store_true", help="Do not write files, only report")
    args = parser.parse_args()

    if args.dry_run:
        print("Dry run mode: no files will be modified.")

    scanned = 0
    touched = 0
    for root in args.paths:
        for file_path in find_python_files(root):
            scanned += 1
            changed = apply_docstrings(file_path, dry_run=args.dry_run)
            if changed:
                touched += 1
                if args.dry_run:
                    print(f"Would update: {file_path}")

    print(f"Scanned {scanned} files, would modify {touched} files.")


if __name__ == "__main__":
    main()
