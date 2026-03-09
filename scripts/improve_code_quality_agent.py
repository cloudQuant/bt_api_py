#!/usr/bin/env python3
"""
单个代理的代码质量改进脚本

用于改进指定文件的类型注释和Google风格文档注释。
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="改进指定文件的代码质量")
    parser.add_argument("task_file", help="任务JSON文件路径")
    parser.add_argument("--dry-run", action="store_true", help="仅显示将要处理的文件，不实际修改")

    args = parser.parse_args()

    # 读取任务文件
    with open(args.task_file, encoding="utf-8") as f:
        tasks = json.load(f)

    print(f"需要改进的文件数量: {len(tasks)}")
    print("\n文件列表:")

    for i, filepath in enumerate(sorted(tasks.keys()), 1):
        issues = tasks[filepath]
        print(f"{i:3d}. {filepath}")
        if args.dry_run:
            print(f"     问题数: {len(issues)}")
            for issue in issues[:3]:
                print(f"       - {issue}")
            if len(issues) > 3:
                print(f"       ... 还有 {len(issues) - 3} 个问题")

    if args.dry_run:
        print("\n[DRY RUN] 以上文件需要改进")
        return

    print("\n开始改进代码质量...")
    print("改进策略:")
    print("1. 为所有函数和方法添加完整的类型注释（参数和返回值）")
    print("2. 为所有公共函数和方法添加Google风格的文档注释")
    print("3. 文档注释应包含:")
    print("   - 简短描述")
    print("   - Args部分（如果有参数）")
    print("   - Returns部分（如果有返回值）")
    print("   - Raises部分（如果可能抛出异常）")
    print("\n注意:")
    print("- 保持代码风格一致")
    print("- 不要改变现有逻辑")
    print("- 使用Python 3.11+类型语法（如 list[str] 而非 List[str]）")
    print("- 参考 bt_api_py/AGENTS.md 中的代码风格指南")


if __name__ == "__main__":
    main()
