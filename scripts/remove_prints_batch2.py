#!/usr/bin/env python3
"""
第二批 print 语句替换脚本
处理 functions 和 monitoring 目录
"""

from pathlib import Path
import re

FILES_TO_PROCESS = {
    "bt_api_py/functions/decorators.py": {
        "lines": [50, 65, 71],
        "imports": [
            "from bt_api_py.logging_factory import get_logger",
            "",
            'logger = get_logger("function")',
        ],
        "replace_type": "debug",  # 性能统计，使用 debug
    },
    "bt_api_py/functions/utils.py": {
        "lines": [19, 26, 41],
        "imports": [
            "from bt_api_py.logging_factory import get_logger",
            "",
            'logger = get_logger("function")',
        ],
        "replace_type": "error",  # 错误处理
    },
    "bt_api_py/functions/async_send_message.py": {
        "lines": [92],  # 行 105 是测试代码，保留
        "imports": [
            "from bt_api_py.logging_factory import get_logger",
            "",
            'logger = get_logger("function")',
        ],
        "replace_type": "error",
    },
    "bt_api_py/monitoring/elk.py": {
        "lines": [291],
        "imports": [
            "from bt_api_py.logging_factory import get_logger",
            "",
            'logger = get_logger("monitoring")',
        ],
        "replace_type": "warning",
    },
}


def has_logger_import(content: str) -> bool:
    """检查文件是否已经导入了 logger"""
    return "from bt_api_py.logging_factory import get_logger" in content


def add_logger_import(content: str, imports: list) -> str:
    """在文件顶部添加 logger 导入"""
    lines = content.split("\n")

    # 找到最后一个 import 行
    last_import_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            last_import_idx = i

    # 在最后一个 import 后添加 logger 导入
    for j, import_line in enumerate(imports):
        lines.insert(last_import_idx + 1 + j, import_line)

    return "\n".join(lines)


def replace_print_with_logger(line: str, replace_type: str) -> str:
    """将 print 语句替换为 logger 调用"""
    indent = len(line) - len(line.lstrip())
    spaces = " " * indent

    # 提取 print 的内容
    match = re.search(r"print\((.+)\)", line)
    if not match:
        return line

    content = match.group(1)

    # 根据类型选择日志级别
    if replace_type == "error":
        if "{e}" in content or "traceback" in content.lower():
            return f"{spaces}logger.error({content}, exc_info=True)\n"
        else:
            return f"{spaces}logger.error({content})\n"
    elif replace_type == "warning":
        return f"{spaces}logger.warning({content})\n"
    elif replace_type == "debug":
        return f"{spaces}logger.debug({content})\n"
    else:
        return f"{spaces}logger.info({content})\n"


def process_file(file_path: str, config: dict):
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
        content = add_logger_import(content, config["imports"])
        print(f"  ✓ 添加了 logger 导入")

    lines = content.split("\n")

    # 计算偏移量（添加的行数）
    offset = len(config["imports"])

    # 替换指定的 print 语句
    for line_num in config["lines"]:
        adjusted_line_num = line_num + offset - 1  # 转为 0-based index
        if adjusted_line_num < len(lines):
            original_line = lines[adjusted_line_num]
            if "print(" in original_line:
                new_line = replace_print_with_logger(original_line, config["replace_type"])
                lines[adjusted_line_num] = new_line
                print(f"  ✓ 行 {line_num}: 替换 print -> logger.{config['replace_type']}")
            else:
                print(f"  ! 行 {line_num} 不包含 print 语句")

    # 写回文件
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"  ✓ 文件处理完成\n")


def main():
    """主函数"""
    print("=" * 60)
    print("开始第二批 print 语句替换")
    print("=" * 60 + "\n")

    for file_path, config in FILES_TO_PROCESS.items():
        process_file(file_path, config)

    print("=" * 60)
    print("第二批处理完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
