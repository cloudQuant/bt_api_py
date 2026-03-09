#!/usr/bin/env python3
"""Script to improve code quality by adding type hints and docstrings."""

import json
import os
import re
from pathlib import Path


def read_file(file_path: str) -> str:
    """Read file content."""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def write_file(file_path: str, content: str) -> None:
    """Write content to file."""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)


def add_type_hints_to_function(match: re.Match) -> str:
    """Add type hints to function signature."""
    func_def = match.group(0)

    # Skip if already has return type annotation
    if " -> " in func_def:
        return func_def

    # Skip __repr__ and __str__ as they should return str
    if "__repr__" in func_def or "__str__" in func_def:
        if " -> " not in func_def:
            func_def = func_def.rstrip(":") + " -> str:"
        return func_def

    # Skip __init__ as it returns None
    if "__init__" in func_def:
        if " -> " not in func_def:
            func_def = func_def.rstrip(":") + " -> None:"
        return func_def

    # Add -> Any for other functions
    if " -> " not in func_def:
        func_def = func_def.rstrip(":") + " -> Any:"

    return func_def


def improve_file(file_path: str, stats: dict) -> None:
    """Improve a single file by adding type hints and docstrings."""
    full_path = Path("/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py") / file_path

    if not full_path.exists():
        print(f"File not found: {full_path}")
        return

    content = read_file(str(full_path))
    original_content = content

    # Check if file is __init__.py with only imports
    if file_path.endswith("__init__.py"):
        if not content.strip().startswith('"""') and not content.strip().startswith("'''"):
            # Add module docstring
            module_name = Path(file_path).parent.name
            docstring = f'"""{module_name.replace("_", " ").title()} module."""\n\n'
            content = docstring + content
            stats["docstrings_added"] += 1
    else:
        # Add module docstring if missing
        if not content.strip().startswith('"""') and not content.strip().startswith("'''"):
            module_name = Path(file_path).stem.replace("_", " ").title()
            docstring = f'"""{module_name} implementation."""\n\n'
            content = docstring + content
            stats["docstrings_added"] += 1

        # Add typing imports if needed
        if "from typing import" not in content and " -> " in content:
            typing_import = "from typing import Any\n\n"
            # Find first import line
            import_match = re.search(r"^(import |from )", content, re.MULTILINE)
            if import_match:
                insert_pos = import_match.start()
                content = content[:insert_pos] + typing_import + content[insert_pos:]

        # Add type hints to functions
        func_pattern = r"def\s+\w+\([^)]*\)\s*:"
        matches = list(re.finditer(func_pattern, content))

        for match in reversed(matches):  # Process from end to preserve positions
            func_def = match.group(0)

            # Skip if already has return type
            if " -> " in func_def:
                continue

            # Determine return type based on function name
            func_name_match = re.search(r"def\s+(\w+)", func_def)
            if not func_name_match:
                continue

            func_name = func_name_match.group(1)

            # Add appropriate return type
            if func_name in ["__init__", "__post_init__"]:
                new_func_def = func_def.rstrip(":") + " -> None:"
            elif func_name in ["__str__", "__repr__"]:
                new_func_def = func_def.rstrip(":") + " -> str:"
            elif (
                func_name.startswith("get_")
                or func_name.startswith("is_")
                or func_name.startswith("has_")
            ):
                new_func_def = func_def.rstrip(":") + " -> Any:"
            else:
                new_func_def = func_def.rstrip(":") + " -> Any:"

            content = content[: match.start()] + new_func_def + content[match.end() :]
            stats["type_hints_added"] += 1

    # Only write if content changed
    if content != original_content:
        write_file(str(full_path), content)
        stats["files_processed"] += 1
        print(f"Processed: {file_path}")


def main():
    """Main function to process all files."""
    # Load task file
    task_file = "/Users/yunjinqi/Documents/source_code/bt_api_py/agent_tasks/agent_8_tasks.json"
    with open(task_file, "r") as f:
        tasks = json.load(f)

    stats = {
        "files_processed": 0,
        "type_hints_added": 0,
        "docstrings_added": 0,
    }

    # Process each file
    for file_info in tasks["files"]:
        file_path = file_info["file_path"]
        improve_file(file_path, stats)

    # Print statistics
    print("\n" + "=" * 60)
    print("Code Quality Improvement Statistics")
    print("=" * 60)
    print(f"Files processed: {stats['files_processed']}")
    print(f"Type hints added: {stats['type_hints_added']}")
    print(f"Docstrings added: {stats['docstrings_added']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
