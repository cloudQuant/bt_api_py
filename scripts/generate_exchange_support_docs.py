#!/usr/bin/env python3
"""Generate exchange support documentation from a single metadata file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "docs" / "data" / "exchange_support_matrix.json"


TARGETS = (
    (ROOT / "README.md", "EXCHANGE_SUPPORT_OVERVIEW", "readme"),
    (ROOT / "docs" / "index.md", "EXCHANGE_SUPPORT_OVERVIEW", "docs_index"),
    (ROOT / "docs" / "project-overview.md", "EXCHANGE_SUPPORT_OVERVIEW", "project_overview"),
    (ROOT / "docs" / "exchanges" / "EXCHANGE_STATUS.md", "EXCHANGE_SUPPORT_STATUS", "status"),
)


def load_data() -> dict:
    with DATA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(
        [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["--------"] * len(headers)) + " |",
            *body,
        ]
    )


def render_readme(data: dict) -> str:
    meta = data["metadata"]
    full = data["fully_supported"]
    implemented = data["implemented_api"]
    summary = data["summary"]
    framework = data["framework_ready"]

    full_rows = [
        [
            f"**{item['name']}**",
            item["codes"],
            item["spot"],
            item["contract"],
            item["options"],
            item["stocks"],
            item["test_status"],
            item["note"],
        ]
        for item in full
    ]
    implemented_rows = [
        [
            f"**{item['name']}**",
            item["market_type"],
            item["status"],
            item["test_status"],
            item["note"],
        ]
        for item in implemented
    ]
    return "\n".join(
        [
            f"> 测试状态建议通过 `{meta['verification_command']}` 复核，当前口径更新于 {meta['last_updated']}。",
            "",
            "### ✅ 已完整支持（REST + WebSocket + 测试通过）",
            "",
            _render_table(
                ["交易所", "代码", "现货", "合约", "期权", "股票", "测试状态", "说明"],
                full_rows,
            ),
            "",
            "### 🔧 已实现 API（仍需继续验证或补齐能力）",
            "",
            _render_table(
                ["交易所", "类型", "当前状态", "测试状态", "备注"],
                implemented_rows,
            ),
            "",
            "### 📋 已注册（基础框架就绪）",
            "",
            f"{framework['count_label']} 个交易所已完成注册或基础框架接入，但还需要继续补实现、测试或文档后，再提升对外状态。",
            "",
            f"> **总计**: {len(full)} 个完整支持 + {len(implemented)} 个已实现 API + {summary['framework_ready_label']} 个已注册 = **{summary['total_supported_label']} 个交易所**",
            ">",
            "> **说明**: 该分级采用保守口径；只有 REST、WebSocket 和测试资产同时满足时，才会提升到“完整支持”。",
        ]
    )


def render_docs_index(data: dict) -> str:
    full = data["fully_supported"]
    implemented = data["implemented_api"]
    summary = data["summary"]

    full_rows = [
        [f"**{item['name']}**", item["codes"], item["spot"], item["contract"], item["options"], item["stocks"], item["test_status"]]
        for item in full
    ]
    implemented_rows = [
        [item["name"], item["market_type"], item["test_status"], item["note"]]
        for item in implemented
    ]
    return "\n".join(
        [
            "### ✅ 已完整支持（REST + WebSocket + 测试通过）",
            "",
            _render_table(["交易所", "代码", "现货", "合约", "期权", "股票", "测试状态"], full_rows),
            "",
            "### 🔧 已实现 API（仍需继续验证或补齐能力）",
            "",
            _render_table(["交易所", "类型", "测试状态", "备注"], implemented_rows),
            "",
            "### 📋 已注册（基础框架就绪）",
            "",
            f"{summary['framework_ready_label']} 个交易所已完成注册和基础框架接入，但仍需继续补实现、测试或文档。",
        ]
    )


def render_project_overview(data: dict) -> str:
    full_names = "、".join(item["name"] for item in data["fully_supported"])
    implemented_names = "、".join(item["name"] for item in data["implemented_api"])
    framework = data["framework_ready"]
    return "\n".join(
        [
            "### 1. 多交易所统一接口",
            "",
            f"**支持{data['summary']['total_supported_label']}交易所：**",
            "",
            "**✅ 完整支持（REST + WebSocket + 测试通过）：**",
            f"- {full_names}",
            "",
            "**🔧 已实现 API（仍需继续验证或补齐能力）：**",
            f"- {implemented_names}",
            "",
            "**📋 已注册框架：**",
            f"- {framework['count_label']} 个交易所完成基础框架和注册，等待继续补实现、测试或文档",
        ]
    )


def render_status(data: dict) -> str:
    meta = data["metadata"]
    full = data["fully_supported"]
    implemented = data["implemented_api"]
    summary = data["summary"]
    framework = data["framework_ready"]

    full_rows = [
        [
            f"**{item['name']}**",
            item["market_type"],
            "✅",
            "✅",
            item["test_status"],
            item["note"],
        ]
        for item in full
    ]
    implemented_rows = [
        [
            f"**{item['name']}**",
            item["market_type"],
            item["status"],
            item["test_status"],
            item["note"],
        ]
        for item in implemented
    ]
    return "\n".join(
        [
            "# 交易所实现状态",
            "",
            f"**最后更新：** {meta['last_updated']}  ",
            f"**版本：** {meta['version']}",
            "",
            "本文档由 `docs/data/exchange_support_matrix.json` 生成，用于统一 README、文档首页和状态页的交易所支持口径。",
            "",
            "## 状态口径",
            "",
            f"- **验证命令**：`{meta['verification_command']}`",
            f"- **状态策略**：{meta['status_policy']}",
            "",
            "## ✅ 已完整支持",
            "",
            _render_table(["交易所", "类型", "REST", "WebSocket", "测试状态", "备注"], full_rows),
            "",
            "## 🔧 已实现 API（仍需继续验证或补齐能力）",
            "",
            _render_table(["交易所", "类型", "当前状态", "测试状态", "备注"], implemented_rows),
            "",
            "## 📋 已注册（基础框架就绪）",
            "",
            f"- 数量：{framework['count_label']}",
            f"- 说明：{framework['note']}",
            "",
            "## 📈 统计总览",
            "",
            _render_table(
                ["状态", "数量", "说明"],
                [
                    ["✅ 完整支持", str(len(full)), "REST、WebSocket 和测试资产均已具备"],
                    ["🔧 已实现 API", str(len(implemented)), "已有实现，但仍需继续验证、补测试或补 WebSocket 能力"],
                    ["📋 已注册", summary["framework_ready_label"], "已注册或基础框架接入，等待继续完善"],
                    ["总计", summary["total_supported_label"], "当前仓库对外声明支持的交易所总量"]
                ],
            ),
            "",
            "## 维护说明",
            "",
            "1. 修改 `docs/data/exchange_support_matrix.json`。",
            "2. 运行 `python scripts/generate_exchange_support_docs.py`。",
            "3. 提交生成后的 README 和文档页。",
        ]
    )


RENDERERS = {
    "readme": render_readme,
    "docs_index": render_docs_index,
    "project_overview": render_project_overview,
    "status": render_status,
}


def replace_marker_block(text: str, marker: str, body: str) -> str:
    begin = f"<!-- BEGIN GENERATED:{marker} -->"
    end = f"<!-- END GENERATED:{marker} -->"
    if begin not in text or end not in text:
        raise ValueError(f"Missing marker block {marker}")
    prefix, rest = text.split(begin, maxsplit=1)
    _, suffix = rest.split(end, maxsplit=1)
    return f"{prefix}{begin}\n{body}\n{end}{suffix}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if generated content is out of date.")
    args = parser.parse_args()

    data = load_data()
    dirty = False
    for path, marker, renderer_name in TARGETS:
        original = path.read_text(encoding="utf-8")
        body = RENDERERS[renderer_name](data)
        updated = replace_marker_block(original, marker, body)
        if updated != original:
            dirty = True
            if not args.check:
                path.write_text(updated, encoding="utf-8")

    if args.check and dirty:
        print("Exchange support documentation is out of date.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
