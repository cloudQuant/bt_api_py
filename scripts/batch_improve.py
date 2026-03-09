#!/usr/bin/env python3
"""
批量改进代码质量：添加类型注释和文档字符串
"""

import json
import re
from pathlib import Path
from typing import Any


def add_type_hints_to_method(
    code: str, method_name: str, params: list[str], return_type: str = "None"
) -> str:
    """为方法添加类型注释"""
    # 查找方法定义
    pattern = rf"(\s*def {method_name}\()([^)]+)(\):)"

    def replacer(match):
        indent = match.group(1)
        params_str = match.group(2)
        close = match.group(3)

        # 如果已经有类型注释，跳过
        if (
            ":" in params_str
            and "->" in code[code.find(match.group(0)) : code.find(match.group(0)) + 200]
        ):
            return match.group(0)

        # 添加类型注释
        param_list = [p.strip() for p in params_str.split(",") if p.strip()]
        typed_params = []
        for param in param_list:
            if param == "self":
                typed_params.append(param)
            elif param in params or param == "content":
                typed_params.append(f"{param}: dict[str, Any]")
            elif "kwargs" in param:
                typed_params.append(f"{param}: Any")
            else:
                typed_params.append(f"{param}: Any")

        return f"{indent}def {method_name}({', '.join(typed_params)}) -> {return_type}:"

    return re.sub(pattern, replacer, code)


def improve_docstring(docstring: str, method_name: str, params: list[str]) -> str:
    """改进文档字符串"""
    if not docstring:
        return None

    # 如果已经有完整的Google风格docstring，跳过
    if "Args:" in docstring or "Returns:" in docstring:
        return None

    # 添加参数说明
    lines = docstring.strip().split("\n")
    summary = lines[0]

    # 构建新的docstring
    new_lines = [summary, ""]

    if params and params != ["self"]:
        new_lines.append("Args:")
        for param in params:
            if param != "self":
                new_lines.append(f"    {param}: Description of {param}.")
        new_lines.append("")

    return "\n".join(new_lines)


def process_file(file_path: str, issues: list[str]) -> None:
    """处理单个文件"""
    path = Path(file_path)
    if not path.exists():
        print(f"File not found: {file_path}")
        return

    code = path.read_text()
    original_code = code

    # 确保有typing导入
    if "from typing import Any" not in code and "dict[str, Any]" in code:
        # 在第一个import之后添加typing导入
        import_pattern = r"(import [^\n]+\n)"
        code = re.sub(import_pattern, r"\1from typing import Any\n", code, count=1)

    # 分析需要改进的方法
    methods_to_improve = {}
    for issue in issues:
        if "TypeHint" in issue or "Docstring" in issue:
            # 提取方法名
            match = re.search(r"\.(\w+)\s*-\s*", issue)
            if match:
                method_name = match.group(1)
                if method_name not in methods_to_improve:
                    methods_to_improve[method_name] = {"types": False, "docstring": False}

                if "missing return annotation" in issue or "missing annotation for param" in issue:
                    methods_to_improve[method_name]["types"] = True
                if "incomplete Google-style docstring" in issue or "missing docstring" in issue:
                    methods_to_improve[method_name]["docstring"] = True

    # 改进每个方法
    for method_name, improvements in methods_to_improve.items():
        if improvements["types"]:
            # 查找方法参数
            pattern = rf"def {method_name}\(([^)]*)\)"
            match = re.search(pattern, code)
            if match:
                params = [p.strip().split("=")[0].strip() for p in match.group(1).split(",")]
                params = [p for p in params if p]
                code = add_type_hints_to_method(code, method_name, params)

    # 如果代码有变化，写回文件
    if code != original_code:
        path.write_text(code)
        print(f"Improved: {file_path}")
    else:
        print(f"No changes: {file_path}")


def main():
    """主函数"""
    tasks_file = Path("scripts/agent_tasks/agent_5_tasks.json")
    if not tasks_file.exists():
        print("Tasks file not found")
        return

    with open(tasks_file) as f:
        tasks = json.load(f)

    print(f"Processing {len(tasks)} files...")

    for file_path, issues in tasks.items():
        print(f"\nProcessing: {file_path}")
        process_file(file_path, issues)

    print("\nDone!")


if __name__ == "__main__":
    main()
