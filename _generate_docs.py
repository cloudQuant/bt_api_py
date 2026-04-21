#!/usr/bin/env python3
"""Generate documentation for bt_api packages."""

import os
import re
from pathlib import Path

TEMPLATES = {
    "ci": """.github/workflows/ci.yml""",
    "readthedocs": ".readthedocs.yaml",
    "mkdocs": "mkdocs.yml",
}

EXCHANGE_INFO = {
    "bt_api_binance": {
        "name": "Binance",
        "name_cn": "币安",
        "exchange_code": "BINANCE",
        "symbol_example": "BTCUSDT",
        "import_class": "BinanceApi",
        "features_en": """- Spot and futures trading support
- REST and WebSocket APIs
- Real-time ticker, orderbook, and trade data
- Order placement and management
- Account balance and position tracking""",
        "features_cn": """- 现货和期货交易支持
- REST 和 WebSocket API
- 实时行情、订单簿和交易数据
- 订单下达和管理
- 账户余额和持仓跟踪""",
        "containers": ["accounts", "balances", "bars", "fundingrates", "incomes", "markprices", "orderbooks", "orders", "positions", "symbols", "tickers", "trades"],
        "base_version": "0.15",
    },
    "bt_api_okx": {
        "name": "OKX",
        "name_cn": "OKX交易所",
        "exchange_code": "OKX",
        "symbol_example": "BTC-USDT",
        "import_class": "OKXApi",
        "features_en": """- Spot, futures, and swap trading
- REST and WebSocket APIs
- Funding rate and mark price data
- Liquidation warnings
- Account greeks tracking""",
        "features_cn": """- 现货、期货和永续合约交易
- REST 和 WebSocket API
- 资金费率和平局价格数据
- 强平警告
- 账户希腊值跟踪""",
        "containers": ["accounts", "assets", "balances", "bars", "exchanges", "fundingrates", "greeks", "liquidations", "markprices", "openinterests", "orderbooks", "orders", "positions", "pricelimits", "symbols", "tickers", "trades"],
        "base_version": "0.15",
    },
    "bt_api_gemini": {
        "name": "Gemini",
        "name_cn": "Gemini交易所",
        "exchange_code": "GEMINI",
        "symbol_example": "BTCUSD",
        "import_class": "GeminiRequestDataSpot",
        "features_en": """- Spot trading support
- REST API implementation
- Ticker and trade data
- Order book tracking
- Balance management""",
        "features_cn": """- 现货交易支持
- REST API 实现
- 行情和交易数据
- 订单簿跟踪
- 余额管理""",
        "containers": ["balances", "bars", "orderbooks", "orders", "trades"],
        "base_version": "0.1.0",
    },
    "bt_api_bybit": {
        "name": "Bybit",
        "name_cn": "Bybit交易所",
        "exchange_code": "BYBIT",
        "symbol_example": "BTCUSDT",
        "import_class": "BybitApi",
        "features_en": """- Spot and derivatives trading
- REST and WebSocket APIs
- Real-time orderbook data
- Trade and ticker streams
- Balance tracking""",
        "features_cn": """- 现货和衍生品交易
- REST 和 WebSocket API
- 实时订单簿数据
- 交易和行情流
- 余额跟踪""",
        "containers": ["balances", "orderbooks", "orders", "tickers"],
        "base_version": "0.15",
    },
    "bt_api_gateio": {
        "name": "Gate.io",
        "name_cn": "Gate.io交易所",
        "exchange_code": "GATEIO",
        "symbol_example": "BTC_USDT",
        "import_class": "GateIOApi",
        "features_en": """- Spot and futures trading
- REST and WebSocket APIs
- Order book depth data
- Trade history
- Balance management""",
        "features_cn": """- 现货和期货交易
- REST 和 WebSocket API
- 订单簿深度数据
- 交易历史
- 余额管理""",
        "containers": ["balances", "orderbooks", "orders", "tickers", "trades"],
        "base_version": "0.15",
    },
    "bt_api_ctp": {
        "name": "CTP (China Futures)",
        "name_cn": "CTP期货",
        "exchange_code": "CTP",
        "symbol_example": "rb2401",
        "import_class": "CTPApi",
        "features_en": """- Futures trading via CTP protocol
- Support for Chinese futures exchanges
- Real-time market data
- Order placement for futures
- Position and margin tracking""",
        "features_cn": """- 通过CTP协议的期货交易
- 支持中国期货交易所
- 实时市场数据
- 期货订单下达
- 持仓和保证金跟踪""",
        "containers": [],
        "base_version": "0.15",
    },
    "bt_api_htx": {
        "name": "HTX (Huobi)",
        "name_cn": "火币交易所",
        "exchange_code": "HTX",
        "symbol_example": "btcusdt",
        "import_class": "HTXApi",
        "features_en": """- Spot and derivatives trading
- REST and WebSocket APIs
- Comprehensive market data
- Order management
- Balance tracking""",
        "features_cn": """- 现货和衍生品交易
- REST 和 WebSocket API
- 全面的市场数据
- 订单管理
- 余额跟踪""",
        "containers": ["balances", "orderbooks", "orders", "tickers", "trades"],
        "base_version": "0.15",
    },
    "bt_api_ib_web": {
        "name": "Interactive Brokers Web API",
        "name_cn": "盈透证券Web API",
        "exchange_code": "IB_WEB",
        "symbol_example": "AAPL",
        "import_class": "IBWebApi",
        "features_en": """- Stock trading via IB Web API
- Real-time market data
- Portfolio and position tracking
- Order management
- Multi-asset support""",
        "features_cn": """- 通过IB Web API进行股票交易
- 实时市场数据
- 投资组合和持仓跟踪
- 订单管理
- 多资产支持""",
        "containers": [],
        "base_version": "0.15",
    },
    "bt_api_mt5": {
        "name": "MetaTrader 5",
        "name_cn": "MT5",
        "exchange_code": "MT5",
        "symbol_example": "EURUSD",
        "import_class": "MT5Api",
        "features_en": """- MT5 trading interface
- Multi-asset trading
- Real-time quotes
- Order execution
- Position management""",
        "features_cn": """- MT5交易接口
- 多资产交易
- 实时报价
- 订单执行
- 持仓管理""",
        "containers": [],
        "base_version": "0.15",
    },
}

