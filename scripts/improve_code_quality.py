#!/usr/bin/env python3
"""批量改进代码质量脚本 - 为函数添加类型注释和文档注释"""

import json
import re
from pathlib import Path


def add_return_annotation(code: str, line_num: int, return_type: str = "None") -> str:
    """为方法添加返回类型注释"""
    lines = code.split("\n")
    line_idx = line_num - 1

    if line_idx >= len(lines):
        return code

    line = lines[line_idx]

    # 查找方法定义
    if "def " in line:
        # 检查是否已有返回类型注释
        if ") -> " not in line and ") ->" not in line:
            # 找到右括号位置
            paren_pos = line.rfind("):")
            if paren_pos != -1:
                # 在 ): 之前插入返回类型
                new_line = line[:paren_pos] + f") -> {return_type}:"
                lines[line_idx] = new_line
                return "\n".join(lines)

    return code


def add_param_annotation(code: str, line_num: int, param_name: str, param_type: str) -> str:
    """为参数添加类型注释"""
    lines = code.split("\n")
    line_idx = line_num - 1

    if line_idx >= len(lines):
        return code

    line = lines[line_idx]

    # 检查参数是否已有类型注释
    if f"{param_name}:" in line:
        return code

    # 添加类型注释
    pattern = rf"\b{param_name}\b(?!\s*:)"
    new_line = re.sub(pattern, f"{param_name}: {param_type}", line)
    lines[line_idx] = new_line

    return "\n".join(lines)


def add_docstring(code: str, line_num: int, docstring: str) -> str:
    """为方法添加文档注释"""
    lines = code.split("\n")
    line_idx = line_num - 1

    if line_idx >= len(lines):
        return code

    # 找到方法的缩进级别
    line = lines[line_idx]
    indent = len(line) - len(line.lstrip())
    indent_str = " " * indent

    # 在下一行插入文档注释
    docstring_lines = docstring.split("\n")
    formatted_docstring = []
    formatted_docstring.append(f'{indent_str}"""{docstring_lines[0]}')
    for doc_line in docstring_lines[1:]:
        formatted_docstring.append(f"{indent_str}{doc_line}")
    formatted_docstring.append(f'{indent_str}"""')

    # 插入文档注释
    for i, doc_line in enumerate(reversed(formatted_docstring)):
        lines.insert(line_idx + 1, doc_line)

    return "\n".join(lines)


def process_file(file_path: str, issues: list[str]) -> bool:
    """处理单个文件的所有问题"""
    path = Path(file_path)
    if not path.exists():
        print(f"文件不存在: {file_path}")
        return False

    code = path.read_text(encoding="utf-8")
    modified = False

    # 按行号排序，从后往前处理，避免行号偏移
    issues_by_line = {}
    for issue in issues:
        # 解析问题: "TypeHint: Line 100: method - description"
        # 或 "Docstring: Line 100: method - description"
        match = re.match(r"(TypeHint|Docstring): Line (\d+): (.+)", issue)
        if match:
            issue_type, line_num, desc = match.groups()
            line_num = int(line_num)
            if line_num not in issues_by_line:
                issues_by_line[line_num] = []
            issues_by_line[line_num].append((issue_type, desc))

    # 从后往前处理
    for line_num in sorted(issues_by_line.keys(), reverse=True):
        for issue_type, desc in issues_by_line[line_num]:
            if issue_type == "TypeHint":
                # 处理类型注释问题
                if "missing return annotation" in desc:
                    code = add_return_annotation(code, line_num)
                    modified = True
                elif "missing annotation for param" in desc:
                    # 提取参数名和类型
                    param_match = re.search(r"param '(\w+)'", desc)
                    if param_match:
                        param_name = param_match.group(1)
                        # 根据参数名推断类型
                        param_type = infer_param_type(param_name)
                        code = add_param_annotation(code, line_num, param_name, param_type)
                        modified = True

    if modified:
        path.write_text(code, encoding="utf-8")
        print(f"✓ 已改进: {file_path}")

    return modified


def infer_param_type(param_name: str) -> str:
    """根据参数名推断类型"""
    type_hints = {
        "kwargs": "Any",
        "args": "Any",
        "user_id": "str",
        "token": "str",
        "symbol": "str",
        "data": "dict[str, Any]",
        "config": "dict[str, Any]",
        "attributes": "dict[str, Any]",
        "updates": "dict[str, Any]",
        "context_attrs": "dict[str, Any]",
        "details": "dict[str, Any]",
        "encryption_manager": "EncryptionManager | None",
        "audit_logger": "AuditLogger | None",
        "bt_api_instance": "Any",
        "func": "Callable",
        "future": "asyncio.Future",
        "request_data": "dict[str, Any]",
        "funding_rate_info": "dict[str, Any]",
        "symbol_name": "str",
        "asset_type": "str",
        "has_been_json_encoded": "bool",
        "data_queue": "str | None",
        "path": "str",
        "params": "dict[str, Any] | None",
        "body": "dict[str, Any] | None",
        "extra_data": "dict[str, Any] | None",
        "timeout": "int",
        "vol": "float",
        "volume": "float",
        "price": "float",
        "order_type": "str",
        "offset": "str",
        "client_order_id": "str | None",
        "order_id": "str",
        "count": "int",
        "period": "str",
        "limit": "int",
        "is_sign": "bool",
        "amount": "float",
        "post_only": "bool",
    }
    return type_hints.get(param_name, "Any")


def main():
    """主函数"""
    # 读取任务文件
    task_file = Path("scripts/agent_tasks/agent_10_tasks.json")
    if not task_file.exists():
        print("任务文件不存在")
        return

    tasks = json.loads(task_file.read_text(encoding="utf-8"))

    print(f"开始处理 {len(tasks)} 个文件...")

    success_count = 0
    for file_path, issues in tasks.items():
        if process_file(file_path, issues):
            success_count += 1

    print(f"\n完成! 成功改进 {success_count}/{len(tasks)} 个文件")


if __name__ == "__main__":
    main()
