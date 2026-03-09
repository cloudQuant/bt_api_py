#!/usr/bin/env python3
"""
批量改进 ticker 文件的代码质量
添加类型注释和 Google 风格的文档字符串
"""

import os
import re
from pathlib import Path


def add_type_hints_to_init(content: str, class_name: str) -> str:
    """为 __init__ 方法添加类型注释"""
    # 匹配 __init__ 方法签名
    pattern = r"(    def __init__\(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False\):)"
    replacement = r"""    def __init__(
        self,
        ticker_info: str | dict[str, any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:"""

    content = re.sub(pattern, replacement, content)
    return content


def add_docstring_to_init(content: str, exchange_name: str) -> str:
    """为 __init__ 方法添加文档字符串"""
    # 在 __init__ 方法后添加文档字符串
    pattern = r"(    def __init__\([^)]+\) -> None:\n)(        super\(\).__init__)"

    docstring = f'''        """Initialize {exchange_name} ticker data container.

        Args:
            ticker_info: Raw ticker data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether ticker_info is already parsed.
        """
'''

    replacement = r"\1" + docstring + r"\2"
    content = re.sub(pattern, replacement, content)
    return content


def add_return_type_to_init_data(content: str, class_name: str) -> str:
    """为 init_data 方法添加返回类型"""
    pattern = r"(    def init_data\(self\):)"
    replacement = r'    def init_data(self) -> "' + class_name + '":'
    content = re.sub(pattern, replacement, content)
    return content


def add_type_hints_to_parse_float(content: str) -> str:
    """为 _parse_float 方法添加类型注释"""
    # 添加 Any 导入（如果还没有）
    if "from typing import" not in content:
        # 在文件开头添加导入
        content = re.sub(
            r"(import json\nimport time)",
            r"import json\nimport time\nfrom typing import Any",
            content,
        )
    elif "Any" not in content:
        # 添加 Any 到现有导入
        content = re.sub(
            r"(from typing import [^\n]+)",
            lambda m: m.group(1).rstrip() + ", Any"
            if not m.group(1).rstrip().endswith(", Any")
            else m.group(1),
            content,
        )

    # 添加类型注释到 _parse_float
    pattern = r"(    @staticmethod\n    def _parse_float\(value\):)"
    replacement = r"    @staticmethod\n    def _parse_float(value: Any) -> float | None:"
    content = re.sub(pattern, replacement, content)

    # 添加文档字符串
    pattern = r"(    @staticmethod\n    def _parse_float\(value: Any\) -> float \| None:\n        if value is None:)"
    replacement = r'''    @staticmethod
    def _parse_float(value: Any) -> float | None:
        """Parse value to float.

        Args:
            value: Value to parse.

        Returns:
            Parsed float value or None if parsing fails.
        """
        if value is None:'''
    content = re.sub(pattern, replacement, content)

    return content


def add_type_hints_to_parse_int(content: str) -> str:
    """为 _parse_int 方法添加类型注释"""
    pattern = r"(    @staticmethod\n    def _parse_int\(value\):)"
    replacement = r"    @staticmethod\n    def _parse_int(value: Any) -> int | None:"
    content = re.sub(pattern, replacement, content)

    # 添加文档字符串
    pattern = r"(    @staticmethod\n    def _parse_int\(value: Any\) -> int \| None:\n        if value is None:)"
    replacement = r'''    @staticmethod
    def _parse_int(value: Any) -> int | None:
        """Parse value to int.

        Args:
            value: Value to parse.

        Returns:
            Parsed int value or None if parsing fails.
        """
        if value is None:'''
    content = re.sub(pattern, replacement, content)

    return content


def fix_ticker_data_type(content: str) -> str:
    """修复 ticker_data 的类型"""
    # 添加类型注释到 ticker_data
    pattern = r"(        self\.ticker_data = ticker_info if has_been_json_encoded else None)"
    replacement = r"""        self.ticker_data: dict[str, Any] | None = (
            ticker_info if has_been_json_encoded and isinstance(ticker_info, dict) else None
        )"""
    content = re.sub(pattern, replacement, content)
    return content


def process_file(filepath: Path) -> bool:
    """处理单个文件"""
    print(f"Processing {filepath}...")

    try:
        content = filepath.read_text()
        original_content = content

        # 获取类名（假设只有一个主要类）
        class_match = re.search(r"class (\w+RequestTickerData)\(TickerData\):", content)
        if not class_match:
            print(f"  No RequestTickerData class found in {filepath}")
            return False

        class_name = class_match.group(1)
        exchange_name = class_name.replace("RequestTickerData", "")

        # 应用改进
        content = add_type_hints_to_init(content, class_name)
        content = add_docstring_to_init(content, exchange_name)
        content = add_return_type_to_init_data(content, class_name)
        content = add_type_hints_to_parse_float(content)
        content = add_type_hints_to_parse_int(content)
        content = fix_ticker_data_type(content)

        # 只有在内容改变时才写入
        if content != original_content:
            filepath.write_text(content)
            print(f"  ✓ Updated {filepath}")
            return True
        else:
            print(f"  - No changes needed for {filepath}")
            return False

    except Exception as e:
        print(f"  ✗ Error processing {filepath}: {e}")
        return False


def main():
    """主函数"""
    tickers_dir = Path("bt_api_py/containers/tickers")

    # 获取所有需要处理的文件
    ticker_files = [
        f for f in tickers_dir.glob("*.py") if f.name != "__init__.py" and f.name != "ticker.py"
    ]

    print(f"Found {len(ticker_files)} ticker files to process\n")

    updated_count = 0
    for filepath in sorted(ticker_files):
        if process_file(filepath):
            updated_count += 1

    print(f"\n✓ Updated {updated_count} out of {len(ticker_files)} files")


if __name__ == "__main__":
    main()
