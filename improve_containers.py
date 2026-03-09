#!/usr/bin/env python3
"""自动改进 containers 文件的类型注释和文档字符串"""

import re
from pathlib import Path
from typing import Any


def add_type_hints_to_init(content: str, class_name: str) -> str:
    """为 __init__ 方法添加类型提示"""

    # 匹配 __init__ 方法签名
    pattern = rf"(class {class_name}.*?def __init__\(self,\s*)([^)]+)\):"

    def replace_init(match):
        params_str = match.group(2)
        # 解析参数
        params = []
        for param in params_str.split(","):
            param = param.strip()
            if not param or param == "self":
                continue

            # 提取参数名和默认值
            if "=" in param:
                name, default = param.split("=", 1)
                name = name.strip()
                default = default.strip()
            else:
                name = param.strip()
                default = None

            # 根据参数名推断类型
            param_type = infer_param_type(name, default)

            if default:
                params.append(f"{name}: {param_type} = {default}")
            else:
                params.append(f"{name}: {param_type}")

        params_with_types = ",\n        ".join(params)
        return f"{match.group(1)}\n        {params_with_types},\n    ) -> None:"

    return re.sub(pattern, replace_init, content, flags=re.DOTALL)


def infer_param_type(param_name: str, default: str | None) -> str:
    """根据参数名推断类型"""

    type_mapping = {
        "symbol_name": "str",
        "asset_type": "str",
        "trade_info": "str | dict[str, Any]",
        "account_info": "dict[str, Any]",
        "bar_info": "dict[str, Any]",
        "order_info": "dict[str, Any]",
        "position_info": "dict[str, Any]",
        "ticker_info": "dict[str, Any]",
        "symbol_info": "str | dict[str, Any]",
        "data": "str | dict[str, Any]",
        "has_been_json_encoded": "bool",
        "is_rest": "bool",
        "trade_list": "list[Any]",
        "data_queue": "str",
        "symbol": "str",
    }

    return type_mapping.get(param_name, "Any")


def add_return_type_to_method(content: str, method_name: str, return_type: str) -> str:
    """为方法添加返回类型"""
    pattern = rf"(def {method_name}\(self.*?\)):"
    replacement = rf"\1 -> {return_type}:"
    return re.sub(pattern, replacement, content)


def add_docstring_to_method(content: str, method_name: str, docstring: str) -> str:
    """为方法添加文档字符串"""
    pattern = rf'(def {method_name}\(self.*?\):\s*)(?:"""[^"]*""")?'

    def add_doc(match):
        existing_doc = match.group(0)
        if '"""' in existing_doc:
            return existing_doc
        return f'{match.group(1)}\n        """{docstring}"""\n'

    return re.sub(pattern, add_doc, content)


def process_file(file_path: Path) -> bool:
    """处理单个文件"""
    print(f"Processing {file_path}...")

    try:
        content = file_path.read_text(encoding="utf-8")
        original_content = content

        # 添加必要的导入
        if "from typing import Any" not in content and "Any" in content:
            # 在第一个 import 之后添加
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if line.startswith("import ") or line.startswith("from "):
                    lines.insert(i + 1, "from typing import Any")
                    break
            content = "\n".join(lines)

        # 为所有 get_ 方法添加返回类型
        get_methods = re.findall(r"def (get_\w+)\(self\):", content)
        for method in get_methods:
            # 根据方法名推断返回类型
            if (
                "time" in method
                or "price" in method
                or "volume" in method
                or "qty" in method
                or "amount" in method
                or "balance" in method
                or "margin" in method
                or "pnl" in method
                or "profit" in method
            ):
                return_type = "float | None"
            elif (
                "name" in method
                or "id" in method
                or "symbol" in method
                or "side" in method
                or "type" in method
                or "status" in method
            ):
                return_type = "str | None"
            elif "types" in method or "force" in method:
                return_type = "list[str] | None"
            elif "data" in method or "balances" in method or "positions" in method:
                return_type = "dict[str, Any] | list[Any]"
            elif "is_" in method or "has_" in method:
                return_type = "bool"
            else:
                return_type = "str | None"

            content = add_return_type_to_method(content, method, return_type)

        # 为 init_data 方法添加返回类型
        content = add_return_type_to_method(content, "init_data", "Self")

        # 如果有修改，写回文件
        if content != original_content:
            file_path.write_text(content, encoding="utf-8")
            print(f"✓ Updated {file_path}")
            return True
        else:
            print(f"- No changes needed for {file_path}")
            return False

    except Exception as e:
        print(f"✗ Error processing {file_path}: {e}")
        return False


def main():
    """主函数"""
    base_path = Path("/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/containers")

    # 定义要处理的目录
    directories = [
        "trades",
        "ctp",
        "symbols",
    ]

    total_files = 0
    updated_files = 0

    for directory in directories:
        dir_path = base_path / directory
        if not dir_path.exists():
            continue

        # 查找所有 Python 文件
        for py_file in dir_path.glob("*.py"):
            if py_file.name.startswith("__"):
                continue

            total_files += 1
            if process_file(py_file):
                updated_files += 1

    print(f"\n{'=' * 60}")
    print(f"Total files processed: {total_files}")
    print(f"Files updated: {updated_files}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
