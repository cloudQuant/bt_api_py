#!/usr/bin/env python3
"""Fix E402 caused by module docstrings not being the first statement.

This script moves the first top-level string literal (module docstring) to the
top of the file, preserving an optional shebang and encoding declaration.

It is intentionally conservative:
- Only rewrites a file when it finds a top-level docstring that is not the first
  statement.
- Does not attempt to reorder imports beyond moving the docstring.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


_CODING_RE = re.compile(r"^#.*coding[:=]\s*([-\w.]+)")


def _prefix_line_count(lines: list[str]) -> int:
    """Return number of prefix lines (shebang and/or encoding) to preserve."""
    i = 0
    if i < len(lines) and lines[i].startswith("#!"):
        i += 1
    if i < len(lines) and _CODING_RE.match(lines[i]):
        i += 1
    return i


def _find_misplaced_docstring_node(mod: ast.Module) -> ast.Expr | None:
    if not mod.body:
        return None
    if isinstance(mod.body[0], ast.Expr) and isinstance(mod.body[0].value, ast.Constant):
        if isinstance(mod.body[0].value.value, str):
            return None  # already first statement
    for node in mod.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                return node
        # Stop early once we hit a real definition or executable statement.
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Assign,
                ast.AnnAssign,
                ast.If,
                ast.For,
                ast.While,
                ast.With,
                ast.Try,
            ),
        ):
            break
    return None


def fix_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    try:
        mod = ast.parse(text)
    except SyntaxError:
        return False

    node = _find_misplaced_docstring_node(mod)
    if node is None:
        return False
    if not (hasattr(node, "lineno") and hasattr(node, "end_lineno")):
        return False

    lines = text.splitlines(keepends=True)
    prefix = _prefix_line_count(lines)

    start = node.lineno - 1
    end = node.end_lineno
    if start < prefix:
        return False

    doc_lines = lines[start:end]
    remaining = lines[:start] + lines[end:]

    # Insert docstring after preserved prefix, then ensure a blank line after it.
    insert_at = prefix
    new_lines = remaining[:insert_at] + doc_lines
    if not doc_lines[-1].endswith("\n"):
        new_lines.append("\n")
    if len(new_lines) == 0 or new_lines[-1] != "\n":
        new_lines.append("\n")
    new_lines += remaining[insert_at:]

    new_text = "".join(new_lines)
    if new_text == text:
        return False

    path.write_text(new_text, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: fix_module_docstring_position.py <file> [<file> ...]")
        return 2

    changed = 0
    for p in argv[1:]:
        path = Path(p)
        if not path.exists() or path.suffix != ".py":
            continue
        if fix_file(path):
            changed += 1
    print(f"changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

