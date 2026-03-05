# bt_api_py 文档

**bt_api_py** 是一个专业的统一多交易所交易 API 框架，专为量化交易者和机构投资者设计。支持现货、合约、期货、股票等多种交易类型，提供同步、异步和 WebSocket 三种接口模式，让您用一套代码轻松对接全球主流交易所。

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![PyPI](https://img.shields.io/pypi/v/bt_api_py.svg)](https://pypi.org/project/bt_api_py/)

## 核心特性

| 特性 | 说明 |
|------|------|
| **🔌 即插即用架构** | 基于 Registry 模式，新增交易所无需修改核心代码，只需注册即可使用 |
| **🌐 多交易所统一接口** | 通过 `BtApi` 类统一管理 Binance、OKX、HTX、CTP、Interactive Brokers 等 73 个交易所 |
| **⚡ 三种 API 模式** | 同步 REST、异步 REST、WebSocket 实时推送，满足不同场景需求 |
| **📦 标准化数据容器** | 20+ 种标准化数据类型 (Ticker, OrderBook, Bar, Order, Position 等) |
| **🔔 事件驱动** | 内置 EventBus 事件总线，支持发布/订阅回调模式 |
| **🚀 高性能扩展** | 核心计算模块使用 Cython 和 C++ 实现，适合高频交易场景 |
| **💻 跨平台支持** | Linux (x86_64)、Windows (x64)、macOS (arm64/x86_64) |

## 完整支持的交易所

| 交易所 | 现货 (SPOT) | 合约 (SWAP) | 期货 (FUTURE) | 期权 (OPTION) | 股票 (STK) |
|--------|:-----------:|:-----------:|:-------------:|:-------------:|:----------:|
| **Binance** | ✅ | ✅ | — | ✅ | — |
| **HTX (Huobi)** | ✅ | ✅ | — | ✅ | — |
| **CTP** (中国期货) | — | — | ✅ | — | — |
| **Interactive Brokers** | — | — | ✅ | — | ✅ |

> 另有 17 个交易所已实现 REST API，40+ 个交易所已完成注册框架，总计 **73 个交易所**。

## 快速链接

<div class="grid cards" markdown>

- :material-download: **[安装指南](installation.md)** — 如何安装和配置
- :material-rocket-launch: **[快速入门](quickstart.md)** — 5 分钟快速上手
- :material-chart-timeline: **[架构设计](architecture.md)** — 核心架构和设计模式
- :material-book-open: **[使用指南](usage_guide.md)** — 完整使用教程
- :material-code-braces: **[API 参考](api_reference.md)** — BtApi 类完整 API
- :material-account-group: **[开发者指南](developer_guide.md)** — 如何扩展和贡献代码
- :material-file-document: **[代码示例](examples/api_examples.md)** — 丰富的实战示例

</div>

## 示例代码

```python
from bt_api_py import BtApi

# 配置多个交易所
exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": True,
    },
    "OKX___SPOT": {
        "api_key": "okx_key",
        "secret": "okx_secret",
        "passphrase": "okx_passphrase",
    },
}

# 创建统一 API 实例
api = BtApi(exchange_kwargs=exchange_kwargs)

# 获取行情（统一接口，适用所有交易所）
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()
print(f"BTC 价格: {ticker.get_last_price()}")

# 下单交易
order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    price=50000,
    order_type="limit"
)

# 查看所有已注册的交易所
print(BtApi.list_available_exchanges())
```

## 项目信息

- ***GitHub**: https://github.com/cloudQuant/bt_api_py
- ***PyPI**: https://pypi.org/project/bt_api_py/
- ***License**: MIT

---

!!! tip "提示"
    建议从 [快速入门](quickstart.md) 开始阅读文档。如需了解框架设计理念，请参考 [架构设计](architecture.md)。
