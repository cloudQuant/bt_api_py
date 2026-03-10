#!/usr/bin/env python3
"""
自动化脚本：将生产代码中的 print 语句替换为 logger 调用
"""

import re
from pathlib import Path
from typing import List, Tuple

# 需要处理的文件和对应的 print 行号
FILES_TO_PROCESS = {
    "bt_api_py/containers/orderbooks/bybit_orderbook.py": [82],
    "bt_api_py/containers/balances/coinbase_balance.py": [117],
    "bt_api_py/containers/balances/upbit_balance.py": [58],
    "bt_api_py/containers/balances/bybit_balance.py": [78],
    "bt_api_py/containers/balances/hyperliquid_balance.py": [68, 177],
    "bt_api_py/containers/accounts/coinbase_account.py": [79, 257, 312],
    "bt_api_py/containers/accounts/hyperliquid_account.py": [56],
    "bt_api_py/containers/tickers/coinbase_ticker.py": [131, 255, 292],
    "bt_api_py/containers/tickers/bybit_ticker.py": [77],
    "bt_api_py/containers/tickers/upbit_ticker.py": [90],
    "bt_api_py/containers/tickers/hyperliquid_ticker.py": [66],
    "bt_api_py/containers/orders/coinbase_order.py": [87, 245, 299],
    "bt_api_py/containers/orders/upbit_order.py": [88],
    "bt_api_py/containers/orders/bybit_order.py": [100, 140],
    "bt_api_py/containers/orders/hyperliquid_order.py": [59, 186],
    "bt_api_py/containers/trades/hyperliquid_trade.py": [50],
    "bt_api_py/containers/bars/coinbase_bar.py": [156],
}


def has_logger_import(content: str) -> bool:
    """检查文件是否已经导入了 logger"""
    return "from bt_api_py.logging_factory import get_logger" in content


def add_logger_import(content: str) -> str:
    """在文件顶部添加 logger 导入"""
    lines = content.split("\n")

    # 找到最后一个 import 行
    last_import_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            last_import_idx = i

    # 在最后一个 import 后添加 logger 导入
    lines.insert(last_import_idx + 1, "from bt_api_py.logging_factory import get_logger")
    lines.insert(last_import_idx + 2, "")
    lines.insert(last_import_idx + 3, 'logger = get_logger("container")')
    lines.insert(last_import_idx + 4, "")

    return "\n".join(lines)


def replace_print_with_logger(line: str) -> str:
    """将 print 语句替换为 logger 调用"""
    # 匹配 print(f"Error ...: {e}") 或 print(f"... Error ...")
    if 'print(f"Error' in line or 'print(f"error' in line:
        # 提取错误信息
        match = re.search(r'print\(f"([^"]+)"\s*(?:,\s*(.+?))?\)', line)
        if match:
            error_msg = match.group(1)
            indent = len(line) - len(line.lstrip())
            spaces = " " * indent
            # 如果有 exc_info，添加 exc_info=True
            if "{e}" in error_msg or "exception" in error_msg.lower():
                return f'{spaces}logger.error(f"{error_msg}", exc_info=True)\n'
            else:
                return f'{spaces}logger.error(f"{error_msg}")\n'

    # 匹配普通的 print 语句
    match = re.search(r"(\s*)print\((.+)\)", line)
    if match:
        indent = match.group(1)
        content = match.group(2)
        # 根据内容判断日志级别
        if "error" in content.lower() or "fail" in content.lower():
            return f"{indent}logger.error({content})\n"
        elif "warn" in content.lower():
            return f"{indent}logger.warning({content})\n"
        else:
            return f"{indent}logger.info({content})\n"

    return line


def process_file(file_path: str, line_numbers: List[int]):
    """处理单个文件"""
    path = Path(file_path)
    if not path.exists():
        print(f"文件不存在: {file_path}")
        return

    print(f"处理文件: {file_path}")

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 检查是否需要添加 logger 导入
    if not has_logger_import(content):
        content = add_logger_import(content)
        print(f"  ✓ 添加了 logger 导入")

    lines = content.split("\n")

    # 替换指定的 print 语句
    # 注意：添加导入后行号会偏移
    offset = 4  # 添加了 4 行（import + 空行 + logger + 空行）

    for line_num in line_numbers:
        adjusted_line_num = line_num + offset - 1  # 转为 0-based index
        if adjusted_line_num < len(lines):
            original_line = lines[adjusted_line_num]
            if "print(" in original_line:
                new_line = replace_print_with_logger(original_line)
                lines[adjusted_line_num] = new_line
                print(f"  ✓ 行 {line_num}: {original_line.strip()} -> {new_line.strip()}")
            else:
                print(f"  ! 行 {line_num} 不包含 print 语句: {original_line.strip()}")

    # 写回文件
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"  ✓ 文件处理完成\n")


def main():
    """主函数"""
    print("=" * 60)
    print("开始处理 print 语句替换")
    print("=" * 60 + "\n")

    for file_path, line_numbers in FILES_TO_PROCESS.items():
        process_file(file_path, line_numbers)

    print("=" * 60)
    print("处理完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
