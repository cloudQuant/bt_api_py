#!/usr/bin/env python3
"""
提取所有交易所的 capabilities 并生成能力矩阵
"""

import ast
import re
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Set, Tuple


def extract_capabilities_from_file(file_path: Path) -> Tuple[str, List[str]]:
    """从文件中提取 capabilities"""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 提取交易所名称和类型
    path_parts = file_path.parts
    exchange_name = None
    asset_type = "UNKNOWN"

    # 从路径中提取交易所名称
    for i, part in enumerate(path_parts):
        if part.startswith("live_") and part != "live_":
            exchange_name = part.replace("live_", "").upper()
            break

    # 从文件名中提取资产类型
    filename = file_path.name
    if "spot" in filename:
        asset_type = "SPOT"
    elif "swap" in filename or "futures" in filename:
        asset_type = "SWAP"
    elif "margin" in filename:
        asset_type = "MARGIN"
    elif "option" in filename:
        asset_type = "OPTION"

    if not exchange_name:
        # 尝试从文件名中提取
        match = re.search(r"live_([a-z_]+)_feed", filename)
        if match:
            exchange_name = match.group(1).upper()
        else:
            return None, []

    # 查找 _capabilities 方法
    capabilities = []

    # 使用正则表达式查找 _capabilities 方法
    pattern = r"def _capabilities\([^)]*\)[^:]*:\s*\n\s*return\s*\{([^}]+)\}"
    matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL)

    if matches:
        for match in matches:
            # 提取 Capability.xxx
            cap_pattern = r"Capability\.([A-Z_]+)"
            caps = re.findall(cap_pattern, match)
            capabilities.extend(caps)

    # 去重并排序
    capabilities = sorted(list(set(capabilities)))

    return f"{exchange_name}___{asset_type}", capabilities


def scan_all_exchanges():
    """扫描所有交易所文件"""
    feeds_dir = Path("bt_api_py/feeds")

    # 存储所有交易所的能力
    exchange_caps: Dict[str, Set[str]] = defaultdict(set)

    # 查找所有 Python 文件
    for py_file in feeds_dir.rglob("*.py"):
        if "__pycache__" in str(py_file) or "test" in str(py_file):
            continue

        key, caps = extract_capabilities_from_file(py_file)
        if key and caps:
            exchange_caps[key].update(caps)

    # 转换为排序后的字典
    result = {}
    for key in sorted(exchange_caps.keys()):
        result[key] = sorted(list(exchange_caps[key]))

    return result


def generate_markdown_table(exchange_caps: Dict[str, List[str]]) -> str:
    """生成 Markdown 表格"""
    # 收集所有可能的 capabilities
    all_caps = set()
    for caps in exchange_caps.values():
        all_caps.update(caps)

    all_caps = sorted(list(all_caps))

    # 生成表头
    header = "| 交易所 | 资产类型 | " + " | ".join(all_caps) + " |"
    separator = "|" + "---|" * (2 + len(all_caps))

    # 生成数据行
    rows = []
    for key in sorted(exchange_caps.keys()):
        exchange, asset_type = key.split("___")
        caps_set = set(exchange_caps[key])

        row = f"| {exchange} | {asset_type} |"
        for cap in all_caps:
            symbol = "✓" if cap in caps_set else "✗"
            row += f" {symbol} |"
        rows.append(row)

    return header + "\n" + separator + "\n" + "\n".join(rows)


def generate_csv(exchange_caps: Dict[str, List[str]]) -> str:
    """生成 CSV 格式"""
    # 收集所有可能的 capabilities
    all_caps = set()
    for caps in exchange_caps.values():
        all_caps.update(caps)

    all_caps = sorted(list(all_caps))

    # 生成表头
    header = "Exchange,AssetType," + ",".join(all_caps)

    # 生成数据行
    rows = []
    for key in sorted(exchange_caps.keys()):
        exchange, asset_type = key.split("___")
        caps_set = set(exchange_caps[key])

        values = [exchange, asset_type]
        for cap in all_caps:
            values.append("1" if cap in caps_set else "0")

        rows.append(",".join(values))

    return header + "\n" + "\n".join(rows)


def generate_summary(exchange_caps: Dict[str, List[str]]) -> str:
    """生成统计摘要"""
    # 统计交易所数量
    exchanges = set()
    for key in exchange_caps.keys():
        exchange = key.split("___")[0]
        exchanges.add(exchange)

    # 统计各能力的支持情况
    cap_support = defaultdict(int)
    for caps in exchange_caps.values():
        for cap in caps:
            cap_support[cap] += 1

    # 统计资产类型
    asset_types = defaultdict(int)
    for key in exchange_caps.keys():
        asset_type = key.split("___")[1]
        asset_types[asset_type] += 1

    summary = f"""# 交易所能力矩阵

## 统计摘要

- **总交易所数量**: {len(exchanges)}
- **总实现数量**: {len(exchange_caps)} (包含不同资产类型)

### 按资产类型分布

"""

    for asset_type, count in sorted(asset_types.items()):
        summary += f"- **{asset_type}**: {count} 个实现\n"

    summary += "\n### 按能力支持排名\n\n"

    for cap, count in sorted(cap_support.items(), key=lambda x: x[1], reverse=True):
        percentage = (count / len(exchange_caps)) * 100
        summary += f"- **{cap}**: {count} 个交易所支持 ({percentage:.1f}%)\n"

    return summary


def main():
    """主函数"""
    print("正在扫描所有交易所实现...")
    exchange_caps = scan_all_exchanges()

    print(f"找到 {len(exchange_caps)} 个交易所实现")

    # 生成 Markdown 表格
    print("\n生成 Markdown 表格...")
    md_table = generate_markdown_table(exchange_caps)

    # 生成 CSV
    print("生成 CSV 文件...")
    csv_data = generate_csv(exchange_caps)

    # 生成摘要
    print("生成统计摘要...")
    summary = generate_summary(exchange_caps)

    # 组合完整文档
    full_doc = summary + "\n\n## 详细能力矩阵\n\n" + md_table

    # 保存文件
    output_dir = Path("_bmad-output/planning-artifacts")
    output_dir.mkdir(parents=True, exist_ok=True)

    md_file = output_dir / "exchange-capability-matrix.md"
    csv_file = output_dir / "exchange-capability-matrix.csv"

    with open(md_file, "w", encoding="utf-8") as f:
        f.write(full_doc)
    print(f"\n✓ Markdown 文件已保存: {md_file}")

    with open(csv_file, "w", encoding="utf-8") as f:
        f.write(csv_data)
    print(f"✓ CSV 文件已保存: {csv_file}")

    print("\n完成！")


if __name__ == "__main__":
    main()
