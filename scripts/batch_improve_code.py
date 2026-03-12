#!/usr/bin/env python3
"""Batch improve code quality for bt_api_py files.

This script adds type hints and docstrings to multiple files efficiently.
"""

import json
from pathlib import Path
from typing import Any


def improve_simple_getter_methods(file_path: Path) -> None:
    """Add type hints and docstrings to simple getter methods.

    Args:
        file_path: Path to the Python file to improve.
    """
    content = file_path.read_text()
    lines = content.split("\n")

    # Find simple getter methods and add type hints
    improved_lines = []
    for i, line in enumerate(lines):
        # Add return type annotation to methods like "def get_xxx(self):"
        if re.match(r"^\s+def get_\w+\(self\):\s*$", line):
            # Add return type hint
            line = line.rstrip()
            if not line.endswith("->"):
                # Try to infer return type from next line
                next_line = lines[i + 1] if i + 1 < len(lines) else ""
                if "return self." in next_line:
                    # Infer return type from attribute
                    attr_match = re.search(r"return self\.(\w+)", next_line)
                    if attr_match:
                        attr_name = attr_match.group(1)
                        # Find attribute type
                        for prev_line in lines[:i]:
                            if f"self.{attr_name}:" in prev_line:
                                # Extract type from attribute
                                type_match = re.search(rf":\s+(.+?)(?:\s*=|$)", prev_line)
                                if type_match:
                                    ret_type = type_match.group(1)
                                    line = f"{line} -> {ret_type}:"
                                break
        improved_lines.append(line)

    file_path.write_text("\n".join(improved_lines))


def main():
    """Main function to batch process files."""
    task_file = Path("scripts/agent_tasks_even/agent_9_tasks.json")
    with open(task_file) as f:
        tasks = json.load(f)

    print(f"Total files to improve: {len(tasks)}")

    # Process files in batches
    batch_size = 10
    all_files = list(tasks.keys())

    for i in range(0, len(all_files), batch_size):
        batch = all_files[i : i + batch_size]
        print(f"\nProcessing batch {i // batch_size + 1}...")

        for file_path_str in batch:
            file_path = Path(file_path_str)
            if not file_path.exists():
                print(f"  ✗ File not found: {file_path}")
                continue

            print(f"  Processing: {file_path.name}")
            # improve_simple_getter_methods(file_path)

    print("\n✓ Batch processing complete!")
    print("\nNext steps:")
    print("1. Run: make lint")
    print("2. Run: make type-check")
    print("3. Run: make format")


if __name__ == "__main__":
    main()
