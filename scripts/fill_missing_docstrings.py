#!/usr/bin/env python3
"""Fill missing docstrings for python files using AST-based insertion."""

from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
from typing import Iterable


def find_python_files(root_dir: str, exclude_dirs: Iterable[str] | None = None) -> list[str]:
    if exclude_dirs is None:
        exclude_dirs = {
            'docs',
            '__pycache__',
            '.git',
            '.tox',
            'build',
            'dist',
            'egg-info',
            '.eggs',
            'venv',
            'env',
            'node_modules',
        }

    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs and not d.endswith('.egg-info')]
        for filename in filenames:
            if filename.endswith('.py'):
                files.append(os.path.join(dirpath, filename))

    return sorted(files)


def find_module_doc_insert_index(lines: list[str]) -> int:
    idx = 0
    if lines and lines[0].startswith('#!'):
        idx += 1
    if idx < len(lines) and 'coding:' in lines[idx].lower() and lines[idx].lstrip().startswith('#'):
        idx += 1
    return idx


def split_header_and_body(line: str) -> tuple[str, str] | None:
    stripped = line.rstrip('\n')
    in_single = False
    in_double = False
    escape = False
    paren = 0
    bracket = 0
    brace = 0

    for i, ch in enumerate(stripped):
        if escape:
            escape = False
            continue

        if ch == '\\':
            escape = True
            continue

        if in_single:
            if ch == "'":
                in_single = False
            continue

        if in_double:
            if ch == '"':
                in_double = False
            continue

        if ch == "'":
            in_single = True
            continue
        if ch == '"':
            in_double = True
            continue

        if ch == '#':
            return None

        if ch == '(':
            paren += 1
        elif ch == ')':
            paren = max(0, paren - 1)
        elif ch == '[':
            bracket += 1
        elif ch == ']':
            bracket = max(0, bracket - 1)
        elif ch == '{':
            brace += 1
        elif ch == '}':
            brace = max(0, brace - 1)

        if ch == ':' and paren == 0 and bracket == 0 and brace == 0:
            return stripped[: i + 1], stripped[i + 1 :].lstrip()

    return None


def rewrite_inline_statement(line: str, indent: str, text: str) -> str | None:
    parts = split_header_and_body(line)
    if not parts:
        return None
    prefix, body = parts
    doc = f'{indent}    """{text}"""\n'
    if body:
        return f'{prefix}\n{doc}{indent}    {body}\n'
    return f'{prefix}\n{doc}'


def build_docstring(indent: str, text: str) -> str:
    return f'{indent}"""{text}"""\n'


def ast_collect(tree: ast.AST) -> dict[str, list[tuple[ast.AST, int]]]:
    """Return missing module, classes, methods and top-level functions."""

    important_dunders = {'__init__', '__new__', '__call__', '__enter__', '__exit__'}
    missing = {
        'module': [],
        'classes': [],
        'methods': [],
        'functions': [],
    }

    if ast.get_docstring(tree) is None:
        missing['module'].append((tree, 1))

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if node.name and not node.name.startswith('_') and ast.get_docstring(node) is None:
                missing['classes'].append((node, node.lineno))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if (not item.name.startswith('_')) or item.name in important_dunders:
                        if ast.get_docstring(item) is None:
                            missing['methods'].append((item, item.lineno))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.col_offset == 0:
            if not node.name.startswith('_') and ast.get_docstring(node) is None:
                missing['functions'].append((node, node.lineno))

    return missing


def apply_docstrings(filepath: str, *, dry_run: bool = False) -> bool:
    path = Path(filepath)
    try:
        src = path.read_text(encoding='utf-8')
    except Exception:
        return False

    try:
        tree = ast.parse(src)
    except SyntaxError:
        print(f"Skip (syntax error): {filepath}")
        return False

    lines = src.splitlines(keepends=True)
    missing = ast_collect(tree)
    actions: list[tuple[int, str, str]] = []

    if missing['module']:
        idx = find_module_doc_insert_index(lines)
        actions.append((idx, 'insert', build_docstring('', 'Module documentation')))

    for node, _ in missing['classes']:
        if not node.body:
            continue
        first = node.body[0]
        indent = ' ' * node.col_offset
        if first.lineno == node.lineno:
            original = lines[first.lineno - 1]
            replacement = rewrite_inline_statement(original, indent, f'Class {node.name}')
            if replacement is not None:
                actions.append((first.lineno - 1, 'replace', replacement))
                continue
        actions.append((first.lineno - 1, 'insert', build_docstring(indent, f'Class {node.name}')))

    for node, _ in missing['methods']:
        if not node.body:
            continue
        first = node.body[0]
        indent = ' ' * node.col_offset
        if first.lineno == node.lineno:
            original = lines[first.lineno - 1]
            replacement = rewrite_inline_statement(original, indent, f'{node.name} method')
            if replacement is not None:
                actions.append((first.lineno - 1, 'replace', replacement))
                continue
        actions.append((first.lineno - 1, 'insert', build_docstring(indent, f'{node.name} method')))

    for node, _ in missing['functions']:
        if not node.body:
            continue
        first = node.body[0]
        indent = ' ' * node.col_offset
        if first.lineno == node.lineno:
            original = lines[first.lineno - 1]
            replacement = rewrite_inline_statement(original, indent, f'{node.name} function')
            if replacement is not None:
                actions.append((first.lineno - 1, 'replace', replacement))
                continue
        actions.append((first.lineno - 1, 'insert', build_docstring(indent, f'{node.name} function')))

    if not actions:
        return False

    deduped: dict[int, tuple[int, str, str]] = {}
    for idx, mode, payload in actions:
        deduped[idx] = (idx, mode, payload)

    for idx, mode, payload in sorted(deduped.values(), key=lambda item: item[0], reverse=True):
        if mode == 'replace':
            if idx < len(lines):
                replacement_lines = payload.splitlines(keepends=True)
                if replacement_lines and not replacement_lines[-1].endswith('\n'):
                    replacement_lines[-1] += '\n'
                lines[idx : idx + 1] = replacement_lines
        else:
            lines.insert(idx, payload)

    new_src = ''.join(lines)
    if new_src != src:
        if not dry_run:
            path.write_text(new_src, encoding='utf-8')
        return True

    return False


def main() -> None:
    parser = argparse.ArgumentParser(description='Fill missing docstrings in python files.')
    parser.add_argument('paths', nargs='*', default=['bt_api', 'bt_api_py'])
    parser.add_argument('--dry-run', action='store_true', help='Do not write files, only report')
    args = parser.parse_args()

    if args.dry_run:
        print('Dry run mode: no files will be modified.')

    scanned = 0
    touched = 0
    for root in args.paths:
        for file_path in find_python_files(root):
            scanned += 1
            changed = apply_docstrings(file_path, dry_run=args.dry_run)
            if changed:
                touched += 1
                if args.dry_run:
                    print(f'Would update: {file_path}')

    print(f'Scanned {scanned} files, would modify {touched} files.')


if __name__ == '__main__':
    main()
