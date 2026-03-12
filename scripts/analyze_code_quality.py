#!/usr/bin/env python3
"""分析代码质量，找出需要改进的文件"""

import os
import re
from pathlib import Path
from typing import Dict, List, Tuple


def analyze_file_quality(file_path: str) -> Dict:
    """分析单个文件的质量指标"""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    total_lines = len(content.split("\n"))

    # 统计类型注解
    type_hints = len(
        re.findall(r":\s*(?:str|int|float|bool|list|dict|tuple|Optional|Union|Any|None)", content)
    )

    # 统计文档字符串
    docstrings = len(re.findall(r'"""[\s\S]*?"""', content))

    # 统计函数数量
    functions = len(re.findall(r"^\s*def\s+\w+", content, re.MULTILINE))

    # 统计类数量
    classes = len(re.findall(r"^\s*class\s+\w+", content, re.MULTILINE))

    # 检查是否有模块级文档
    has_module_doc = bool(re.match(r'^[\s]*"""', content))

    # 计算质量分数 (0-100)
    quality_score = 0
    if functions > 0 or classes > 0:
        # 类型注解覆盖率
        type_coverage = min(100, (type_hints / max(1, functions)) * 50)
        # 文档字符串覆盖率
        doc_coverage = min(100, (docstrings / max(1, functions + classes)) * 50)
        quality_score = (type_coverage + doc_coverage) / 2

    return {
        "file_path": file_path,
        "total_lines": total_lines,
        "functions": functions,
        "classes": classes,
        "type_hints": type_hints,
        "docstrings": docstrings,
        "has_module_doc": has_module_doc,
        "quality_score": quality_score,
    }


def find_python_files(root_dir: str) -> List[str]:
    """找出所有Python文件"""
    python_files = []
    for root, dirs, files in os.walk(root_dir):
        # 跳过__pycache__和其他不需要的目录
        dirs[:] = [d for d in dirs if d not in ["__pycache__", ".git", "build", "dist", "htmlcov"]]
        for file in files:
            if file.endswith(".py") and not file.startswith("."):
                python_files.append(os.path.join(root, file))
    return python_files


def main():
    root_dir = "/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py"
    python_files = find_python_files(root_dir)

    print(f"找到 {len(python_files)} 个Python文件")

    # 分析所有文件
    results = []
    for file_path in python_files:
        try:
            result = analyze_file_quality(file_path)
            results.append(result)
        except Exception as e:
            print(f"分析 {file_path} 时出错: {e}")

    # 按质量分数排序
    results.sort(key=lambda x: x["quality_score"])

    # 找出需要改进的文件（质量分数低于50）
    low_quality_files = [r for r in results if r["quality_score"] < 50]

    print(f"\n需要改进的文件（质量分数<50）: {len(low_quality_files)}")

    # 按目录分组
    dir_groups = {}
    for result in low_quality_files:
        dir_name = os.path.dirname(result["file_path"])
        rel_dir = os.path.relpath(dir_name, root_dir)
        if rel_dir not in dir_groups:
            dir_groups[rel_dir] = []
        dir_groups[rel_dir].append(result)

    print("\n按目录分组的文件分布:")
    for dir_name in sorted(dir_groups.keys(), key=lambda x: len(dir_groups[x]), reverse=True):
        print(f"{dir_name}: {len(dir_groups[dir_name])} 个文件")

    # 输出需要改进的文件列表（按优先级）
    print("\n\n需要改进的文件列表（按优先级排序）:")
    for i, result in enumerate(low_quality_files[:50], 1):
        rel_path = os.path.relpath(result["file_path"], root_dir)
        print(
            f"{i:3d}. {rel_path:<80} (质量分数: {result['quality_score']:.1f}, 函数: {result['functions']}, 类型注解: {result['type_hints']})"
        )

    # 将完整列表保存到文件
    output_file = "/Users/yunjinqi/Documents/source_code/bt_api_py/code_quality_analysis.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"代码质量分析报告\n")
        f.write(f"{'=' * 80}\n\n")
        f.write(f"总文件数: {len(results)}\n")
        f.write(f"需要改进的文件数: {len(low_quality_files)}\n\n")

        f.write(f"\n按目录分组的文件分布:\n")
        for dir_name in sorted(dir_groups.keys(), key=lambda x: len(dir_groups[x]), reverse=True):
            f.write(f"{dir_name}: {len(dir_groups[dir_name])} 个文件\n")

        f.write(f"\n\n需要改进的文件列表:\n")
        for i, result in enumerate(low_quality_files, 1):
            rel_path = os.path.relpath(result["file_path"], root_dir)
            f.write(f"{i:3d}. {rel_path}\n")
            f.write(f"     质量分数: {result['quality_score']:.1f}\n")
            f.write(f"     函数数: {result['functions']}, 类数: {result['classes']}\n")
            f.write(
                f"     类型注解: {result['type_hints']}, 文档字符串: {result['docstrings']}\n\n"
            )

    print(f"\n完整分析报告已保存到: {output_file}")


if __name__ == "__main__":
    main()