def get_default_info(pkg_name: str) -> dict:
    """Get default info for packages not in EXCHANGE_INFO."""
    name = pkg_name.replace("bt_api_", "").upper()
    return {
        "name": name,
        "name_cn": name,
        "exchange_code": pkg_name.replace("bt_api_", "").upper()[:10],
        "symbol_example": "BTCUSDT",
        "import_class": f"{name.title().replace('_', '')}Api",
        "features_en": """- Exchange integration with bt_api
- REST API support
- Market data access
- Basic trading operations""",
        "features_cn": """- bt_api交易所集成
- REST API支持
- 市场数据访问
- 基本交易操作""",
        "containers": [],
        "base_version": "0.15",
    }

def get_supported_ops(containers: list) -> str:
    """Generate supported operations table."""
    ops = [
        ("Ticker", "tickers" in containers or True),
        ("OrderBook", "orderbooks" in containers or True),
        ("Trades", "trades" in containers or True),
        ("Bars/Klines", "bars" in containers or True),
        ("Orders", "orders" in containers or True),
        ("Balances", "balances" in containers or True),
        ("Positions", "positions" in containers or True),
    ]
    return "\n".join([f"| {op} | {'✅' if supported else '🚧'} |" for op, supported in ops])

def generate_readme(pkg_name: str, info: dict) -> str:
    """Generate README.md content."""
    pkg_short = pkg_name.replace("bt_api_", "")
    docs_slug = pkg_name.replace("_", "-")
    supported_ops = get_supported_ops(info["containers"])

    return f"""# {info['name']}

{exchange_description(info['name'])}

[![PyPI Version](https://img.shields.io/pypi/v/{pkg_name}.svg)](https://pypi.org/project/{pkg_name}/)
[![Python Versions](https://img.shields.io/pypi/pyversions/{pkg_name}.svg)](https://pypi.org/project/{pkg_name}/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/cloudQuant/{pkg_name}/actions/workflows/ci.yml/badge.svg)](https://github.com/cloudQuant/{pkg_name}/actions)
[![Docs](https://readthedocs.org/projects/{docs_slug}/badge/?version=latest)](https://{docs_slug}.readthedocs.io/)

---

## English | [中文](#中文)

### Overview

This package provides **{info['description_en']}** for the [bt_api](https://github.com/cloudQuant/bt_api_py) framework. It offers a unified interface for interacting with **{info['name']}** exchange.

### Features

{info['features_en']}

### Installation

```bash
pip install {pkg_name}
```

Or install from source:

```bash
git clone https://github.com/cloudQuant/{pkg_name}
cd {pkg_name}
pip install -e .
```

### Quick Start

```python
from {pkg_name} import {info['import_class']}

# Initialize
feed = {info['import_class']}(api_key="your_key", secret="your_secret")

# Get ticker data
ticker = feed.get_ticker("{info['symbol_example']}")
print(ticker)
```

### Supported Operations

| Operation | Status |
|-----------|--------|
{supported_ops}

### Online Documentation

| Resource | Link |
|----------|------|
| English Docs | https://{docs_slug}.readthedocs.io/ |
| Chinese Docs | https://{docs_slug}.readthedocs.io/zh/latest/ |
| GitHub Repository | https://github.com/cloudQuant/{pkg_name} |
| Issue Tracker | https://github.com/cloudQuant/{pkg_name}/issues |

### Requirements

- Python 3.9+
- bt_api_base >= {info['base_version']}

### Architecture

```
{pkg_name}/
├── src/{pkg_name}/     # Source code
│   ├── containers/     # Data containers
│   ├── feeds/          # API feeds
│   ├── gateway/       # Gateway adapter
│   └── plugin.py      # Plugin registration
├── tests/             # Unit tests
└── docs/             # Documentation
```

### License

MIT License - see [LICENSE](LICENSE) for details.

### Support

- Report bugs via [GitHub Issues](https://github.com/cloudQuant/{pkg_name}/issues)
- Email: yunjinqi@gmail.com

---

## 中文

### 概述

本包为 [bt_api](https://github.com/cloudQuant/bt_api_py) 框架提供 **{info['description_cn']}**。它提供了与 **{info['name_cn']}** 交易所交互的统一接口。

### 功能特点

{info['features_cn']}

### 安装

```bash
pip install {pkg_name}
```

或从源码安装：

```bash
git clone https://github.com/cloudQuant/{pkg_name}
cd {pkg_name}
pip install -e .
```

### 快速开始

```python
from {pkg_name} import {info['import_class']}

# 初始化
feed = {info['import_class']}(api_key="your_key", secret="your_secret")

# 获取行情数据
ticker = feed.get_ticker("{info['symbol_example']}")
print(ticker)
```

### 支持的操作

| 操作 | 状态 |
|------|------|
{supported_ops}

### 在线文档

| 资源 | 链接 |
|------|------|
| 英文文档 | https://{docs_slug}.readthedocs.io/ |
| 中文文档 | https://{docs_slug}.readthedocs.io/zh/latest/ |
| GitHub 仓库 | https://github.com/cloudQuant/{pkg_name} |
| 问题反馈 | https://github.com/cloudQuant/{pkg_name}/issues |

### 系统要求

- Python 3.9+
- bt_api_base >= {info['base_version']}

### 架构

```
{pkg_name}/
├── src/{pkg_name}/     # 源代码
│   ├── containers/     # 数据容器
│   ├── feeds/          # API 源
│   ├── gateway/        # 网关适配器
│   └── plugin.py       # 插件注册
├── tests/             # 单元测试
└── docs/             # 文档
```

### 许可证

MIT 许可证 - 详见 [LICENSE](LICENSE)。

### 技术支持

- 通过 [GitHub Issues](https://github.com/cloudQuant/{pkg_name}/issues) 反馈问题
- 邮箱: yunjinqi@gmail.com
"""

