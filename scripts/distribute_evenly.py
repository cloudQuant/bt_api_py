#!/usr/bin/env python3
"""
更均匀地分配代码质量改进任务给10个代理

使用轮询方式分配文件，确保每个代理的文件数量基本相同。
"""

import json
from pathlib import Path


def distribute_evenly():
    """均匀分配任务给10个代理"""
    # 读取已有的任务文件
    all_tasks = {}
    for i in range(1, 11):
        task_file = Path(f"scripts/agent_tasks/agent_{i}_tasks.json")
        if task_file.exists():
            with open(task_file, encoding="utf-8") as f:
                tasks = json.load(f)
                all_tasks.update(tasks)

    print(f"总共需要改进的文件: {len(all_tasks)} 个\n")

    # 按文件路径排序，确保相似的文件分配给不同的代理
    sorted_files = sorted(all_tasks.items())

    # 使用轮询方式分配给10个代理
    agent_tasks = [{} for _ in range(10)]
    for i, (filepath, issues) in enumerate(sorted_files):
        agent_idx = i % 10
        agent_tasks[agent_idx][filepath] = issues

    # 保存新的任务文件
    output_dir = Path("scripts/agent_tasks_even")
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, tasks in enumerate(agent_tasks, 1):
        if not tasks:
            continue

        # 统计目录分布
        dir_count = {}
        for filepath in tasks.keys():
            parts = Path(filepath).parts
            if len(parts) >= 2:
                dir_name = parts[1]
                dir_count[dir_name] = dir_count.get(dir_name, 0) + 1

        # 保存JSON
        json_file = output_dir / f"agent_{i}_tasks.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(tasks, f, indent=2, ensure_ascii=False)

        # 保存可读文本
        txt_file = output_dir / f"agent_{i}_files.txt"
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write(f"代理 {i} 需要改进的文件 (共 {len(tasks)} 个)\n")
            f.write("=" * 80 + "\n\n")

            f.write("目录分布:\n")
            for dir_name, count in sorted(dir_count.items(), key=lambda x: x[1], reverse=True):
                f.write(f"  - {dir_name}: {count} 个文件\n")
            f.write("\n")

            f.write("文件列表:\n")
            f.write("-" * 80 + "\n")
            for filepath, issues in sorted(tasks.items()):
                f.write(f"\n{filepath}\n")
                for issue in issues[:3]:  # 只显示前3个问题
                    f.write(f"  - {issue}\n")
                if len(issues) > 3:
                    f.write(f"  ... 还有 {len(issues) - 3} 个问题\n")

        print(f"代理 {i:2d}: {len(tasks):3d} 个文件 -> {txt_file}")

    print(f"\n✅ 任务文件已保存到: {output_dir}")
    print("\n文件分配情况:")
    for i, tasks in enumerate(agent_tasks, 1):
        if tasks:
            print(f"  代理 {i:2d}: {len(tasks):3d} 个文件")


if __name__ == "__main__":
    distribute_evenly()
