# bt_api_py 架构设计

本文档描述 bt_api_py 的核心架构设计、关键设计模式和数据流。

---

## 目录

- [整体架构](#整体架构)
- [核心组件](#核心组件)
- [Registry 模式](#registry-模式)
- [Container 数据容器](#container-数据容器)
- [Feed 交易所适配层](#feed-交易所适配层)
- [Stream 流式数据](#stream-流式数据)
- [数据队列与事件总线](#数据队列与事件总线)
- [认证配置体系](#认证配置体系)
- [异常体系](#异常体系)
- [交易所命名规范](#交易所命名规范)

---

## 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                      用户代码                            │
│                  bt_api = BtApi(...)                     │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                    BtApi (统一入口)                       │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐             │
│  │ exchange  │  │  data    │  │  event    │             │
│  │  feeds    │  │  queues  │  │   bus     │             │
│  └─────┬────┘  └────┬─────┘  └─────┬─────┘             │
└────────┼────────────┼──────────────┼────────────────────┘
         │            │              │
┌────────▼────────────▼──────────────▼────────────────────┐
│              ExchangeRegistry (全局注册表)                │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────────┐  │
│  │ feed_classes │ │stream_classes│ │exchange_data_cls │  │
│  └──────┬──────┘ └──────┬───────┘ └────────┬─────────┘  │
└─────────┼───────────────┼──────────────────┼────────────┘
          │               │                  │
┌─────────▼───────────────▼──────────────────▼────────────┐
│               交易所适配层 (feeds/)                       │
│  ┌────────────┐ ┌────────────┐ ┌──────┐ ┌────────────┐  │
│  │  Binance   │ │    OKX     │ │ CTP  │ │     IB     │  │
│  │ Spot/Swap  │ │ Spot/Swap  │ │Future│ │  STK/Web   │  │
│  └─────┬──────┘ └─────┬──────┘ └──┬───┘ └─────┬──────┘  │
└────────┼──────────────┼───────────┼────────────┼────────┘
         │              │           │            │
┌────────▼──────────────▼───────────▼────────────▼────────┐
│               数据容器层 (containers/)                    │
│  Ticker │ Bar │ OrderBook │ Order │ Position │ Balance   │
│  Trade  │ Symbol │ FundingRate │ MarkPrice │ Income     │
└─────────────────────────────────────────────────────────┘
```

---

## 核心组件

### BtApi — 统一入口

`BtApi` 是用户的主要交互接口，负责：

- **初始化交易所连接** — 根据 `exchange_kwargs` 自动创建各交易所的 Feed 实例
- **管理数据队列** — 每个交易所一个独立的 `queue.Queue`
- **REST 请求代理** — 通过 `get_request_api()` 获取交易所 Feed，调用同步/异步方法
- **WebSocket 订阅** — 通过 `subscribe()` 订阅实时数据流
- **余额管理** — `update_total_balance()` 统一查询所有交易所余额
- **历史数据下载** — `download_history_bars()` 支持分批下载大量历史K线

```python
bt_api = BtApi(exchange_kwargs, debug=True, event_bus=None)
```

### ExchangeRegistry — 全局注册表

注册表是整个系统的核心枢纽，实现交易所的即插即用：

| 注册类型 | 方法 | 说明 |
|----------|------|------|
| Feed 类 | `register_feed()` | REST API 适配类 |
| Stream 类 | `register_stream()` | WebSocket / 回调适配类 |
| ExchangeData 类 | `register_exchange_data()` | 交易所配置数据类 |
| Balance Handler | `register_balance_handler()` | 余额解析函数 |

### EventBus — 事件总线

轻量级发布/订阅事件分发，支持 Queue 模式和 Callback 模式：

```python
event_bus = EventBus()
event_bus.on("BarEvent", lambda data: print(data))
event_bus.emit("BarEvent", bar_data)
```

支持的事件类型包括：`BarEvent`、`OrderEvent`、`TradeEvent`、`TickEvent` 等。

---

## Registry 模式

Registry 模式是 bt_api_py 的核心设计，确保新增交易所时无需修改核心代码。

### 注册流程

每个交易所有一个独立的注册模块 `register_{exchange}.py`，在模块导入时自动完成注册：

```python
# feeds/register_binance.py

from bt_api_py.registry import ExchangeRegistry

def register_binance():
    # Swap
    ExchangeRegistry.register_feed("BINANCE___SWAP", BinanceRequestDataSwap)
    ExchangeRegistry.register_exchange_data("BINANCE___SWAP", BinanceExchangeDataSwap)
    ExchangeRegistry.register_balance_handler("BINANCE___SWAP", _binance_balance_handler)
    ExchangeRegistry.register_stream("BINANCE___SWAP", "subscribe", _binance_swap_subscribe_handler)

    # Spot
    ExchangeRegistry.register_feed("BINANCE___SPOT", BinanceRequestDataSpot)
    ExchangeRegistry.register_exchange_data("BINANCE___SPOT", BinanceExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("BINANCE___SPOT", _binance_balance_handler)
    ExchangeRegistry.register_stream("BINANCE___SPOT", "subscribe", _binance_spot_subscribe_handler)

# 模块导入时自动注册
register_binance()
```

### 自动注册机制

`BtApi` 初始化时通过 `try/except` 导入各注册模块，缺少依赖的交易所会静默跳过：

```python
try:
    import bt_api_py.feeds.register_binance  # noqa
except ImportError as e:
    _reg_logger.debug(f"Binance register skipped: {e}")
```

### 使用注册表

```python
# 创建 feed 实例
feed = ExchangeRegistry.create_feed("BINANCE___SWAP", data_queue, **params)

# 获取流式数据类
stream_cls = ExchangeRegistry.get_stream_class("BINANCE___SWAP", "market")

# 检查交易所是否可用
ExchangeRegistry.has_exchange("OKX___SPOT")  # True/False

# 列出所有已注册交易所
ExchangeRegistry.list_exchanges()  # ["BINANCE___SWAP", "BINANCE___SPOT", ...]
```

---

## Container 数据容器

数据容器是对交易所返回数据的标准化封装，统一不同交易所的数据格式。

### 容器类型一览

| 容器类 | 目录 | 说明 |
|--------|------|------|
| `AccountData` | `containers/accounts/` | 账户信息 |
| `BalanceData` | `containers/balances/` | 余额信息 |
| `BarData` | `containers/bars/` | K线数据 |
| `ExchangeData` | `containers/exchanges/` | 交易所配置 |
| `FundingRateData` | `containers/fundingrates/` | 资金费率 |
| `IncomeData` | `containers/incomes/` | 收益记录 |
| `LiquidationData` | `containers/liquidations/` | 强平数据 |
| `MarkPriceData` | `containers/markprices/` | 标记价格 |
| `OrderBookData` | `containers/orderbooks/` | 深度数据 |
| `OrderData` | `containers/orders/` | 订单数据 |
| `PositionData` | `containers/positions/` | 持仓数据 |
| `SymbolData` | `containers/symbols/` | 合约信息 |
| `TickerData` | `containers/tickers/` | 行情快照 |
| `TradeData` | `containers/trades/` | 成交数据 |
| `TimerData` | `containers/timers/` | 定时器 |
| `RequestData` | `containers/requestdatas/` | 请求数据 |

### 容器层次结构

```
containers/{type}/
├── {type}.py                # 抽象基类 (定义 get_*() 方法)
├── binance_{type}.py        # Binance 实现
├── okx_{type}.py            # OKX 实现
├── ctp/                     # CTP 子目录实现
└── ib/                      # IB 子目录实现
```

### 使用模式

```python
# 1. 从交易所 API 获取原始数据
data = feed.get_tick("BTC-USDT")

# 2. 调用 init_data() 解析原始数据 (或依赖 AutoInitMixin 自动初始化)
data.init_data()

# 3. 使用统一的 get_*() 方法访问数据
price = data.get_last_price()
volume = data.get_volume()
symbol = data.get_symbol()
```

### AutoInitMixin

`AutoInitMixin` 提供自动初始化功能，避免用户忘记调用 `init_data()`：

```python
class AutoInitMixin:
    def _ensure_init(self):
        if not getattr(self, '_initialized', False):
            self.init_data()
            self._initialized = True
        return self
```

### has_been_json_encoded 标志

容器支持从 JSON 编码后的数据构建，通过 `has_been_json_encoded` 标志区分原始数据和 JSON 数据：

- `False` — 数据来自交易所 REST/WebSocket 原始响应
- `True` — 数据来自 JSON 序列化后的重建

---

## Feed 交易所适配层

### AbstractVenueFeed 协议

所有交易所 Feed 必须满足 `AbstractVenueFeed` 协议，定义统一的方法签名：

**行情查询**：
- `get_tick(symbol)` — 获取最新价格
- `get_depth(symbol, count)` — 获取深度
- `get_kline(symbol, period, count)` — 获取K线

**交易操作**：
- `make_order(symbol, volume, price, order_type, ...)` — 下单
- `cancel_order(symbol, order_id)` — 撤单
- `cancel_all(symbol)` — 撤销所有订单
- `query_order(symbol, order_id)` — 查询订单
- `get_open_orders(symbol)` — 查询挂单

**账户查询**：
- `get_balance(symbol)` — 查询余额
- `get_account(symbol)` — 查询账户
- `get_position(symbol)` — 查询持仓

**异步版本**：
- 所有同步方法都有对应的 `async_` 前缀异步版本
- 异步方法将结果推送到数据队列而非直接返回

### AsyncWrapperMixin

为非 HTTP 场所（CTP/IB）提供默认异步包装，使用 `run_in_executor` 将同步方法包装为异步：

```python
class CtpFeed(Feed, AsyncWrapperMixin):
    def get_tick(self, symbol, extra_data=None, **kwargs):
        # CTP 同步实现
        ...
    # async_get_tick 自动由 AsyncWrapperMixin 提供
```

HTTP 场所（Binance/OKX）应覆盖为真正的异步实现。

### Feed 目录结构

```
feeds/
├── abstract_feed.py          # 统一场所协议
├── base_stream.py            # 流式数据基类
├── feed.py                   # Feed 基类实现
├── http_client.py            # HTTP 客户端工具
├── capability.py             # 能力声明
├── connection_mixin.py       # 连接管理混入
├── live_binance/             # Binance REST + WebSocket 实现
├── live_okx/                 # OKX REST + WebSocket 实现
├── live_binance_feed.py      # Binance Feed 入口
├── live_okx_feed.py          # OKX Feed 入口
├── live_ctp_feed.py          # CTP Feed 实现
├── live_ib_feed.py           # IB TWS Feed 实现
├── live_ib_web_feed.py       # IB Web API Feed 实现
├── register_binance.py       # Binance 注册模块
├── register_okx.py           # OKX 注册模块
├── register_ctp.py           # CTP 注册模块
├── register_ib.py            # IB 注册模块
└── register_ib_web.py        # IB Web API 注册模块
```

---

## Stream 流式数据

### BaseDataStream 抽象基类

所有 WebSocket/回调 流式数据连接器继承 `BaseDataStream`：

```python
class MyStream(BaseDataStream):
    def connect(self):
        """建立连接"""
        ...

    def disconnect(self):
        """断开连接"""
        ...

    def subscribe_topics(self, topics):
        """订阅主题"""
        # topics = [{"topic": "kline", "symbol": "BTC-USDT", "period": "1m"}]
        ...

    def _run_loop(self):
        """主循环，在独立 daemon 线程中运行"""
        ...
```

### 连接状态机

```
DISCONNECTED → CONNECTING → CONNECTED → AUTHENTICATED
                                ↓
                              ERROR
```

状态由 `ConnectionState` 枚举管理，状态变化时触发 `on_state_change()` 回调。

### 线程模型

- 每个 Stream 在独立的 **daemon 线程** 中运行
- 通过 `push_data()` 将数据推送到共享的 `queue.Queue`
- `wait_connected(timeout=30)` 可阻塞等待连接建立

---

## 数据队列与事件总线

### Queue 模式（默认）

每个交易所维护独立的 `queue.Queue`，WebSocket 和异步请求的结果都推送到队列中：

```python
data_queue = bt_api.get_data_queue("BINANCE___SWAP")
data = data_queue.get(timeout=10)  # 阻塞等待数据
data.init_data()
```

### Callback 模式（EventBus）

通过 `EventBus` 注册事件回调，适用于 CTP SPI / IB EWrapper 等回调驱动 API：

```python
event_bus = bt_api.get_event_bus()
event_bus.on("BarEvent", my_bar_handler)
event_bus.on("OrderEvent", my_order_handler)
```

### 数据流示意

```
交易所 API ──→ Feed/Stream ──→ Container (init_data) ──→ data_queue.put()
                                                              │
                                                    ┌────────┴────────┐
                                                    ▼                 ▼
                                              data_queue.get()  EventBus.emit()
                                              (轮询模式)         (回调模式)
```

---

## 认证配置体系

认证配置通过继承 `AuthConfig` 基类实现不同交易所的认证方式：

| 配置类 | 交易所 | 认证方式 |
|--------|--------|----------|
| `CryptoAuthConfig` | Binance, OKX | API Key + Secret + Passphrase(OKX) |
| `CtpAuthConfig` | CTP | Broker ID + User ID + Password + Auth Code |
| `IbAuthConfig` | IB TWS | Host + Port + Client ID |
| `IbWebAuthConfig` | IB Web API | Base URL + Account ID + OAuth/Cookie |

所有配置类都提供 `get_exchange_name()` 和 `to_dict()` 方法。

---

## 异常体系

```
BtApiError (基类)
├── ExchangeNotFoundError     # 交易所未注册
├── ExchangeConnectionError   # 连接失败
│   └── AuthenticationError   # 认证失败
├── RequestTimeoutError       # 请求超时
├── RequestError              # 请求失败
├── OrderError                # 下单/撤单失败
├── SubscribeError            # 订阅失败
└── DataParseError            # 数据解析失败
```

**规则**：始终使用自定义异常，禁止使用 `Exception` 或 `assert` 处理错误。

---

## 交易所命名规范

使用 **三下划线** 格式：`EXCHANGE___ASSET_TYPE`

| 标识 | 说明 |
|------|------|
| `BINANCE___SPOT` | Binance 现货 |
| `BINANCE___SWAP` | Binance 永续合约 |
| `OKX___SPOT` | OKX 现货 |
| `OKX___SWAP` | OKX 永续合约 |
| `CTP___FUTURE` | CTP 期货 (中国) |
| `IB___STK` | Interactive Brokers 股票 |
| `IB_WEB___STK` | IB Web API 股票 |

此命名贯穿整个系统：Registry 键、配置段、数据队列标识。

---

## 标准化符号格式

| 市场 | 格式 | 示例 |
|------|------|------|
| 加密货币 | `{BASE}-{QUOTE}` | `BTC-USDT`, `ETH-USDT` |
| CTP 期货 | 交易所合约代码 | `au2506`, `IF2503` |
| IB 股票 | `{SYMBOL}-{TYPE}-{EXCHANGE}` | `AAPL-STK-SMART` |

---

*最后更新: 2026-02-28*
