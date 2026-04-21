#!/usr/bin/env python3
"""
Generate a minimal bt_api plugin package for a single exchange.

Usage:
    python scripts/generate_plugins/generate_exchange_plugin.py HTX "HTX (Huobi)" "binance,okx" "SPOT,MARGIN,USDT_SWAP,COIN_SWAP,OPTION"
"""

import os
import sys
from pathlib import Path

EXCHANGE_REGISTERS_PATH = Path("bt_api_py/exchange_registers")
PACKAGES_PATH = Path("packages")
HTX_EXCHANGES = [
    ("HTX___SPOT", "spot", "HtxSpotRequestData"),
    ("HTX___MARGIN", "margin", "HtxMarginRequestData"),
    ("HTX___USDT_SWAP", "usdt_swap", "HtxUsdtSwapRequestData"),
    ("HTX___COIN_SWAP", "coin_swap", "HtxCoinSwapRequestData"),
    ("HTX___OPTION", "option", "HtxOptionRequestData"),
]


def to_snake(name):
    """Convert CamelCase to snake_case."""
    result = []
    for i, c in enumerate(name):
        if c.isupper() and i > 0:
            result.append('_')
        result.append(c.lower())
    return ''.join(result)


def to_kebab(name):
    """Convert to kebab-case."""
    return to_snake(name).replace('_', '-')


def generate_pyproject(name, display_name, version, exchanges, asset_types):
    """Generate pyproject.toml content."""
    kebab = to_kebab(name)
    exchanges_str = ', '.join(f'"{e}"' for e in exchanges)
    asset_types_str = ', '.join(f'"{a}"' for a in asset_types)
    
    return f'''[build-system]
requires = ["setuptools>=65", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "bt_api_{kebab}"
version = "{version}"
description = "{display_name} exchange plugin for bt_api_py"
readme = "README.md"
requires-python = ">=3.9"
dependencies = [
    "bt_api_base>=0.15,<1.0",
    "bt_api_py>=0.15,<1.0",
]

[project.entry-points."bt_api_py.plugins"]
{kebab} = "bt_api_{kebab}.plugin:register_plugin"

[tool.setuptools.packages.find]
where = ["src"]
'''


def generate_init(version):
    """Generate __init__.py content."""
    return f'''__version__ = "{version}"
'''


def generate_plugin(exchange_name, adapter_name, asset_types, subscribe_handler_code):
    """Generate plugin.py content."""
    return f'''from __future__ import annotations

from typing import Any

from bt_api_base.gateway.registrar import GatewayRuntimeRegistrar
from bt_api_base.plugins.protocol import PluginInfo
from bt_api_base.registry import ExchangeRegistry

from bt_api_{to_kebab(exchange_name)} import __version__
from bt_api_{to_kebab(exchange_name)}.registry_registration import register_{to_snake(exchange_name)}


def register_plugin(
    registry: type[ExchangeRegistry], runtime_factory: type[GatewayRuntimeRegistrar]
) -> PluginInfo:
    register_{to_snake(exchange_name)}(registry)
    runtime_factory.register_adapter("{adapter_name}", None)  # TODO: Add adapter if available

    return PluginInfo(
        name="bt_api_{to_kebab(exchange_name)}",
        version=__version__,
        core_requires=">=0.15,<1.0",
        supported_exchanges={tuple(repr(e) for e in [f"{exchange_name}___" + a for a in asset_types])},
        supported_asset_types={tuple(repr(a) for a in asset_types)},
    )
'''


def generate_registry_registration(exchanges):
    """Generate registry_registration.py content."""
    lines = ['from __future__ import annotations\n']
    
    for ex_name, feed_module, request_class in exchanges:
        asset = ex_name.split('___')[1]
        lines.append(f'from bt_api_base.feeds.live_{to_snake(ex_name.split("___")[0])}.{feed_module} import {request_class}')
    
    lines.append('')
    
    # Generate subscribe handlers
    for ex_name, feed_module, request_class in exchanges:
        asset = ex_name.split('___')[1]
        func_name = f'_{to_snake(ex_name)}_{asset}_subscribe_handler'
        lines.append(f'def {func_name}(data_queue, exchange_params, topics, bt_api):')
        lines.append(f'    """Subscribe handler for {ex_name}."""')
        lines.append(f'    from bt_api_py.exchange_data import {to_snake(ex_name.split("___")[0]).capitalize()}ExchangeData')
        lines.append(f'    exchange_data = {to_snake(ex_name.split("___")[0]).capitalize()}ExchangeData()')
        lines.append(f'    kwargs = dict(exchange_params.items())')
        lines.append(f'    kwargs["exchange_data"] = exchange_data')
        lines.append(f'    kwargs["topics"] = topics')
        lines.append(f'    # TODO: Start the appropriate stream')
        lines.append(f'    pass\n')
    
    lines.append(f'def register_{to_snake(exchanges[0][0.split("___")[0])}(registry: ExchangeRegistry) -> None:')
    for ex_name, feed_module, request_class in exchanges:
        asset = ex_name.split('___')[1]
        func_name = f'_{to_snake(ex_name)}_{asset}_subscribe_handler'
        lines.append(f'    registry.register_feed("{ex_name}", {request_class})')
        lines.append(f'    registry.register_stream("{ex_name}", "subscribe", {func_name})')
    
    return '\n'.join(lines)


def generate_readme(name, display_name):
    """Generate README.md content."""
    kebab = to_kebab(name)
    return f'''# bt_api_{kebab}

{display_name} exchange plugin for `bt_api_py`.

## Installation

```bash
pip install bt_api_{kebab}
```

## Usage

The plugin will be automatically discovered by `bt_api_py` on startup.

## Supported Exchanges

'''


def generate_plugin_tests(name):
    """Generate test_plugin_registration.py content."""
    kebab = to_kebab(name)
    return f'''"""Tests for bt_api_{kebab} plugin registration."""

import pytest


def test_plugin_registration():
    """Test that plugin can be loaded and registered."""
    pass
'''


def create_plugin_structure(name, display_name, version, exchanges, asset_types):
    """Create all plugin files."""
    kebab = to_kebab(name)
    pkg_path = PACKAGES_PATH / f"bt_api_{kebab}"
    
    if pkg_path.exists():
        print(f"Plugin {kebab} already exists, skipping")
        return False
    
    # Create directory structure
    src_path = pkg_path / "src" / f"bt_api_{kebab}"
    tests_path = pkg_path / "tests"
    src_path.mkdir(parents=True)
    tests_path.mkdir(parents=True)
    
    # Generate files
    files = {
        "pyproject.toml": generate_pyproject(name, display_name, version, exchanges, asset_types),
        "README.md": generate_readme(name, display_name),
        f"src/bt_api_{kebab}/__init__.py": generate_init(version),
        f"src/bt_api_{kebab}/plugin.py": generate_plugin(name, name, asset_types, ""),
        f"src/bt_api_{kebab}/registry_registration.py": generate_registry_registration(exchanges),
        "tests/test_plugin_registration.py": generate_plugin_tests(name),
    }
    
    for path, content in files.items():
        full_path = pkg_path / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        print(f"Created: {full_path}")
    
    return True


if __name__ == "__main__":
    # Example: Generate HTX plugin
    create_plugin_structure(
        name="HTX",
        display_name="HTX (Huobi)",
        version="0.15.0",
        exchanges=HTX_EXCHANGES,
        asset_types=["SPOT", "MARGIN", "USDT_SWAP", "COIN_SWAP", "OPTION"]
    )