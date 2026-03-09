#!/usr/bin/env python3
"""
Automated code quality improvement script for bt_api_py
Adds type hints and improves docstrings according to Google style
"""

import json
import re
from pathlib import Path
from typing import Any


class CodeImprover:
    """Automatically improve code quality by adding type hints and docstrings"""

    def __init__(self):
        self.improved_files = 0
        self.improved_methods = 0

    def add_typing_import(self, code: str) -> str:
        """Add typing import if needed"""
        if "from typing import" in code:
            # Check if Any is imported
            if re.search(r"from typing import.*\bAny\b", code):
                return code
            # Add Any to existing import
            code = re.sub(r"from typing import (.+)", r"from typing import Any, \1", code)
        else:
            # Add new typing import after docstring
            if code.startswith('"""'):
                end = code.find('"""', 3) + 3
                code = code[:end] + "\n\nfrom typing import Any\n" + code[end:]
            else:
                code = "from typing import Any\n\n" + code
        return code

    def infer_param_type(self, param_name: str) -> str:
        """Infer parameter type based on naming conventions"""
        param_lower = param_name.lower()

        # Common patterns
        if param_name == "self":
            return None
        elif "kwargs" in param_lower:
            return "Any"
        elif param_lower in ["content", "data", "info", "extra_data"]:
            return "dict[str, Any]"
        elif param_lower in ["symbol", "inst_id", "inst_type", "ccy", "currency"]:
            return "str | None"
        elif param_lower in ["limit", "count", "after", "before"]:
            return "int | None"
        elif param_lower in ["lever", "amt", "px", "price", "qty"]:
            return "float | None"
        elif param_lower in ["extra_data"]:
            return "dict[str, Any] | None"
        else:
            return "Any"

    def add_type_hints_to_method(self, code: str, method_start: int) -> str:
        """Add type hints to a method"""
        # Find method signature
        sig_end = code.find(":", method_start)
        if sig_end == -1:
            return code

        sig = code[method_start:sig_end]

        # Check if already has type hints
        if "->" in code[method_start : method_start + 200]:
            return code

        # Parse method name and parameters
        match = re.search(r"def\s+(\w+)\s*\(([^)]*)\)", sig)
        if not match:
            return code

        method_name = match.group(1)
        params_str = match.group(2)

        # Parse parameters
        params = []
        for param in params_str.split(","):
            param = param.strip()
            if not param or param == "self":
                params.append(param)
                continue

            # Remove default value
            param_name = param.split("=")[0].strip()
            if ":" in param_name:
                # Already has type hint
                params.append(param)
                continue

            # Infer type
            param_type = self.infer_param_type(param_name)
            if param_type:
                params.append(f"{param_name}: {param_type}")
            else:
                params.append(param_name)

        # Build new signature
        new_sig = f"def {method_name}({', '.join(params)}) -> dict[str, Any]:"

        # Replace in code
        old_sig_pattern = re.escape(match.group(0))
        code = re.sub(old_sig_pattern, new_sig, code, count=1)

        return code

    def improve_docstring(self, docstring: str, params: list[str]) -> str:
        """Improve docstring to Google style"""
        if not docstring:
            return None

        docstring = docstring.strip()

        # Skip if already has Args section
        if "Args:" in docstring or "Returns:" in docstring:
            return None

        # Extract summary
        lines = docstring.split("\n")
        summary = lines[0]

        # Build new docstring
        new_lines = [summary, ""]

        # Add Args section if there are parameters
        if params and params != ["self"]:
            new_lines.append("Args:")
            for param in params:
                if param != "self":
                    param_name = param.split("=")[0].strip()
                    if ":" in param_name:
                        param_name = param_name.split(":")[0]
                    new_lines.append(f"    {param_name}: Parameter description.")
            new_lines.append("")

        # Add Returns section
        new_lines.append("Returns:")
        new_lines.append("    Response data from the API.")

        return "\n".join(new_lines)

    def process_method(
        self, code: str, method_name: str, needs_types: bool, needs_doc: bool
    ) -> str:
        """Process a single method"""
        # Find method
        pattern = rf"(\s+def {method_name}\s*\()"
        match = re.search(pattern, code)
        if not match:
            return code

        method_start = match.start()
        indent = match.group(1)

        # Add type hints
        if needs_types:
            code = self.add_type_hints_to_method(code, method_start)

        # Improve docstring
        if needs_doc:
            # Find method signature end
            sig_end = code.find(":", method_start)
            if sig_end != -1:
                # Find existing docstring
                doc_start = code.find('"""', sig_end)
                if doc_start != -1 and doc_start < sig_end + 200:
                    doc_end = code.find('"""', doc_start + 3)
                    if doc_end != -1:
                        old_doc = code[doc_start + 3 : doc_end]

                        # Parse parameters
                        sig_match = re.search(
                            r"def\s+\w+\s*\(([^)]*)\)", code[method_start:sig_end]
                        )
                        if sig_match:
                            params = [
                                p.strip().split("=")[0].strip()
                                for p in sig_match.group(1).split(",")
                            ]
                            params = [p.split(":")[0].strip() for p in params if p and p != "self"]

                            new_doc = self.improve_docstring(old_doc, params)
                            if new_doc:
                                code = code[: doc_start + 3] + new_doc + code[doc_end:]

        return code

    def process_file(self, file_path: str, issues: list[str]) -> bool:
        """Process a single file"""
        path = Path(file_path)
        if not path.exists():
            print(f"  ✗ File not found: {file_path}")
            return False

        code = path.read_text()
        original_code = code

        # Analyze issues
        methods_to_improve = {}
        for issue in issues:
            # Extract method name
            match = re.search(r"\.(\w+)\s*-\s*(.+)", issue)
            if match:
                method_name = match.group(1)
                issue_type = match.group(2)

                if method_name not in methods_to_improve:
                    methods_to_improve[method_name] = {"types": False, "doc": False}

                if (
                    "missing return annotation" in issue_type
                    or "missing annotation for param" in issue_type
                ):
                    methods_to_improve[method_name]["types"] = True
                elif "incomplete" in issue_type or "missing docstring" in issue_type:
                    methods_to_improve[method_name]["doc"] = True

        # Add typing import if needed
        if any(m["types"] for m in methods_to_improve.values()):
            code = self.add_typing_import(code)

        # Process each method
        for method_name, improvements in methods_to_improve.items():
            code = self.process_method(
                code, method_name, improvements["types"], improvements["doc"]
            )
            if code != original_code:
                self.improved_methods += 1

        # Save if changed
        if code != original_code:
            path.write_text(code)
            print(f"  ✓ Improved: {file_path}")
            self.improved_files += 1
            return True
        else:
            print(f"  - No changes: {file_path}")
            return False

    def run(self, tasks_file: str) -> None:
        """Run the improvement process"""
        tasks_path = Path(tasks_file)
        if not tasks_path.exists():
            print(f"Tasks file not found: {tasks_file}")
            return

        with open(tasks_path) as f:
            tasks = json.load(f)

        print(f"Processing {len(tasks)} files...")
        print(f"Total issues to fix: {sum(len(i) for i in tasks.values())}")
        print()

        for file_path, issues in tasks.items():
            self.process_file(file_path, issues)

        print()
        print("=" * 60)
        print(f"Summary:")
        print(f"  Files improved: {self.improved_files}/{len(tasks)}")
        print(f"  Methods improved: {self.improved_methods}")
        print("=" * 60)


def main():
    improver = CodeImprover()
    improver.run("scripts/agent_tasks/agent_5_tasks.json")


if __name__ == "__main__":
    main()
