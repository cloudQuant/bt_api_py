# BtApi API 参考

本文档提供 `BtApi` 类的完整 API 参考。

## 详细文档

- [数据容器详解](api_reference/data_containers.md) - Ticker、Order、Bar 等数据容器
- [WebSocket 订阅](api_reference/websocket.md) - 实时行情订阅
- [事件系统](api_reference/event_system.md) - EventBus 事件总线
- [Binance API](api_reference/binance.md) - Binance 交易所专用接口
- [OKX API](api_reference/okx.md) - OKX 交易所专用接口
- [CTP API](api_reference/ctp.md) - CTP 期货专用接口
- [IB API](api_reference/ib.md) - Interactive Brokers 专用接口

---

## 概述

`BtApi` 是 bt_api_py 的核心类，提供统一的多交易所交易接口。通过 `BtApi`，你可以使用相同的方法访问不同的交易所，无需关心底层实现差异。

```python
from bt_api_py import BtApi

api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {"api_key": "...", "secret": "..."},
    "OKX___SPOT": {"api_key": "...", "secret": "...", "passphrase": "..."},
})

```

---

## 初始化

### `__init__(exchange_kwargs, debug=True, event_bus=None)`

创建 BtApi 实例。

- *参数：**

| 参数 | 类型 | 默认值 | 说明 |

|------|------|--------|------|

| `exchange_kwargs` | dict | *必需*| 交易所配置字典 |

| `debug` | bool | `True` | 是否启用调试模式（日志输出） |

| `event_bus` | EventBus | `None` | 自定义事件总线 |

- *示例：**

```python
from bt_api_py import BtApi, CtpAuthConfig, IbWebAuthConfig

exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": True,
    },
    "OKX___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "passphrase": "your_passphrase",
    },
    "CTP___FUTURE": {
        "auth_config": CtpAuthConfig(
            broker_id="9999",
            user_id="your_user_id",
            password="your_password",
            md_front="tcp://...",
            td_front="tcp://...",
        )
    },
    "IB_WEB___STK": {
        "auth_config": IbWebAuthConfig(
            account_id="your_account_id",
        )
    },
}

api = BtApi(exchange_kwargs=exchange_kwargs, debug=True)

```

---

## 交易所管理

### `add_exchange(exchange_name, exchange_params)`

添加一个交易所到 API 实例。

### `list_exchanges()`

列出所有已添加的交易所。

### `list_available_exchanges()` (静态方法)

列出所有已注册可用的交易所。

- *示例：**

```python

# 查看支持的交易所

print(BtApi.list_available_exchanges())

# ['BINANCE___SPOT', 'BINANCE___SWAP', 'OKX___SPOT', 'OKX___SWAP', 'CTP___FUTURE', 'IB_WEB___STK', 'IB_WEB___FUT']

```

---

## 行情查询

### `get_tick(exchange_name, symbol, extra_data=None, **kwargs)`

获取最新行情数据。

- *返回：** `Ticker` 数据容器

- *示例：**

```python
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()
print(f"最新价: {ticker.get_last_price()}")

```

### `get_depth(exchange_name, symbol, count=20, extra_data=None, **kwargs)`

获取深度数据（订单簿）。

### `get_kline(exchange_name, symbol, period, count=20, extra_data=None, **kwargs)`

获取 K 线数据。

- *支持的周期：** `1m`, `5m`, `15m`, `30m`, `1H`, `1D`

- *示例：**

```python
klines = api.get_kline("BINANCE___SPOT", "BTCUSDT", "1m", count=100)
for bar in klines:
    bar.init_data()
    print(f"{bar.get_close_price()} - {bar.get_volume()}")

```

---

## 交易操作

### `make_order(exchange_name, symbol, volume, price, order_type, offset="open", post_only=False, client_order_id=None, extra_data=None, **kwargs)`

下单。

- *参数：**

| 参数 | 类型 | 默认值 | 说明 |

|------|------|--------|------|

| `exchange_name` | str | - | 交易所标识 |

| `symbol` | str | - | 交易对 |

| `volume` | float | - | 数量 |

| `price` | float | - | 价格（市价单传 0） |

| `order_type` | str | - | 订单类型：`"limit"` / `"market"` |

| `offset` | str | `"open"` | 开平方向（CTP 需要指定） |

| `post_only` | bool | `False` | 是否只做 maker |

- *返回：**`Order` 数据容器

### `cancel_order(exchange_name, symbol, order_id, extra_data=None,**kwargs)`

撤销订单。

### `cancel_all(exchange_name, symbol=None, extra_data=None, **kwargs)`

撤销所有订单。

### `query_order(exchange_name, symbol, order_id, extra_data=None, **kwargs)`

查询订单状态。

### `get_open_orders(exchange_name, symbol=None, extra_data=None, **kwargs)`

