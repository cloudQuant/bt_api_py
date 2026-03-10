#!/usr/bin/env python3
"""Fix E402 in tests by moving `pytestmark` below imports.

Many test modules set `pytestmark = [...]` before importing the system under test,
which triggers Ruff E402. This script moves a single-line `pytestmark = ...`
assignment to just after the initial import block.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


_PYTESTMARK_RE = re.compile(r"^pytestmark\s*=\s*.+$")
_IMPORT_RE = re.compile(r"^(from\s+\S+\s+import\s+|import\s+)")


def fix_file(path: Path) -> bool:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines:
        return False

    # Find a single-line pytestmark assignment near the top.
    pm_idx = None
    for i, line in enumerate(lines[:80]):
        if _PYTESTMARK_RE.match(line.strip()):
            pm_idx = i
            break
        # Stop scanning if we hit a def/class; we only want the header area.
        if line.startswith("def ") or line.startswith("class "):
            return False
    if pm_idx is None:
        return False

    # If pytestmark is already after imports, do nothing.
    # We consider it "misplaced" if there is any import line after it in the header.
    has_import_after = any(_IMPORT_RE.match(lines[j]) for j in range(pm_idx + 1, min(len(lines), 120)))
    if not has_import_after:
        return False

    pm_line = lines[pm_idx]
    # Remove pytestmark and adjacent blank line(s) to avoid leaving double gaps.
    del lines[pm_idx]
    while pm_idx < len(lines) and lines[pm_idx].strip() == "":
        # Keep a single blank line after import block later; remove immediate blanks here.
        del lines[pm_idx]

    # Find end of the initial import block.
    insert_at = 0
    for i, line in enumerate(lines):
        if _IMPORT_RE.match(line) or line.strip() == "" or line.lstrip().startswith("#"):
            insert_at = i + 1
            continue
        break

    # Ensure exactly one blank line before pytestmark (after imports).
    if insert_at > 0 and lines[insert_at - 1].strip() != "":
        pm_line = "\n" + pm_line

    # Ensure a blank line after pytestmark.
    if insert_at < len(lines) and (lines[insert_at].strip() != ""):
        pm_line = pm_line.rstrip("\n") + "\n\n"

    lines.insert(insert_at, pm_line)

    new_text = "".join(lines)
    path.write_text(new_text, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: fix_pytestmark_position.py <file> [<file> ...]")
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

