#!/usr/bin/env python3
"""补齐 bt_api_* 子模块缺失的 bt_api.plugins entry-points 与 plugin.py。

对每个缺失 `[project.entry-points."bt_api.plugins"]` 的交易所插件子模块：
1. 从 register 文件解析 register 函数名、exchange 标识、函数签名；
2. 生成/修正 plugin.py（统一 register_plugin 格式）；
3. 在 pyproject.toml 追加 entry-points 段。

用法:
    python scripts/fix_plugin_entries.py --dry-run
    python scripts/fix_plugin_entries.py
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BT_API_DIR = ROOT / "bt_api"

EXCLUDED = {"bt_api_base", "bt_api_btbns"}

REGISTER_FUNC_RE = re.compile(r"def\s+(register(?:_\w+)?)\s*\(([^)]*)\)")
REGISTER_FEED_RE = re.compile(r'register_feed\(\s*["\']([^"\']+)["\']')


def find_register_file(pkg_dir: Path) -> Path | None:
    """定位 register 文件。"""
    for pattern in ("registry_registration.py", "exchange_registers/register_*.py"):
        matches = sorted(pkg_dir.glob(f"src/**/{pattern}"))
        if matches:
            return matches[0]
    return None


def parse_register_file(register_file: Path, pkg_name: str) -> dict:
    """解析 register 文件，返回 register 函数信息。"""
    text = register_file.read_text(encoding="utf-8")
    func_match = REGISTER_FUNC_RE.search(text)
    if func_match:
        func_name = func_match.group(1)
        takes_registry = bool(func_match.group(2).strip())
    else:
        func_name = "register"
        takes_registry = False

    exchange = ""
    feed_match = REGISTER_FEED_RE.search(text)
    if feed_match:
        exchange = feed_match.group(1)
    if not exchange:
        short = pkg_name.replace("bt_api_", "").upper()
        exchange = f"{short}___SPOT"
    asset_type = exchange.rsplit("___", 1)[-1] if "___" in exchange else "SPOT"

    return {
        "func_name": func_name,
        "takes_registry": takes_registry,
        "exchange": exchange,
        "asset_type": asset_type,
    }


def has_version(pkg_dir: Path, pkg_name: str) -> bool:
    init_file = pkg_dir / "src" / pkg_name / "__init__.py"
    return init_file.exists() and "__version__" in init_file.read_text(encoding="utf-8")


def register_module(register_file: Path, pkg_name: str) -> str:
    """从 register 文件路径推导其 Python 模块路径（src 包内相对路径）。"""
    parts = list(register_file.parts)
    # 找最后一个 pkg_name（src/<pkg_name>/ 内的那个），取其后的模块路径
    idx = max(i for i, p in enumerate(parts) if p == pkg_name)
    sub = parts[idx + 1 :]
    return ".".join(p.replace(".py", "") for p in sub)


def generate_plugin_py(pkg_dir: Path, pkg_name: str, info: dict) -> str:
    """生成 register_plugin 格式的 plugin.py。"""
    version_line = f"from {pkg_name} import __version__\n" if has_version(pkg_dir, pkg_name) else ""
    call = f"{info['func_name']}(registry)" if info["takes_registry"] else f"{info['func_name']}()"
    version_field = "__version__" if version_line else '"0.1.0"'
    reg_mod = info["reg_module"]
    return (
        '"""Module-level docstring."""\n'
        "# generated, verify register call\n"
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from typing import TYPE_CHECKING, Any\n"
        "\n"
        "from bt_api_base.plugins.protocol import PluginInfo\n"
        "\n"
        f"from {pkg_name}.{reg_mod} import {info['func_name']}\n"
        f"{version_line}\n"
        "if TYPE_CHECKING:\n"
        "    from bt_api_base.registry import ExchangeRegistry\n"
        "\n"
        "\n"
        "def register_plugin(registry: ExchangeRegistry, runtime_factory: Any) -> PluginInfo:\n"
        '    """register_plugin function"""\n'
        f"    {call}\n"
        "\n"
        "    return PluginInfo(\n"
        f'        name="{pkg_name}",\n'
        f"        version={version_field},\n"
        '        core_requires=">=0.15,<1.0",\n'
        f'        supported_exchanges=("{info["exchange"]}",),\n'
        f'        supported_asset_types=("{info["asset_type"]}",),\n'
        "    )\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    processed = []
    for pkg_dir in sorted(BT_API_DIR.iterdir()):
        pkg_name = pkg_dir.name
        if not pkg_name.startswith("bt_api_") or pkg_name in EXCLUDED:
            continue
        pyproject = pkg_dir / "pyproject.toml"
        if not pyproject.exists():
            continue
        if 'entry-points."bt_api.plugins"' in pyproject.read_text(encoding="utf-8"):
            continue

        register_file = find_register_file(pkg_dir)
        if register_file is None:
            print(f"[skip] {pkg_name}: no register file found")
            continue

        info = parse_register_file(register_file, pkg_name)
        info["reg_module"] = register_module(register_file, pkg_name)
        short_name = pkg_name.replace("bt_api_", "")

        plugin_path = pkg_dir / "src" / pkg_name / "plugin.py"
        if not plugin_path.exists() or "def register_plugin" not in plugin_path.read_text(
            encoding="utf-8"
        ):
            content = generate_plugin_py(pkg_dir, pkg_name, info)
            processed.append((pkg_name, "plugin.py"))
            if not args.dry_run:
                plugin_path.parent.mkdir(parents=True, exist_ok=True)
                plugin_path.write_text(content, encoding="utf-8")

        entry = (
            f'\n[project.entry-points."bt_api.plugins"]\n'
            f'{short_name} = "{pkg_name}.plugin:register_plugin"\n'
        )
        processed.append((pkg_name, "pyproject.toml entry-points"))
        if not args.dry_run:
            with pyproject.open("a", encoding="utf-8") as fh:
                fh.write(entry)

    print(f"共 {len(processed)} 个改动（dry-run={args.dry_run}）")
    for pkg, kind in processed:
        print(f"  - {pkg}: {kind}")


if __name__ == "__main__":
    main()