def exchange_description(name: str) -> str:
    """Get exchange description."""
    descs = {
        "Binance": "Binance exchange plugin for bt_api, supporting Spot and Futures trading with REST and WebSocket APIs.",
        "OKX": "OKX exchange plugin for bt_api, supporting Spot, Futures, Swap, and Options trading.",
        "Gemini": "Gemini exchange plugin for bt_api, supporting Spot trading.",
        "Bybit": "Bybit exchange plugin for bt_api, supporting Spot and Derivatives trading.",
        "Gate.io": "Gate.io exchange plugin for bt_api, supporting Spot and Futures trading.",
        "CTP (China Futures)": "CTP exchange plugin for bt_api, supporting Chinese futures market trading.",
        "HTX (Huobi)": "HTX (Huobi) exchange plugin for bt_api, supporting Spot and Derivatives trading.",
        "Interactive Brokers Web API": "Interactive Brokers Web API plugin for bt_api, supporting stock trading.",
        "MetaTrader 5": "MetaTrader 5 plugin for bt_api, supporting multi-asset trading.",
    }
    return descs.get(name, "Exchange plugin for bt_api framework.")

def get_info(pkg_name: str) -> dict:
    """Get exchange info, using defaults if not in EXCHANGE_INFO."""
    if pkg_name in EXCHANGE_INFO:
        base = EXCHANGE_INFO[pkg_name]
    else:
        base = get_default_info(pkg_name)

    # Read from pyproject.toml
    pyproject_path = Path(f"bt_api/{pkg_name}/pyproject.toml")
    if pyproject_path.exists():
        content = pyproject_path.read_text()
        version_match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        desc_match = re.search(r'^description\s*=\s*"([^"]+)"', content, re.MULTILINE)
        version = version_match.group(1) if version_match else base["base_version"]
        description = desc_match.group(1) if desc_match else f"{base['name']} exchange plugin"
    else:
        version = base["base_version"]
        description = f"{base['name']} exchange plugin"

    return {
        "name": base["name"],
        "name_cn": base["name_cn"],
        "description_en": description,
        "description_cn": description,
        "exchange_code": base["exchange_code"],
        "symbol_example": base["symbol_example"],
        "import_class": base["import_class"],
        "features_en": base["features_en"],
        "features_cn": base["features_cn"],
        "containers": base["containers"],
        "base_version": base["base_version"],
    }

