#!/usr/bin/env python3
"""将需要改进的文件分配给10个子代理"""

import os
import json
from pathlib import Path


def read_analysis_file(file_path: str) -> list[dict]:
    """读取代码质量分析文件，提取需要改进的文件列表"""
    files = []
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    i = 127  # 从第127行开始是文件列表
    while i < len(lines):
        line = lines[i].strip()
        if line and line[0].isdigit():
            # 提取文件路径
            parts = line.split(". ", 1)
            if len(parts) == 2:
                file_path = parts[1].strip()

                # 读取后续的元数据
                quality_score = 0.0
                functions = 0
                classes = 0
                type_hints = 0
                docstrings = 0

                for j in range(i + 1, min(i + 4, len(lines))):
                    meta_line = lines[j].strip()
                    if "质量分数" in meta_line:
                        quality_score = float(meta_line.split(":")[1].strip())
                    elif "函数数" in meta_line:
                        parts = meta_line.split(",")
                        for part in parts:
                            if "函数数" in part:
                                functions = int(part.split(":")[1].strip())
                            elif "类数" in part:
                                classes = int(part.split(":")[1].strip())
                    elif "类型注解" in meta_line:
                        parts = meta_line.split(",")
                        for part in parts:
                            if "类型注解" in part:
                                type_hints = int(part.split(":")[1].strip())
                            elif "文档字符串" in part:
                                docstrings = int(part.split(":")[1].strip())

                files.append(
                    {
                        "file_path": file_path,
                        "quality_score": quality_score,
                        "functions": functions,
                        "classes": classes,
                        "type_hints": type_hints,
                        "docstrings": docstrings,
                    }
                )
        i += 1

    return files


def assign_files_to_agents(files: list[dict], num_agents: int = 10) -> dict[int, list[dict]]:
    """将文件分配给多个代理，按目录分组并平衡负载"""

    # 按目录分组
    dir_groups = {}
    for file_info in files:
        file_path = file_info["file_path"]
        dir_name = os.path.dirname(file_path)
        if dir_name not in dir_groups:
            dir_groups[dir_name] = []
        dir_groups[dir_name].append(file_info)

    # 按目录大小排序（大目录优先）
    sorted_dirs = sorted(dir_groups.items(), key=lambda x: len(x[1]), reverse=True)

    # 分配给代理（轮询方式）
    agent_assignments = {i: [] for i in range(num_agents)}
    agent_idx = 0

    for dir_name, dir_files in sorted_dirs:
        for file_info in dir_files:
            agent_assignments[agent_idx].append(file_info)
            agent_idx = (agent_idx + 1) % num_agents

    return agent_assignments


def generate_agent_tasks(agent_assignments: dict[int, list[dict]]) -> None:
    """生成每个代理的任务文件"""
    output_dir = Path("/Users/yunjinqi/Documents/source_code/bt_api_py/agent_tasks")
    output_dir.mkdir(exist_ok=True)

    for agent_id, files in agent_assignments.items():
        task_file = output_dir / f"agent_{agent_id}_tasks.json"

        task_data = {
            "agent_id": agent_id,
            "total_files": len(files),
            "files": files,
            "instructions": {
                "goal": "改进代码质量，添加类型注解和文档字符串",
                "steps": [
                    "1. 为所有函数和类方法添加类型注解（参数和返回值）",
                    "2. 为所有公共函数和类添加Google风格的文档字符串",
                    "3. 为模块添加模块级文档字符串",
                    "4. 确保代码符合项目的代码风格（ruff format）",
                    "5. 保持代码功能不变，只改进质量",
                ],
                "style_guide": {
                    "type_hints": "使用Python 3.11+类型语法（list[str]而不是List[str]）",
                    "docstrings": "Google风格，包含Args、Returns、Raises等部分",
                    "quotes": "使用双引号",
                    "line_length": "100字符",
                },
            },
        }

        with open(task_file, "w", encoding="utf-8") as f:
            json.dump(task_data, f, indent=2, ensure_ascii=False)

        print(f"代理 {agent_id}: {len(files)} 个文件 -> {task_file}")


def main():
    analysis_file = "/Users/yunjinqi/Documents/source_code/bt_api_py/code_quality_analysis.txt"

    print("读取分析文件...")
    files = read_analysis_file(analysis_file)
    print(f"找到 {len(files)} 个需要改进的文件")

    print("\n分配文件给10个代理...")
    agent_assignments = assign_files_to_agents(files, num_agents=10)

    print("\n生成任务文件...")
    generate_agent_tasks(agent_assignments)

    # 打印统计信息
    print("\n\n代理任务分配统计:")
    print("=" * 80)
    for agent_id in sorted(agent_assignments.keys()):
        files = agent_assignments[agent_id]
        print(f"\n代理 {agent_id}:")
        print(f"  总文件数: {len(files)}")

        # 按目录分组统计
        dir_count = {}
        for f in files:
            dir_name = os.path.dirname(f["file_path"])
            dir_count[dir_name] = dir_count.get(dir_name, 0) + 1

        # 显示前5个目录
        sorted_dirs = sorted(dir_count.items(), key=lambda x: x[1], reverse=True)
        print(f"  主要目录:")
        for dir_name, count in sorted_dirs[:5]:
            print(f"    - {dir_name}: {count} 个文件")


if __name__ == "__main__":
    main()
