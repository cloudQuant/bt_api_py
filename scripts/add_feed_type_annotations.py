#!/usr/bin/env python3
"""Add consistent type annotations to exchange request_base.py and spot.py files."""

from pathlib import Path

FEEDS_DIR = Path(__file__).resolve().parents[1] / "bt_api_py" / "feeds"

# Patterns to add/fix (search, replace)
INIT_PATTERNS = [
    (
        'def __init__(self, data_queue, **kwargs):',
        'def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:',
    ),
    (
        'def __init__(self, data_queue, **kwargs) -> Any | None:',
        'def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:',
    ),
]

CAPABILITIES_PATTERNS = [
    (
        '@classmethod\n    def _capabilities(cls):',
        '@classmethod\n    def _capabilities(cls) -> set[Capability]:',
    ),
]

TYPING_IMPORT = "from typing import Any"

def ensure_typing_import(content: str) -> str:
    """Ensure 'from typing import Any' exists, add after first non-comment import."""
    if "from typing import" in content or "import typing" in content:
        return content
    lines = content.split("\n")
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith(("import ", "from ")) and "typing" not in line:
            insert_idx = i + 1
            break
    if insert_idx > 0:
        lines.insert(insert_idx, TYPING_IMPORT)
        return "\n".join(lines)
    return content

def process_file(filepath: Path) -> bool:
    """Process a single file. Returns True if modified."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return False

    original = content

    # Add typing import if needed
    if "def __init__(self, data_queue" in content and "Any" in content:
        content = ensure_typing_import(content)

    # Fix __init__ patterns
    for old, new in INIT_PATTERNS:
        if old in content:
            content = content.replace(old, new)

    # Fix _capabilities (need Capability import)
    if "@classmethod" in content and "def _capabilities(cls):" in content:
        if "def _capabilities(cls) -> set[Capability]:" not in content:
            content = content.replace(
                "def _capabilities(cls):",
                "def _capabilities(cls) -> set[Capability]:",
            )

    return content != original

def main():
    modified = 0
    for req_base in FEEDS_DIR.rglob("request_base.py"):
        if process_file(req_base):
            modified += 1
            print(f"Modified: {req_base.relative_to(FEEDS_DIR.parent)}")
    for spot in FEEDS_DIR.rglob("spot.py"):
        if process_file(spot):
            modified += 1
            print(f"Modified: {spot.relative_to(FEEDS_DIR.parent)}")
    print(f"Total modified: {modified}")

if __name__ == "__main__":
    main()
