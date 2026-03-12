#!/usr/bin/env python3
"""Script to batch improve code quality for order container files."""

import ast
import re
from pathlib import Path
from typing import Any


def add_type_hints_to_method(method_code: str) -> str:
    """Add type hints to method parameters and return value."""
    lines = method_code.split("\n")
    result_lines = []

    for line in lines:
        # Add type hints to common parameters
        if "def __init__(self, order_info," in line and "symbol_name" in line:
            if "symbol_name:" not in line:
                line = line.replace("symbol_name,", "symbol_name: str,")
            if "asset_type," in line and "asset_type:" not in line:
                line = line.replace("asset_type,", "asset_type: str,")
            if "has_been_json_encoded=" in line and "has_been_json_encoded:" not in line:
                line = line.replace("has_been_json_encoded=", "has_been_json_encoded: bool =")

        result_lines.append(line)

    return "\n".join(result_lines)


def add_docstring_to_method(method_code: str, method_name: str) -> str:
    """Add Google-style docstring to method."""
    if '"""' in method_code or "'''" in method_code:
        return method_code

    lines = method_code.split("\n")
    if len(lines) < 2:
        return method_code

    # Find the indentation level
    first_line = lines[0]
    indent_match = re.match(r"^(\s*)", first_line)
    indent = indent_match.group(1) if indent_match else ""

    # Determine docstring based on method name
    docstring = None

    if method_name == "__init__":
        docstring = f'{indent}"""Initialize order data."""'
    elif method_name == "init_data":
        docstring = f'{indent}"""Initialize order data by parsing order_info.\n\n{indent}Returns:\n{indent}    Self for method chaining\n{indent}"""'
    elif method_name == "get_all_data":
        docstring = f'{indent}"""Get all order data as a dictionary.\n\n{indent}Returns:\n{indent}    Dictionary containing all order data fields\n{indent}"""'
    elif method_name.startswith("get_"):
        # Extract field name from method name
        field_name = method_name[4:].replace("_", " ")
        docstring = f'{indent}"""Get {field_name}.\n\n{indent}Returns:\n{indent}    {field_name.capitalize()}\n{indent}"""'
    elif method_name.startswith("is_"):
        # Boolean check methods
        check_name = method_name[3:].replace("_", " ")
        docstring = f'{indent}"""Check if order {check_name}.\n\n{indent}Returns:\n{indent}    True if order {check_name}\n{indent}"""'

    if docstring:
        # Insert docstring after the def line
        new_lines = [lines[0], docstring] + lines[1:]
        return "\n".join(new_lines)

    return method_code


def improve_file(file_path: Path) -> bool:
    """Improve a single file."""
    print(f"Processing: {file_path}")

    content = file_path.read_text()

    # Skip if file already has good type hints
    if " -> " in content and '"""' in content:
        print(f"  Skipping (already improved)")
        return False

    # Parse the file to find methods
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        print(f"  Error parsing file: {e}")
        return False

    # Find all class definitions
    improved = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    # Check if method needs improvement
                    method_name = item.name

                    # Skip private methods (starting with _) except __init__
                    if method_name.startswith("_") and method_name != "__init__":
                        continue

                    # Check if method has return annotation
                    if item.returns is None and method_name != "__init__":
                        # Add return type hint
                        improved = True

    if improved:
        print(f"  Marked for improvement")
        return True
    else:
        print(f"  No improvements needed")
        return False


def main():
    """Main function to process all files."""
    task_file = Path("scripts/agent_tasks/agent_3_tasks.json")
    import json

    with open(task_file) as f:
        tasks = json.load(f)

    files_to_improve = list(tasks.keys())
    print(f"Total files to check: {len(files_to_improve)}")

    improved_count = 0
    for file_path_str in files_to_improve:
        file_path = Path(file_path_str)
        if file_path.exists():
            if improve_file(file_path):
                improved_count += 1
        else:
            print(f"File not found: {file_path}")

    print(f"\nFiles marked for improvement: {improved_count}")


if __name__ == "__main__":
    main()
