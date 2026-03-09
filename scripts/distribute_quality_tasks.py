#!/usr/bin/env python3
"""
分配代码质量改进任务给多个代理

根据代码质量检查报告，将需要改进的文件分配给10个代理，
每个代理负责改进一批文件的类型注释和Google风格文档注释。
"""

import json
import re
from collections import defaultdict
from pathlib import Path


def analyze_report(report_file: str) -> dict[str, list[str]]:
    """分析代码质量报告，提取需要改进的文件

    Args:
        report_file: 报告文件路径

    Returns:
        字典，键为文件路径，值为该文件的问题列表
    """
    with open(report_file, encoding="utf-8") as f:
        content = f.read()

    files_needing_improvement = {}

    # 提取缺失类型注释的文件和问题
    type_hint_section = re.search(
        r"缺失类型注释:\n-+\n(.+?)\n\n\n缺失文档注释:", content, re.DOTALL
    )
    if type_hint_section:
        type_hints_text = type_hint_section.group(1)
        current_file = None
        for line in type_hints_text.split("\n"):
            line = line.rstrip()
            if not line:
                continue
            # 文件路径行：不以空格开头，且包含bt_api_py
            if not line.startswith(" ") and "bt_api_py" in line:
                current_file = line.strip()
                if current_file not in files_needing_improvement:
                    files_needing_improvement[current_file] = []
            # 问题行：以空格开头，包含Line
            elif line.strip().startswith("Line") and current_file:
                files_needing_improvement[current_file].append(f"TypeHint: {line.strip()}")

    # 提取缺失文档注释的文件和问题
    docstring_section = re.search(r"缺失文档注释:\n-+\n(.+)", content, re.DOTALL)
    if docstring_section:
        docstrings_text = docstring_section.group(1)
        current_file = None
        for line in docstrings_text.split("\n"):
            line = line.rstrip()
            if not line:
                continue
            # 文件路径行：不以空格开头，且包含bt_api_py
            if not line.startswith(" ") and "bt_api_py" in line:
                current_file = line.strip()
                if current_file not in files_needing_improvement:
                    files_needing_improvement[current_file] = []
            # 问题行：以空格开头，包含Line
            elif line.strip().startswith("Line") and current_file:
                files_needing_improvement[current_file].append(f"Docstring: {line.strip()}")

    return files_needing_improvement


def distribute_files_to_agents(
    files_needing_improvement: dict[str, list[str]], num_agents: int = 10
) -> list[dict[str, list[str]]]:
    """将文件分配给多个代理

    Args:
        files_needing_improvement: 需要改进的文件字典
        num_agents: 代理数量

    Returns:
        列表，每个元素是一个代理的任务字典
    """
    # 按目录分组
    dir_files = defaultdict(list)
    for filepath, issues in files_needing_improvement.items():
        parts = Path(filepath).parts
        if len(parts) >= 3 and parts[1] in [
            "containers",
            "feeds",
            "exchange_registers",
            "errors",
            "functions",
        ]:
            dir_path = "/".join(parts[:3])
        else:
            dir_path = "/".join(parts[:2])
        dir_files[dir_path].append((filepath, issues))

    # 将目录分配给代理
    agent_tasks = [{} for _ in range(num_agents)]
    sorted_dirs = sorted(dir_files.items(), key=lambda x: len(x[1]), reverse=True)

    for i, (dir_path, files_with_issues) in enumerate(sorted_dirs):
        agent_idx = i % num_agents
        for filepath, issues in files_with_issues:
            agent_tasks[agent_idx][filepath] = issues

    return agent_tasks


def save_agent_tasks(agent_tasks: list[dict[str, list[str]]], output_dir: str) -> None:
    """保存每个代理的任务列表

    Args:
        agent_tasks: 代理任务列表
        output_dir: 输出目录
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    for i, tasks in enumerate(agent_tasks, 1):
        if not tasks:
            continue

        # 保存为JSON
        task_file = output_path / f"agent_{i}_tasks.json"
        with open(task_file, "w", encoding="utf-8") as f:
            json.dump(tasks, f, indent=2, ensure_ascii=False)

        # 保存为可读的文本文件
        readable_file = output_path / f"agent_{i}_files.txt"
        with open(readable_file, "w", encoding="utf-8") as f:
            f.write(f"代理 {i} 需要改进的文件 (共 {len(tasks)} 个)\n")
            f.write("=" * 80 + "\n\n")

            # 按目录分组显示
            dir_files = defaultdict(list)
            for filepath, issues in tasks.items():
                parts = Path(filepath).parts
                if len(parts) >= 3 and parts[1] in [
                    "containers",
                    "feeds",
                    "exchange_registers",
                    "errors",
                    "functions",
                ]:
                    dir_path = "/".join(parts[:3])
                else:
                    dir_path = "/".join(parts[:2])
                dir_files[dir_path].append((filepath, issues))

            for dir_path, files_with_issues in sorted(dir_files.items()):
                f.write(f"\n{dir_path}:\n")
                f.write("-" * 80 + "\n")
                for filepath, issues in files_with_issues:
                    f.write(f"  {filepath}\n")
                    for issue in issues[:5]:  # 只显示前5个问题
                        f.write(f"    - {issue}\n")
                    if len(issues) > 5:
                        f.write(f"    ... 还有 {len(issues) - 5} 个问题\n")

        print(f"代理 {i}: {len(tasks)} 个文件 -> {readable_file}")


def main():
    report_file = "/tmp/code_quality_report.txt"
    output_dir = "scripts/agent_tasks"

    print("正在分析代码质量报告...")
    files_needing_improvement = analyze_report(report_file)
    print(f"找到 {len(files_needing_improvement)} 个需要改进的文件\n")

    print("正在将任务分配给10个代理...")
    agent_tasks = distribute_files_to_agents(files_needing_improvement, num_agents=10)

    print("\n正在保存任务列表...")
    save_agent_tasks(agent_tasks, output_dir)

    print(f"\n✅ 任务分配完成！任务文件保存在: {output_dir}")
    print("\n每个代理的任务文件:")
    print("  - agent_N_files.txt: 可读的任务列表")
    print("  - agent_N_tasks.json: JSON格式的详细任务")


if __name__ == "__main__":
    main()