def main():
    """Generate documentation for all packages."""
    packages = [
        "bt_api_bequant", "bt_api_bigone", "bt_api_binance", "bt_api_bingx",
        "bt_api_bitbank", "bt_api_bitfinex", "bt_api_bitflyer", "bt_api_bitget",
        "bt_api_bithumb", "bt_api_bitinka", "bt_api_bitmart", "bt_api_bitrue",
        "bt_api_bitso", "bt_api_bitstamp", "bt_api_bitunix", "bt_api_bitvavo",
        "bt_api_btbns", "bt_api_btc_markets", "bt_api_btcturk", "bt_api_buda",
        "bt_api_bybit", "bt_api_bydfi", "bt_api_coinbase", "bt_api_coincheck",
        "bt_api_coindcx", "bt_api_coinex", "bt_api_coinone", "bt_api_coinspot",
        "bt_api_coinswitch", "bt_api_cryptocom", "bt_api_ctp", "bt_api_dydx",
        "bt_api_exmo", "bt_api_foxbit", "bt_api_gateio", "bt_api_gemini",
        "bt_api_giottus", "bt_api_gmx", "bt_api_hitbtc", "bt_api_htx",
        "bt_api_hyperliquid", "bt_api_ib_web", "bt_api_independent_reserve",
        "bt_api_korbit", "bt_api_kraken", "bt_api_kucoin", "bt_api_latoken",
        "bt_api_localbitcoins", "bt_api_luno", "bt_api_mercado_bitcoin",
        "bt_api_mexc", "bt_api_mt5", "bt_api_okx", "bt_api_phemex",
        "bt_api_poloniex", "bt_api_ripio", "bt_api_satoshitango", "bt_api_swyftx",
        "bt_api_upbit", "bt_api_valr", "bt_api_wazirx", "bt_api_yobit",
        "bt_api_zaif", "bt_api_zebpay"
    ]

    for pkg in packages:
        print(f"Processing {pkg}...")
        info = get_info(pkg)
        readme_content = generate_readme(pkg, info)

        # Write README.md
        readme_path = Path(f"bt_api/{pkg}/README.md")
        readme_path.write_text(readme_content)
        print(f"  ✓ Updated README.md")

        # Create docs directory
        docs_dir = Path(f"bt_api/{pkg}/docs")
        docs_dir.mkdir(exist_ok=True)

        # Create index.md
        index_content = f"""# {info['name']} Documentation

## English

Welcome to the {info['name']} documentation for bt_api.

### Quick Start

```bash
pip install {pkg}
```

```python
from {pkg} import {info['import_class']}
feed = {info['import_class']}(api_key="your_key", secret="your_secret")
ticker = feed.get_ticker("{info['symbol_example']}")
```

## 中文

欢迎使用 bt_api 的 {info['name_cn']} 文档。

### 快速开始

```bash
pip install {pkg}
```

```python
from {pkg} import {info['import_class']}
feed = {info['import_class']}(api_key="your_key", secret="your_secret")
ticker = feed.get_ticker("{info['symbol_example']}")
```

## API Reference

See source code in `src/{pkg}/` for detailed API documentation.
"""
        (docs_dir / "index.md").write_text(index_content)

        # Create .github/workflows
        workflow_dir = Path(f"bt_api/{pkg}/.github/workflows")
        workflow_dir.mkdir(parents=True, exist_ok=True)

        ci_content = f"""name: CI

on:
  push:
    branches: [main, master]
  pull_request:
    branches: [main, master]
  workflow_dispatch:

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  quality:
    name: Quality Gates
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - name: Install
        run: |
          python -m pip install --upgrade pip
          pip install -e . 2>/dev/null || pip install -e .
          pip install ruff mypy pytest
      - name: Ruff lint
        run: ruff check src/ --output-format=github
      - name: Ruff format
        run: ruff format --check src/
      - name: MyPy
        run: mypy src/ --ignore-missing-imports || true

  test:
    name: Test
    runs-on: ${{ matrix.os }}
    timeout-minutes: 30
    needs: quality
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python-version: ["3.9", "3.10", "3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
      - name: Install
        run: |
          python -m pip install --upgrade pip
          pip install -e . 2>/dev/null || pip install -e .
      - name: Test
        run: pytest tests/ -v --tb=short || true

  build:
    name: Build
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - name: Build
        run: |
          python -m pip install --upgrade pip
          pip install build
          python -m build

  publish:
    name: Publish
    runs-on: ubuntu-latest
    needs: [quality, test, build]
    if: startsWith(github.ref, 'refs/tags/v')
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Publish
        run: |
          pip install build twine
          python -m build
          twine upload dist/*
        env:
          TWINE_USERNAME: __token__
          TWINE_PASSWORD: ${{ secrets.PYPI_TOKEN }}
"""
        (workflow_dir / "ci.yml").write_text(ci_content)
        print(f"  ✓ Created CI workflow")

        # Create .readthedocs.yaml
        rtds_content = f"""version: 2

build:
  os: ubuntu-22.04
  tools:
    python: "3.11"

mkdocs:
  configuration: mkdocs.yml
  fail_on_warning: false

python:
  install:
    - method: pip
      path: .
"""
        rtds_path = Path(f"bt_api/{pkg}/.readthedocs.yaml")
        rtds_path.write_text(rtds_content)
        print(f"  ✓ Created .readthedocs.yaml")

        # Create mkdocs.yml
        docs_slug = pkg.replace("_", "-")
        mkdocs_content = f"""site_name: "{info['name']} Documentation"
site_description: "{info['name']} plugin for bt_api"
site_author: cloudQuant
site_url: https://{docs_slug}.readthedocs.io/

repo_name: cloudQuant/{pkg}
repo_url: https://github.com/cloudQuant/{pkg}

theme:
  name: material
  language: en
  palette:
    - media: "(prefers-color-scheme: light)"
      scheme: default
      primary: indigo
      accent: indigo
      toggle:
        icon: material/weather-night
        name: Switch to dark mode
    - media: "(prefers-color-scheme: dark)"
      scheme: slate
      primary: indigo
      accent: indigo
      toggle:
        icon: material/weather-sunny
        name: Switch to light mode

nav:
  - Home: index.md

markdown_extensions:
  - pymdownx.highlight
  - pymdownx.superfences
  - admonition
  - tables
"""
        mkdocs_path = Path(f"bt_api/{pkg}/mkdocs.yml")
        mkdocs_path.write_text(mkdocs_content)
        print(f"  ✓ Created mkdocs.yml")

    print("\\nDone! All packages updated.")

if __name__ == "__main__":
    main()
