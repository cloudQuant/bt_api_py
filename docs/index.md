# bt_api_py

<p align="center">
  <strong>统一多交易所交易 API 框架</strong><br>
  一套代码，轻松对接全球主流交易所
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT"></a>
  <a href="https://pypi.org/project/bt_api_py/"><img src="https://img.shields.io/pypi/v/bt_api_py.svg" alt="PyPI"></a>
</p>

---

## 为什么选择 bt_api_py？

**bt_api_py** 专为量化交易者和机构投资者设计。它解决了多交易所对接中最棘手的问题：每个交易所都有不同的认证方式、数据格式、接口风格 — 而 bt_api_py 提供**一套统一的接口**来屏蔽这些差异。

<div class="grid cards" markdown>

-   :material-power-plug:{ .lg .middle } **即插即用架构**

    ---

    基于 Registry 模式，新增交易所无需修改任何核心代码，只需注册 Feed 类即可。完全符合**开闭原则**。

    [:octicons-arrow-right-24: 了解架构设计](architecture.md)

-   :material-swap-horizontal:{ .lg .middle } **统一多交易所接口**

    ---

    `BtApi` 一个类统一管理 Binance、OKX、HTX、CTP、Interactive Brokers 等 **73 个交易所**，同一套代码，多平台部署。

    [:octicons-arrow-right-24: 查看支持的交易所](#_3)

-   :material-lightning-bolt:{ .lg .middle } **三种 API 模式**

    ---

    同步 REST、异步 REST、WebSocket 实时推送。按场景自由选择，满足从策略回测到高频交易的全部需求。

    [:octicons-arrow-right-24: 使用指南](usage_guide.md)

-   :material-package-variant:{ .lg .middle } **标准化数据容器**

    ---

    20+ 种标准化数据类型：Ticker、OrderBook、Bar、Order、Position 等，屏蔽各交易所数据格式差异。

    [:octicons-arrow-right-24: 数据容器参考](api_reference/data_containers.md)

-   :material-broadcast:{ .lg .middle } **事件驱动架构**

    ---

    内置 `EventBus` 事件总线，支持发布/订阅回调模式，轻松构建实时响应的交易系统。

    [:octicons-arrow-right-24: 事件总线参考](reference/event_bus.md)

-   :material-rocket-launch:{ .lg .middle } **高性能 C/C++ 扩展**

    ---

    核心计算模块使用 Cython 和 C++ 实现，关键路径深度优化，适合高频交易场景。支持 Linux/Windows/macOS。

    [:octicons-arrow-right-24: 性能优化指南](performance.md)

</div>

## 30 秒快速上手

```python
from bt_api_py import BtApi

# 配置多个交易所
api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": True,
    },
    "OKX___SWAP": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "passphrase": "your_passphrase",
    },
})

# 获取行情 — 同一接口，适用所有交易所
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()
print(f"BTC 现价: {ticker.get_last_price()}")

# 下单交易
order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    price=50000,
    order_type="limit",
)

# 批量查询所有交易所行情
all_tickers = api.get_all_ticks("BTCUSDT")
```

[:octicons-arrow-right-24: 查看完整快速入门](quickstart.md){ .md-button .md-button--primary }
[:octicons-arrow-right-24: 安装说明](installation.md){ .md-button }

## 支持的交易所

| 交易所 | 现货 | 合约/永续 | 期货 | 期权 | 股票 |
|--------|:----:|:---------:|:----:|:----:|:----:|
| **Binance** | ✅ | ✅ | — | ✅ | — |
| **OKX** | ✅ | ✅ | — | — | — |
| **HTX (Huobi)** | ✅ | ✅ | — | ✅ | — |
| **CTP** (中国期货) | — | — | ✅ | — | — |
| **Interactive Brokers** | — | — | ✅ | — | ✅ |

!!! info "更多交易所"
    另有 17 个交易所已实现 REST API，40+ 个交易所已完成注册框架，总计支持 **73 个交易所**。
    查看完整列表：`BtApi.list_available_exchanges()`

## 文档结构

本文档按照 [Diátaxis](https://diataxis.fr/) 框架组织：

| 类型 | 目的 | 入口 |
|------|------|------|
| **入门** | 安装和基本使用 | [快速入门](quickstart.md) |
| **使用指南** | 解决特定问题的步骤 | [使用指南](usage_guide.md) |
| **API 参考** | 完整的 Python API 文档 | [API 参考](reference/index.md) |
| **交易所** | 各交易所原始 API 文档 | [交易所](binance/index.md) |
| **深度解读** | 架构设计和原理解释 | [架构设计](architecture.md) |
