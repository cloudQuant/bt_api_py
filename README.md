# bt_api_py

[![Python 3.11-3.14](https://img.shields.io/badge/python-3.11--3.14-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/bt_api_py.svg)](https://pypi.org/project/bt_api_py/)
[![Tests](https://github.com/cloudQuant/bt_api_py/actions/workflows/tests.yml/badge.svg)](https://github.com/cloudQuant/bt_api_py/actions/workflows/tests.yml)
[![Docs](https://github.com/cloudQuant/bt_api_py/actions/workflows/docs.yml/badge.svg)](https://github.com/cloudQuant/bt_api_py/actions/workflows/docs.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

**Language / 语言**: [English (default)](#en) | [中文](#zh)

<a id="en"></a>
## English

[🇨🇳 切换到中文](#zh)

**bt_api_py** is a unified multi-exchange trading API framework built for quant trading, arbitrage execution, market making, and multi-account asset management. It unifies REST, async REST, and WebSocket APIs from different exchanges into a single Python interface to reduce integration duplication.

**Quick links**

- [Online docs](https://cloudquant.github.io/bt_api_py/)
- [Installation guide](https://cloudquant.github.io/bt_api_py/getting-started/installation/)
- [Quickstart](https://cloudquant.github.io/bt_api_py/getting-started/quickstart/)
- [Docs directory in this repo](docs/)
- [Issue tracker](https://github.com/cloudQuant/bt_api_py/issues)

## Why choose bt_api_py

- **Unified exchange surface**: Manage Binance, OKX, HTX, CTP, Interactive Brokers, and more via one `BtApi` interface.
- **Three operation modes**: Synchronous REST, async REST, and WebSocket subscription are all supported.
- **Standardized domain model**: Consistent containers like `Ticker`, `OrderBook`, `Bar`, `Order`, `Position`, and `Balance`.
- **Extensible architecture**: New exchanges are added through registry + adapter patterns without changing core API code.
- **Event-driven design**: Built-in `EventBus` is suitable for execution workflows, market data forwarding, and state fan-out.
- **Forwarding-ready runtime**: Built-in market fan-out and order routing primitives support one market stream serving multiple strategies and one account serving multiple trading clients.

### Core use cases

- Quant strategy development
- Multi-exchange arbitrage systems
- Market making systems requiring low-latency updates
- Multi-account asset and position management
- Automated trading bots

## Core Features

### Multi-exchange unified interface
`BtApi` exposes a single API surface for spot, futures, options, and stock feeds, reducing per-exchange integration work.

### Three API calling modes
- **Sync REST API**: Good for scripts and small tools.
- **Async REST API**: Good for high-concurrency collection and batch workflows.
- **WebSocket subscriptions**: Good for low-latency streaming data and event-driven systems.

### Plug-and-play exchange extension
New exchanges can be added by implementing adapters and registry entries without changing the core package.

### Event-driven mechanics
Built-in `EventBus` handles asynchronous updates such as market updates, order changes, and fills.

### Market data and order forwarding
`bt_api_py.forwarding` provides a lightweight gateway layer for sharing exchange
connections across multiple strategy processes:

- `MarketDataHub` fans out normalized `MarketEvent` objects with topic prefixes such as `md.BINANCE.SWAP.BTC-USDT.tick`.
- `OrderRouter` centralizes account-level order submission, cancellation, basic risk checks, idempotency, and private order/trade/account events.
- `ForwardingClient` is an embedded in-process client for tests and local strategy runners.
- `ZmqForwardingRuntime` / `ZmqForwardingClient` provide ZeroMQ `PUB/SUB` market streams and `ROUTER/DEALER` order commands for multi-process deployments.
- `SQLiteStateStore` persists command acknowledgements and private events so idempotent order commands can survive process restarts.
- Forwarding payloads are capped by the exported `MAX_MESSAGE_BYTES` limit before entering JSON/ZMQ transport paths.
- Embedded clients use `command_timeout` for synchronous command calls. Account, position, and open-order queries fall back to cached snapshots on missing handlers or timeouts; order submission and cancellation still surface command failures.
- Client-side realtime event queues use `event_cache_size=4096` by default to bound slow-consumer memory growth. Set `event_cache_size=None` only when a strategy runner intentionally needs unbounded local event accumulation.
- `ForwardingClient.stats()` refreshes current subscriptions by default, then reports pending and dropped realtime event counts so slow consumers can be diagnosed without inspecting private queues. Use `stats(refresh=False)` for a local-cache-only snapshot.

The recommended boundary is to keep exchange connectivity, market fan-out,
account sharing, risk checks, idempotency, and transport protocols in
`bt_api_py.forwarding`. Strategy engines such as Backtrader should consume this
boundary through a thin client adapter instead of owning the exchange sockets or
account router themselves.

### Standardized containers
Over 20 standardized container types include:
- Market: `Ticker`, `OrderBook`, `Bar`, `MarkPrice`, `FundingRate`
- Trading: `Order`, `Trade`, `Position`, `Balance`, `Account`
- Other: `Symbol`, `Instrument`, `Liquidation`, `Greek`

### Cross-platform support
Current target compatibility is Python `3.11-3.13` (release-blocking) and `3.14` (canary); CI runs on Linux, macOS, and Windows.

## Supported exchanges

### Quick snapshot

The full exchange support matrix is automatically refreshed in the Chinese section below.

- Fully supported: Binance, HTX, CTP, Interactive Brokers (REST + WebSocket + test pass).
- Implemented with known WebSocket gaps: OKX, Bybit, Bitget, Kraken, Gate.io, Upbit, Crypto.com, HitBTC, Phemex, Gemini.
- Implemented and under gap-fixing: KuCoin, MEXC, Bitfinex, Coinbase, BYDFi.
- Implemented but test depth still needs strengthening: Hyperliquid, dYdX.

> Current conservative total: 4 fully supported + 17 partially implemented + 40+ registered exchanges = 73+ exchanges.

## Installation & compatibility

| Item | Support |
|------|---------|
| Python | `3.11` - `3.13`（阻塞发布）；`3.14`（canary） |
| OS | Linux, macOS, Windows |
| Installation | PyPI, source install |
| Main APIs | REST, Async REST, WebSocket |

### Option 1: install from PyPI

```bash
pip install bt_api_py
```

### Option 2: editable source install

Compile Cython and CTP SWIG extensions locally first, then install dependencies by platform.

#### macOS

```bash
xcode-select --install
brew install swig
```

#### Linux (Debian/Ubuntu)

```bash
sudo apt install swig g++
```

#### Windows 11

```bash
winget install Microsoft.VisualStudio.2022.BuildTools
# Open Visual Studio Installer, select "Desktop development with C++"
winget install miniconda3
conda install -c conda-forge swig libiconv
```

Then:

```bash
git clone --recurse-submodules https://github.com/cloudQuant/bt_api_py
cd bt_api_py
python -m pip install --upgrade pip
pip install -e .

# Development mode
pip install -e ".[dev]"
```

If you already cloned the repository without submodules, initialize the exchange plugin submodules:

```bash
git submodule update --init --recursive --jobs 8
```

To check and install `bt_api_*` plugin packages together, use the repository script. The default strategy skips packages that are already installed, then tries local submodule source installs for missing packages, falls back to PyPI when source installs are unavailable or fail, and reports any remaining failures in the summary.

```bash
# Install bt_api_py itself and all plugins with source-first strategy
python scripts/install_bt_api_submodules.py --with-root --editable-root --strategy source-first

# Only install from local submodule source, without PyPI fallback
python scripts/install_bt_api_submodules.py --with-root --editable-root --strategy source-only --upgrade

# Install selected plugins only
python scripts/install_bt_api_submodules.py bt_api_base bt_api_binance bt_api_okx --strategy source-first

# Check installation status only, without installing packages
python scripts/install_bt_api_submodules.py --strategy none
```

### Optional extras

| Extra | Purpose |
|------|---------|
| `bt_api_py[all]` | Install all optional dependencies |
| `bt_api_py[dev]` | `pytest`, `ruff`, `mypy`, and other dev tools |
| `bt_api_py[security]` | `security_compliance`, OAuth/JWT, encryption, password hashing |
| `bt_api_py[ib]` | Interactive Brokers native support |
| `bt_api_py[ib_web]` | IB Web API and browser automation dependencies |
| `bt_api_py[visualization]` | Charting and visualization tools |

```bash
pip install bt_api_py[all]
pip install bt_api_py[dev]
pip install bt_api_py[security]
```

## Quick start

### Synchronous market query

```python
from bt_api_py import BtApi

exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": True,
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
print(ticker)
```

### Make order

```python
from decimal import Decimal
from bt_api_py import OrderRequest, OrderType, Side

order = api.make_order(
    "BINANCE___SPOT",
    OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.001"),
        price=Decimal("50000"),
        account_id="paper",
        client_order_id="cid-1",
    ),
)
print(order)
```

### Async calls

```python
import asyncio
from bt_api_py import BtApi

async def main():
    api = BtApi(
        exchange_kwargs={
            "BINANCE___SPOT": {
                "api_key": "your_api_key",
                "secret": "your_secret",
                "testnet": True,
            }
        }
    )

    ticker = await api.async_get_tick("BINANCE___SPOT", "BTCUSDT")
    print(ticker)

asyncio.run(main())
```

### WebSocket subscribe

```python
api.subscribe(
    "BINANCE___SPOT___BTCUSDT",
    [
        {"topic": "ticker", "symbol": "BTCUSDT"},
        {"topic": "depth", "symbol": "BTCUSDT"},
    ],
)

data_queue = api.get_data_queue("BINANCE___SPOT")
message = data_queue.get(timeout=10)
print(type(message).__name__, message)
```

### Embedded forwarding runtime

```python
import asyncio

from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.forwarding import ForwardingClient, ForwardingRuntime


async def main():
    runtime = ForwardingRuntime(MockBrokerAdapter())
    await runtime.start()

    client = ForwardingClient(
        bus=runtime.bus,
        exchange="SIM",
        market_type="SPOT",
        account_id="paper",
        strategy_id="demo_strategy",
        command_timeout=2.0,
        event_cache_size=4096,
    )
    client.connect()
    client.subscribe("RB2510")

    runtime.market_data.publish_tick(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        price=3500.0,
    )
    print(client.poll_tick("RB2510").price)
    print(client.stats(refresh=False)["dropped_event_counts"])

    order = client.submit_order(
        {
            "symbol": "RB2510",
            "side": "buy",
            "size": 1,
            "order_type": "limit",
            "price": 3500.0,
        }
    )
    print(order["order_id"])

    await runtime.stop()


asyncio.run(main())
```

### ZeroMQ forwarding service

> **Safety notice (mock/local only):** the forwarding gateway defaults to
> read-only loopback/IPC mode. Remote TCP and write-enabled operation require
> authenticated deployment (CurveZMQ + ACL), which lands in a later iteration.
> Do not expose these endpoints on a public network or attach production
> credentials before then.

```python
from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.forwarding import ZmqForwardingClient, ZmqForwardingRuntime

runtime = ZmqForwardingRuntime(
    MockBrokerAdapter(),
    market_endpoint="tcp://127.0.0.1:7001",
    command_endpoint="tcp://127.0.0.1:7002",
    private_endpoint="tcp://127.0.0.1:7003",
)
runtime.start_sync()

client = ZmqForwardingClient(
    market_endpoint="tcp://127.0.0.1:7001",
    command_endpoint="tcp://127.0.0.1:7002",
    private_endpoint="tcp://127.0.0.1:7003",
    exchange="SIM",
    market_type="SPOT",
    account_id="paper",
    strategy_id="demo_strategy",
    command_timeout_ms=2000,
    event_cache_size=4096,
)
client.connect()
client.subscribe("RB2510")
print(client.stats(refresh=False))
```

### Forwarding deployment guide

| Mode | Best for | Transport | Notes |
|------|----------|-----------|-------|
| Embedded runtime | Unit tests, local simulations, single-process strategy runners | `InMemoryForwardingBus` | Use `ForwardingRuntime` + `ForwardingClient`; `command_timeout` is in seconds. |
| ZeroMQ service | Multiple strategy processes or services sharing one upstream exchange/account gateway | `PUB/SUB` for market/private events, `ROUTER/DEALER` for commands | Use `ZmqForwardingRuntime` + `ZmqForwardingClient`; `command_timeout_ms` is in milliseconds. |
| Existing `BtApi` bridge | Reusing current WebSocket queues from exchange adapters | `BtApiForwardingAdapter` into `MarketDataHub` | Good for gradually moving existing connectors behind the forwarding boundary. |
| Restart-safe order routing | Live trading processes that need idempotency across restarts | `SQLiteStateStore` | Pass the store into `ForwardingRuntime` / `ZmqForwardingRuntime` to persist acknowledgements and private events. |

Market topics use normalized symbols, so `BTC/USDT` and `BTC-USDT` map to the
same topic key through `normalize_market_symbol`. Keep order payload symbols in
the exchange-native format expected by the broker adapter. Client-side realtime
event caches are bounded by `event_cache_size` and keep the latest events when a
consumer falls behind. Use `ForwardingClient.stats()` to refresh current
subscriptions and inspect pending and dropped event counts, or
`stats(refresh=False)` for a local-cache-only snapshot.

### Forwarding diagnostics and lifecycle

`ForwardingClient.stats()` returns a compact runtime snapshot:

| Field | Meaning |
|-------|---------|
| `connected` | Whether the client is currently connected |
| `event_cache_size` | Per-queue realtime cache bound; `None` means unbounded |
| `market_subscription_count` / `private_subscription_count` | Active local subscriptions |
| `pending_event_counts` | Locally cached tick, orderbook, bar, and private broker updates |
| `dropped_event_counts` | Events dropped because a bounded local queue was full |

When a bounded client queue is full, the oldest cached event is discarded before
the newest event is appended. Monitor `dropped_event_counts` to detect slow
strategy consumers and increase `event_cache_size` only when the extra local
memory is intentional. For ZeroMQ deployments, `ZmqForwardingRuntime.start_sync()`
and `stop_sync()` are idempotent; `runtime.is_running` and `await runtime.health()`
can be used by service supervisors to confirm endpoint and forwarder status.

## Core API summary

| Method | Description |
|--------|-------------|
| `get_tick(exchange, symbol)` | Query latest ticker |
| `get_depth(exchange, symbol, count=20)` | Query order book depth |
| `get_kline(exchange, symbol, period, count=20)` | Query OHLCV k-lines |
| `make_order(exchange, symbol, volume, price, order_type)` | Unified order creation |
| `cancel_order(exchange, symbol, order_id)` | Cancel order |
| `get_balance(exchange, symbol=None)` | Query balances |
| `get_position(exchange, symbol=None)` | Query positions |
| `async_get_tick(...)` / `async_make_order(...)` | Async APIs delegating to respective feeds |
| `subscribe(dataname, topics)` | Start WebSocket subscription |
| `get_data_queue(exchange)` | Read data pushed from WebSocket |
| `get_event_bus()` | Get EventBus instance |

## Forwarding API summary

| Object | Description |
|--------|-------------|
| `MarketEvent` / `OrderCommand` / `PrivateEvent` | Standard forwarding schemas |
| `MarketDataHub` | Normalizes and fans out market events by topic |
| `BtApiForwardingAdapter` | Bridges existing `BtApi.get_data_queue()` data into `MarketDataHub` |
| `OrderRouter` | Central order gateway with idempotency, basic risk checks, and private events |
| `SQLiteStateStore` | Persists command acknowledgements and private events |
| `ForwardingRuntime` / `ForwardingClient` | Embedded in-process runtime and client |
| `ZmqForwardingRuntime` / `ZmqForwardingClient` | Multi-process ZeroMQ runtime and client |
| `MAX_MESSAGE_BYTES` / `normalize_market_symbol` | Shared transport size guard and market topic symbol normalizer |

## Development and tests

Please refer to the Chinese section for the complete test and roadmap details. The core commands are the same:

```bash
pip install -e ".[dev]"
./scripts/run_tests.sh --help
pytest tests/test_bt_api_quality.py \
  tests/test_event_bus.py \
  tests/core/test_async_context.py \
  tests/gateway/test_config.py -q
ruff check bt_api_py tests
mypy bt_api_py --ignore-missing-imports
```

## Roadmap and FAQ

- See full roadmap in the section below in Chinese.
- Major short-term goals include improving WebSocket support for Bybit/Gate.io and adding historical replay in backtesting.
- FAQ section in Chinese below covers Python versions, adding new exchanges, WebSocket handling, sandbox usage, rate limits, and support channels.

## License and support

- License: MIT
- Author: **cloudQuant**
- Contact: yunjinqi@gmail.com
- Issues: https://github.com/cloudQuant/bt_api_py/issues

<a id="zh"></a>
## 中文

[🇺🇸 Switch to English](#en)

**bt_api_py** 是一个统一多交易所交易 API 框架，面向量化交易、套利执行、做市和多账户资产管理场景。它把不同交易所的 REST、异步 REST 和 WebSocket 接口统一到同一套 Python API 上，尽量减少接入层重复工作。

**快速入口**

- [在线文档](https://cloudquant.github.io/bt_api_py/)
- [安装指南](https://cloudquant.github.io/bt_api_py/getting-started/installation/)
- [快速开始](https://cloudquant.github.io/bt_api_py/getting-started/quickstart/)
- [仓库内文档目录](docs/)
- [问题反馈](https://github.com/cloudQuant/bt_api_py/issues)

## 为什么用 bt_api_py

- **统一接口**: 通过 `BtApi` 管理 Binance、OKX、HTX、CTP、Interactive Brokers 等不同交易所。
- **三种调用模式**: 同时支持同步 REST、异步 REST、WebSocket 订阅。
- **标准化数据模型**: `Ticker`、`OrderBook`、`Bar`、`Order`、`Position`、`Balance` 等容器统一字段语义。
- **可扩展架构**: 基于 Registry 和 Adapter 模式，新增交易所时不需要修改核心入口。
- **事件驱动**: 内置 `EventBus`，适合策略执行、行情转发和状态订阅。
- **行情与交易转发**: 内置 forwarding 运行时，一条行情连接可以服务多个策略，一个账户可以通过中心化路由服务多个交易客户端。

## 适用场景

- **量化交易策略开发**: 用统一接口减少多交易所策略重复代码。
- **套利交易系统**: 同时连接多个交易所，统一读取行情、账户和订单状态。
- **做市系统**: 通过 WebSocket 推送处理低延迟行情和订单簿变化。
- **资产管理平台**: 统一管理多交易所账户、持仓和余额。
- **交易机器人**: 结合事件驱动机制构建自动化执行流程。

## 核心特性

### 多交易所统一接口
通过 `BtApi` 类统一管理 Binance、OKX、CTP（中国期货）、Interactive Brokers 等交易所，一套代码适配多个平台。

### 三种 API 模式
- **同步 REST API**: 适合脚本、小型工具和回测场景。
- **异步 REST API**: 适合高并发采集、批量查询和任务编排。
- **WebSocket 实时推送**: 适合低延迟行情订阅和事件驱动交易。

### 即插即用架构
基于 Registry 设计模式，新增交易所只需实现接口并注册，无需修改核心代码。

### 事件驱动机制
内置 `EventBus` 事件总线，可处理行情更新、订单变化、成交通知等异步事件。

### 行情与交易转发
`bt_api_py.forwarding` 提供轻量级网关层，用于把交易所连接共享给多个策略进程：

- `MarketDataHub` 按 topic 扇出标准化 `MarketEvent`，例如 `md.BINANCE.SWAP.BTC-USDT.tick`。
- `OrderRouter` 集中处理账户级下单、撤单、基础风控、幂等和私有订单/成交/账户事件。
- `ForwardingClient` 适合嵌入式、本地测试和单进程策略运行。
- `ZmqForwardingRuntime` / `ZmqForwardingClient` 提供 ZeroMQ `PUB/SUB` 行情流和 `ROUTER/DEALER` 交易命令通道。
- `SQLiteStateStore` 持久化命令确认和私有事件，保证幂等下单在进程重启后仍可恢复。
- 转发消息进入 JSON/ZMQ 传输路径前会受导出的 `MAX_MESSAGE_BYTES` 上限保护。
- 嵌入式客户端使用 `command_timeout` 保护同步命令调用；账户、持仓和开放订单查询在无 handler 或超时时回退到本地缓存，下单和撤单仍会向调用方暴露命令失败。
- 客户端实时事件队列默认使用 `event_cache_size=4096` 限制慢消费者内存增长；只有策略 runner 明确需要本地无限积压事件时才建议设置 `event_cache_size=None`。
- `ForwardingClient.stats()` 默认会刷新当前订阅，然后报告待消费和已丢弃的实时事件数量，便于诊断慢消费者而不需要读取内部队列；如只想查看本地缓存快照，可使用 `stats(refresh=False)`。

推荐的职责边界是：交易所连接、行情扇出、账户共享、风控、幂等和传输协议都放在
`bt_api_py.forwarding`；Backtrader 这类策略引擎只通过轻量客户端适配层消费这个边界，
不直接负责交易所 socket 或账户路由。

### 标准化数据容器
提供 20+ 种标准化数据类型：
- **行情数据**: `Ticker`、`OrderBook`、`Bar`、`MarkPrice`、`FundingRate`
- **交易数据**: `Order`、`Trade`、`Position`、`Balance`、`Account`
- **其他数据**: `Symbol`、`Instrument`、`Liquidation`、`Greek`

### 跨平台支持
项目当前以 `Python 3.11-3.13` 为兼容目标（`3.14` 为 canary），CI 覆盖 Linux、macOS 和 Windows。

<!-- BEGIN GENERATED:EXCHANGE_SUPPORT_OVERVIEW -->
> 测试状态建议通过 `bash scripts/run_exchange_tests.sh <name>` 复核，当前口径更新于 2026-04-06。

### ✅ 已完整支持（REST + WebSocket + 测试通过）

| 交易所 | 代码 | 现货 | 合约 | 期权 | 股票 | 测试状态 | 说明 |
| -------- | -------- | -------- | -------- | -------- | -------- | -------- | -------- |
| **Binance** | `BINANCE___SPOT` / `BINANCE___SWAP` 等 | ✅ | ✅ | ✅ | — | ✅ 通过 | 现货、合约、杠杆、期权、算法交易、网格、挖矿、质押、钱包、子账户、VIP借币 |
| **HTX (Huobi)** | `HTX___SPOT` / `HTX___USDT_SWAP` 等 | ✅ | ✅ | ✅ | — | ✅ 通过 | 现货、杠杆、U本位永续、币本位永续、期权 |
| **CTP (中国期货)** | `CTP___FUTURE` | — | ✅ | — | — | ✅ 通过 | 中国期货市场（上期所、大商所、郑商所、中金所） |
| **Interactive Brokers** | `IB_WEB___STK` / `IB_WEB___FUT` | — | — | — | ✅ | ✅ 通过 | 美股、期货（通过 Web API） |

### 🔧 已实现 API（按当前主要缺口分组）

首页只展示分组摘要；逐交易所测试状态见 [详细状态页](docs/exchanges/EXCHANGE_STATUS.md)。

- `OKX`: REST 已实现，WebSocket 部分实现。当前主要工作是修正现有 mock 路径问题，并补齐 WebSocket 覆盖。
- `Bybit`、`Bitget`、`Kraken`、`Gate.io`、`Upbit`、`Crypto.com`、`HitBTC`、`Phemex`、`Gemini`: REST 已实现。当前主要缺口是 WebSocket 能力、实时订阅适配和对应测试覆盖。
- `KuCoin`、`MEXC`、`Bitfinex`、`Coinbase`、`BYDFi`: REST 已实现。当前主要工作是修复已知失败项、兼容性问题，并补稳定性回归测试。
- `Hyperliquid`、`dYdX`: 实现存在，但仓库内测试资产仍不足。补齐可执行测试文件和验证资产后，再提升到更高支持等级。

### 📋 已注册（基础框架就绪）

40+ 个交易所已完成注册或基础框架接入，但还需要继续补实现、测试或文档后，再提升对外状态。

> **总计**: 4 个完整支持 + 17 个已实现 API + 40+ 个已注册 = **73+ 个交易所**
>
> **说明**: 该分级采用保守口径；只有 REST、WebSocket 和测试资产同时满足时，才会提升到“完整支持”。
<!-- END GENERATED:EXCHANGE_SUPPORT_OVERVIEW -->

## 安装与兼容性

| 项目 | 当前支持 |
|------|----------|
| Python | `3.11` - `3.13`（阻塞发布）；`3.14`（canary） |
| 操作系统 | Linux, macOS, Windows |
| 安装方式 | PyPI, 源码开发安装 |
| 主要接口 | REST, Async REST, WebSocket |

### 方式一：从 PyPI 安装（推荐）

```bash
pip install bt_api_py
```

### 方式二：从源码安装（开发模式）

从源码安装会编译 Cython 扩展和 CTP SWIG C++ 扩展，请先安装对应平台的编译环境。

#### macOS

```bash
xcode-select --install
brew install swig
```

#### Linux（Debian/Ubuntu）

```bash
sudo apt install swig g++
```

#### Windows 11

```bash
winget install Microsoft.VisualStudio.2022.BuildTools
# 打开 Visual Studio Installer，勾选"使用C++的桌面开发"
winget install miniconda3
conda install -c conda-forge swig libiconv
```

完成前置环境后，再执行源码安装：

```bash
git clone --recurse-submodules https://github.com/cloudQuant/bt_api_py
cd bt_api_py
python -m pip install --upgrade pip
pip install -e .

# 如果你要参与开发
pip install -e ".[dev]"
```

如果已经用普通 `git clone` 拉取了仓库，需要补拉交易所插件子模块：

```bash
git submodule update --init --recursive --jobs 8
```

需要同时检查并安装 `bt_api_*` 插件包时，使用仓库内脚本。默认策略是：已安装则跳过；未安装时先尝试本地子模块源码安装；源码不可安装或安装失败时再尝试 PyPI；仍失败则在 summary 中标记。

```bash
# 安装 bt_api_py 本体，并按源码优先策略安装全部插件
python scripts/install_bt_api_submodules.py --with-root --editable-root --strategy source-first

# 只从本地子模块源码安装，不从 PyPI 兜底
python scripts/install_bt_api_submodules.py --with-root --editable-root --strategy source-only --upgrade

# 只安装指定插件
python scripts/install_bt_api_submodules.py bt_api_base bt_api_binance bt_api_okx --strategy source-first

# 只检查安装状态，不安装任何包
python scripts/install_bt_api_submodules.py --strategy none
```

### 可选依赖

| Extra | 用途 |
|------|------|
| `bt_api_py[all]` | 安装所有可选依赖 |
| `bt_api_py[dev]` | `pytest`、`ruff`、`mypy` 等开发工具 |
| `bt_api_py[security]` | `security_compliance`、OAuth/JWT、加密、密码哈希 |
| `bt_api_py[ib]` | Interactive Brokers 原生支持 |
| `bt_api_py[ib_web]` | IB Web API 和浏览器自动化相关依赖 |
| `bt_api_py[visualization]` | 图表与可视化工具 |

```bash
pip install bt_api_py[all]
pip install bt_api_py[dev]
pip install bt_api_py[security]
```

## 快速开始

### 同步行情查询

```python
from bt_api_py import BtApi

exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": True,
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
print(ticker)
```

### 统一下单

```python
from decimal import Decimal
from bt_api_py import OrderRequest, OrderType, Side

order = api.make_order(
    "BINANCE___SPOT",
    OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.001"),
        price=Decimal("50000"),
        account_id="paper",
        client_order_id="cid-1",
    ),
)
print(order)
```

### 异步请求

```python
import asyncio
from bt_api_py import BtApi

async def main():
    api = BtApi(
        exchange_kwargs={
            "BINANCE___SPOT": {
                "api_key": "your_api_key",
                "secret": "your_secret",
                "testnet": True,
            }
        }
    )

    ticker = await api.async_get_tick("BINANCE___SPOT", "BTCUSDT")
    print(ticker)

asyncio.run(main())
```

### WebSocket 订阅

```python
api.subscribe(
    "BINANCE___SPOT___BTCUSDT",
    [
        {"topic": "ticker", "symbol": "BTCUSDT"},
        {"topic": "depth", "symbol": "BTCUSDT"},
    ],
)

data_queue = api.get_data_queue("BINANCE___SPOT")
message = data_queue.get(timeout=10)
print(type(message).__name__, message)
```

### 嵌入式转发运行时

```python
import asyncio

from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.forwarding import ForwardingClient, ForwardingRuntime


async def main():
    runtime = ForwardingRuntime(MockBrokerAdapter())
    await runtime.start()

    client = ForwardingClient(
        bus=runtime.bus,
        exchange="SIM",
        market_type="SPOT",
        account_id="paper",
        strategy_id="demo_strategy",
        command_timeout=2.0,
        event_cache_size=4096,
    )
    client.connect()
    client.subscribe("RB2510")

    runtime.market_data.publish_tick(
        exchange="SIM",
        market_type="SPOT",
        symbol="RB2510",
        price=3500.0,
    )
    print(client.poll_tick("RB2510").price)
    print(client.stats(refresh=False)["dropped_event_counts"])

    order = client.submit_order(
        {
            "symbol": "RB2510",
            "side": "buy",
            "size": 1,
            "order_type": "limit",
            "price": 3500.0,
        }
    )
    print(order["order_id"])

    await runtime.stop()


asyncio.run(main())
```

### ZeroMQ 转发服务

> **安全提示（仅限 mock/本地）：** 转发网关默认处于只读 loopback/IPC 模式。远程 TCP 和写操作需要认证部署（CurveZMQ + ACL），该能力在后续迭代交付。在此之前不要把端点暴露到公网，也不要接入生产凭证。

```python
from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.forwarding import ZmqForwardingClient, ZmqForwardingRuntime

runtime = ZmqForwardingRuntime(
    MockBrokerAdapter(),
    market_endpoint="tcp://127.0.0.1:7001",
    command_endpoint="tcp://127.0.0.1:7002",
    private_endpoint="tcp://127.0.0.1:7003",
)
runtime.start_sync()

client = ZmqForwardingClient(
    market_endpoint="tcp://127.0.0.1:7001",
    command_endpoint="tcp://127.0.0.1:7002",
    private_endpoint="tcp://127.0.0.1:7003",
    exchange="SIM",
    market_type="SPOT",
    account_id="paper",
    strategy_id="demo_strategy",
    command_timeout_ms=2000,
    event_cache_size=4096,
)
client.connect()
client.subscribe("RB2510")
print(client.stats(refresh=False))
```

### Forwarding 部署模式选择

| 模式 | 适合场景 | 传输方式 | 说明 |
|------|----------|----------|------|
| 嵌入式运行时 | 单元测试、本地仿真、单进程策略 | `InMemoryForwardingBus` | 使用 `ForwardingRuntime` + `ForwardingClient`；`command_timeout` 单位是秒。 |
| ZeroMQ 服务 | 多策略进程或多个服务共享同一个上游行情和账户网关 | 行情/私有事件走 `PUB/SUB`，交易命令走 `ROUTER/DEALER` | 使用 `ZmqForwardingRuntime` + `ZmqForwardingClient`；`command_timeout_ms` 单位是毫秒。 |
| 现有 `BtApi` 桥接 | 复用当前交易所适配器的 WebSocket 队列 | `BtApiForwardingAdapter` 写入 `MarketDataHub` | 适合把已有连接逐步迁移到 forwarding 边界后面。 |
| 可恢复订单路由 | 实盘进程需要跨重启保持下单幂等 | `SQLiteStateStore` | 将 state store 传入 `ForwardingRuntime` / `ZmqForwardingRuntime`，持久化命令确认和私有事件。 |

行情 topic 使用标准化 symbol，`BTC/USDT` 和 `BTC-USDT` 会通过
`normalize_market_symbol` 映射到同一个 topic key。订单 payload 的 symbol 应保持交易所
adapter 期望的原始格式。客户端实时事件缓存受 `event_cache_size` 限制，消费者落后时会保留最新事件。可以用 `ForwardingClient.stats()` 刷新当前订阅并查看待消费和已丢弃事件数量；如只想查看本地缓存快照，可使用 `stats(refresh=False)`。

### Forwarding 诊断和生命周期

`ForwardingClient.stats()` 会返回一份轻量运行时快照：

| 字段 | 含义 |
|------|------|
| `connected` | client 当前是否已连接 |
| `event_cache_size` | 每个实时事件队列的缓存上限；`None` 表示不限制 |
| `market_subscription_count` / `private_subscription_count` | 当前本地订阅数量 |
| `pending_event_counts` | 本地待消费的 tick、orderbook、bar 和私有 broker update 数量 |
| `dropped_event_counts` | 因有界队列已满而丢弃的事件数量 |

当有界队列已满时，client 会先丢弃最老的缓存事件，再追加最新事件。实盘中应监控
`dropped_event_counts` 来发现消费过慢的策略，只有确认需要更多本地积压能力时才调大
`event_cache_size`。ZeroMQ 部署下，`ZmqForwardingRuntime.start_sync()` 和
`stop_sync()` 都是幂等的；服务管理器可以通过 `runtime.is_running` 和
`await runtime.health()` 检查 endpoint 与转发线程状态。

## 核心 API 一览

| 方法 | 说明 |
|------|------|
| `get_tick(exchange, symbol)` | 查询最新行情 |
| `get_depth(exchange, symbol, count=20)` | 查询订单簿深度 |
| `get_kline(exchange, symbol, period, count=20)` | 查询 K 线 |
| `make_order(exchange, symbol, volume, price, order_type)` | 统一下单入口 |
| `cancel_order(exchange, symbol, order_id)` | 撤单 |
| `get_balance(exchange, symbol=None)` | 查询余额 |
| `get_position(exchange, symbol=None)` | 查询持仓 |
| `async_get_tick(...)` / `async_make_order(...)` | 异步接口，自动代理到对应 feed |
| `subscribe(dataname, topics)` | 发起 WebSocket 订阅 |
| `get_data_queue(exchange)` | 读取 WebSocket 推送结果 |
| `get_event_bus()` | 获取事件总线实例 |

## Forwarding API 一览

| 对象 | 说明 |
|------|------|
| `MarketEvent` / `OrderCommand` / `PrivateEvent` | 转发层标准消息模型 |
| `MarketDataHub` | 按 topic 标准化并扇出行情事件 |
| `BtApiForwardingAdapter` | 将现有 `BtApi.get_data_queue()` 数据桥接到 `MarketDataHub` |
| `OrderRouter` | 中心化订单网关，支持幂等、基础风控和私有事件 |
| `SQLiteStateStore` | 持久化命令确认和私有事件 |
| `ForwardingRuntime` / `ForwardingClient` | 嵌入式进程内运行时和客户端 |
| `ZmqForwardingRuntime` / `ZmqForwardingClient` | 多进程 ZeroMQ 运行时和客户端 |
| `MAX_MESSAGE_BYTES` / `normalize_market_symbol` | 共享传输消息大小保护和行情 topic symbol 标准化工具 |

## 仓库结构

- `bt_api_py/`: 核心包，包含 `BtApi`、注册表、数据容器、feeds、gateway、websocket 和风险管理模块。
- `tests/`: 单元测试、兼容性测试、网关测试和 WebSocket 测试。
- `docs/`: MkDocs 文档站点，按 Getting Started / Guides / Reference / Explanation 组织。
- `scripts/`: 开发和维护脚本，统一放置测试入口、文档生成和诊断脚本。
- `configs/`: 配置模板和示例文件，避免把环境样例继续堆在主目录。
- `examples/`: 网络测试和使用示例。
- `.github/workflows/`: CI、文档部署和发布流程。

### 主目录约定

- 根目录只保留包管理、文档入口和自动化配置，例如 `README.md`、`pyproject.toml`、`mkdocs.yml`、`Makefile`。
- 测试与运维脚本统一收纳到 `scripts/`，例如 `scripts/run_tests.sh`。
- 环境模板和样例配置统一收纳到 `configs/examples/`，例如 `configs/examples/security_compliance.env.example`、`configs/examples/hal.config.yaml.example`。

### 哪些配置文件需要留在主目录

- `pyproject.toml`、`mkdocs.yml`、`.readthedocs.yaml`、`.pre-commit-config.yaml` 这类文件通常需要留在根目录，因为对应工具默认就在仓库根层自动发现它们。
- `conftest.py` 也建议保留在根目录。这个仓库除了 `tests/` 之外，还有 `examples/network_tests/` 这类显式运行的测试入口；根层 `conftest.py` 才能统一对这些路径生效。
- 不会被仓库工具自动发现、只是给本地工具或环境使用的样例配置，不建议继续放在根目录，应该下沉到 `configs/examples/`。

## 文档

优先访问在线文档：**[https://cloudquant.github.io/bt_api_py/](https://cloudquant.github.io/bt_api_py/)**。

如果 GitHub Pages 站点暂时不可用，也可以直接查看仓库内的 [docs/](docs/) 目录。

### 核心文档

- [快速入门](https://cloudquant.github.io/bt_api_py/getting-started/quickstart/) - 5 分钟上手指南
- [安装指南](https://cloudquant.github.io/bt_api_py/getting-started/installation/) - 安装和环境准备
- [架构设计](https://cloudquant.github.io/bt_api_py/explanation/architecture/) - 核心架构和设计模式
- [使用指南](https://cloudquant.github.io/bt_api_py/guides/usage_guide/) - 常见调用方式和工程集成
- [开发者指南](https://cloudquant.github.io/bt_api_py/explanation/developer_guide/) - 如何扩展和贡献代码
- [更新日志](https://cloudquant.github.io/bt_api_py/getting-started/change_log/) - 最近文档和能力变更
- [仓库文档目录](docs/) - 本地浏览 `docs/` 全量内容

### 交易所指南

- [Binance](https://cloudquant.github.io/bt_api_py/exchanges/binance/) - 现货、合约、杠杆和算法交易文档
- [OKX](https://cloudquant.github.io/bt_api_py/exchanges/okx/) - 交易、资金、公共数据和算法接口
- [CTP 期货](https://cloudquant.github.io/bt_api_py/exchanges/ctp/quickstart/) - 中国期货接入快速入门
- [Interactive Brokers](https://cloudquant.github.io/bt_api_py/exchanges/ib/quickstart/) - IB Web API 使用指南

## 开发与测试

### 本地快速验证

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 查看仓库测试脚本帮助
./scripts/run_tests.sh --help

# 与 CI smoke suite 保持一致
pytest tests/test_bt_api_quality.py \
  tests/test_event_bus.py \
  tests/core/test_async_context.py \
  tests/gateway/test_config.py -q

# 代码质量
ruff check bt_api_py tests
mypy bt_api_py --ignore-missing-imports
```

### 完整基线测试

```bash
# 完整基线测试建议把安全相关依赖一起装上
# 其中 security extra 包含 PyJWT、cryptography、bcrypt
pip install -e ".[dev,security]"

# 可选：使用仓库脚本跑更常见的本地测试组合
./scripts/run_tests.sh -m "not slow and not network"

# 运行非网络、非集成基线测试
pytest tests -m "not network and not integration and not performance and not e2e" -q

# 生成覆盖率报告
pytest tests -m "not network and not integration and not performance and not e2e" \
  --cov=bt_api_py \
  --cov-report=html \
  --cov-report=xml
```

### CI 说明

- Push / Pull Request: 运行 `Quality Gates`、`Compatibility` 矩阵和 Ubuntu 完整基线测试。
- 兼容性矩阵: Linux、macOS、Windows GitHub-hosted runner x Python `3.11` 到 `3.13`（阻塞）+ `3.14`（canary）。
- Windows 说明: GitHub Actions 使用官方支持的 `windows-latest` hosted runner；项目兼容目标包含 Windows 11。

### 需要真实账户或网络的测试

```bash
# 运行前请先配置 API 密钥、测试网账户和 IP 白名单
pytest tests -m binance -v
pytest tests -m okx -v
pytest tests -m ctp -v
```

## 路线图

### 近期计划 (v0.16-v0.20)

- [x] 添加 HTX (Huobi) 交易所完整支持（现货、杠杆、合约、期权）
- [x] 增加行情与交易转发 MVP（MarketDataHub、OrderRouter、ZeroMQ、SQLite 幂等持久化）
- [ ] 完善 Bybit、Gate.io 等交易所的 WebSocket 支持
- [ ] 完善回测框架，支持历史数据回放
- [ ] WebSocket 断线重连优化
- [ ] 性能优化和稳定性提升

### 长期计划 (v1.0+)

- [ ] 将更多已注册交易所提升至完整支持
- [ ] 内置风险管理模块
- [ ] 策略回测可视化界面
- [ ] 云端部署支持
- [ ] 机器学习集成

## 常见问题 (FAQ)

### Q: 支持哪些 Python 版本？
当前兼容目标是 Python `3.11` 到 `3.13`（`3.14` 为 canary，不阻塞发布）。默认 CI 环境为 Python `3.11`，推荐与之保持一致。

### Q: 如何添加新的交易所？
请参考 [开发者指南](https://cloudquant.github.io/bt_api_py/explanation/developer_guide/)，实现 `AbstractFeed` 接口并注册到 `ExchangeRegistry` 即可。基本步骤：
1. 在 `feeds/` 下创建交易所实现目录
2. 在 `exchange_registers/` 下创建注册模块
3. 在 `errors/` 下添加错误翻译器（可选）

### Q: WebSocket 连接断开怎么办？
框架内置了自动重连能力。推荐通过 `subscribe()` 发起订阅，再用 `get_data_queue()` 或 `get_event_bus()` 消费推送数据，这样断线恢复后上层处理逻辑不需要改写。

### Q: 支持模拟交易吗？
支持！Binance 和 OKX 都支持测试网模式，在配置中设置 `testnet=True` 即可。CTP 也支持 SimNow 模拟环境。

### Q: 如何处理交易所 API 限流？
框架内置了速率限制器 (`rate_limiter.py`)，会自动根据各交易所的限制进行请求控制。您也可以自定义限流策略。

### Q: Security Compliance 模块需要额外安装什么？
如果你要使用 `security_compliance` 相关能力，或运行完整基线测试中的安全测试，请安装 `bt_api_py[security]`。其中包含 `PyJWT`、`cryptography` 和 `bcrypt`。仓库里的环境模板位于 `configs/examples/security_compliance.env.example`。

### Q: 如何获取技术支持？
可以通过以下方式获取帮助：
- [在线文档](https://cloudquant.github.io/bt_api_py/)
- [仓库内文档目录](docs/)
- [GitHub Issue](https://github.com/cloudQuant/bt_api_py/issues)
- 发送邮件至 yunjinqi@gmail.com

### Q: 项目测试覆盖率如何？
项目包含大规模单元测试和兼容性 smoke suite。当前推荐先跑 README 上面的 smoke suite，再根据需要执行完整基线测试并生成覆盖率报告。

## 贡献

我们欢迎所有形式的贡献！无论是报告 Bug、提出新功能建议、改进文档还是提交代码。

### 如何贡献

1. Fork 本仓库
2. 创建您的特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交您的更改（只 stage 明确的文件路径，不要整树暂存）
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启一个 Pull Request，**目标分支选择 `dev`**

> `master` 仅接受 promotion 与 hotfix；交易所适配器变更请到对应
> `bt_api/bt_api_*` 插件仓提 PR。路由表见
> [docs/governance/branch-model.md](docs/governance/branch-model.md)。

详细贡献指南请查看 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [开发者指南](https://cloudquant.github.io/bt_api_py/explanation/developer_guide/)。

### 安全与行为准则

- 安全漏洞请勿开公开 issue，按 [SECURITY.md](SECURITY.md) 的私密通道报告；
  **绝不在 issue/PR 中张贴 API 密钥或账户信息**
- 社区行为规范见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

## 许可证

本项目采用 [MIT License](https://opensource.org/licenses/MIT) 开源许可。您可以自由使用、修改和分发本项目。

## 作者

**cloudQuant** - [GitHub](https://github.com/cloudQuant) - yunjinqi@gmail.com

## 致谢

感谢所有为本项目做出贡献的开发者！

## 联系我们

- Email: yunjinqi@gmail.com
- Issues: [GitHub Issues](https://github.com/cloudQuant/bt_api_py/issues)
- 文档: [在线文档](https://cloudquant.github.io/bt_api_py/)

---

如果这个项目对你有帮助，请给我们一个 Star！

[![Star History Chart](https://api.star-history.com/svg?repos=cloudQuant/bt_api_py&type=Date)](https://star-history.com/#cloudQuant/bt_api_py&Date)