查询挂单。

---

## 账户查询

### `get_balance(exchange_name, symbol=None, extra_data=None, **kwargs)`

查询账户余额。

### `get_account(exchange_name, symbol="ALL", extra_data=None, **kwargs)`

查询账户信息。

### `get_position(exchange_name, symbol=None, extra_data=None, **kwargs)`

查询持仓。

- *示例：**

```python
positions = api.get_position("BINANCE___SWAP")
for pos in positions:
    pos.init_data()
    print(f"{pos.get_symbol_name()}: {pos.get_position()} 手")

```

---

## 异步方法

所有同步方法都有对应的异步版本，异步方法的结果会被推送到数据队列。

### 异步行情

```python
api.async_get_tick("BINANCE___SPOT", "BTCUSDT")

```

### 异步交易

```python
api.async_make_order("BINANCE___SPOT", "BTCUSDT", 0.001, 50000, "limit")

```

---

## 批量操作

### `get_all_ticks(symbol, extra_data=None, **kwargs)`

从所有已连接的交易所获取行情。

- *返回：**`dict` — `{exchange_name: ticker_data}`

### `get_all_balances(symbol=None, extra_data=None,**kwargs)`

从所有交易所查询余额。

### `get_all_positions(symbol=None, extra_data=None, **kwargs)`

从所有交易所查询持仓。

### `cancel_all_orders(symbol=None, extra_data=None, **kwargs)`

撤销所有交易所的所有订单。

---

## WebSocket 订阅

### `subscribe(dataname, topics)`

订阅行情推送。

- *主题类型：**

| 主题 | 说明 |

|------|------|

| `kline` | K 线数据 |

| `depth` | 深度数据 |

| `ticker` | 行情快照 |

| `trade` | 实时成交 |

- *示例：**

```python
api.subscribe("BINANCE___SPOT___BTCUSDT", [
    {"topic": "kline", "symbol": "BTCUSDT", "period": "1m"},
    {"topic": "depth", "symbol": "BTCUSDT"},
])

```

### `run()`

启动 WebSocket 连接并保持运行。

- *示例：**

```python
def on_ticker(ticker):
    ticker.init_data()
    print(f"价格更新: {ticker.get_last_price()}")

api.event_bus.subscribe("ticker", on_ticker)
api.subscribe("BINANCE___SPOT___BTCUSDT", [{"topic": "ticker", "symbol": "BTCUSDT"}])
api.run()

```

---

## 事件系统

`BtApi` 内置了 EventBus，支持发布-订阅模式。

### `get_event_bus()`

获取事件总线实例。

- *事件类型：**

| 事件 | 触发时机 |

|------|----------|

| `ticker` | 行情更新 |

| `kline` | K 线更新 |

| `depth` | 深度更新 |

| `order` | 订单状态变化 |

| `trade` | 成交 |

| `account` | 账户变化 |

| `position` | 持仓变化 |

| `error` | 错误发生 |

- *示例：**

```python
event_bus = api.get_event_bus()

def on_order_update(order):
    order.init_data()
    print(f"订单状态: {order.get_order_status()}")

event_bus.subscribe("order", on_order_update)

```

---

## 交易所标识

| 交易所 | 标识 | 说明 |

|--------|------|------|

| Binance 现货 | `BINANCE___SPOT` | 加密货币现货 |

| Binance 合约 | `BINANCE___SWAP` | USDT 本位合约 |

| Binance 期权 | `BINANCE___OPTION` | 欧式期权 |

| OKX 现货 | `OKX___SPOT` | 加密货币现货 |

| OKX 合约 | `OKX___SWAP` | 永续合约 |

| HTX 现货 | `HTX___SPOT` | 加密货币现货 |

| HTX 杠杆 | `HTX___MARGIN` | 杠杆交易 |

| HTX U本位合约 | `HTX___USDT_SWAP` | USDT 永续合约 |

| HTX 币本位合约 | `HTX___COIN_SWAP` | 反向永续合约 |

| HTX 期权 | `HTX___OPTION` | 期权交易 |

| CTP 期货 | `CTP___FUTURE` | 中国期货 |

| IB Web 股票 | `IB_WEB___STK` | 全球股票 |

| IB Web 期货 | `IB_WEB___FUT` | 全球期货 |

---

## 错误处理

```python
from bt_api_py.exceptions import ExchangeNotFoundError

try:
    ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
except ExchangeNotFoundError as e:
    print(f"交易所不存在: {e}")
except Exception as e:
    print(f"请求失败: {e}")

```

---

## 更多文档

- [数据容器详解](api_reference/data_containers.md)
- [WebSocket 订阅](api_reference/websocket.md)
- [事件系统](api_reference/event_system.md)
- [交易所专用 API](api_reference/)
