# WebSocket 订阅参考

WebSocket 实时行情推送功能说明。

## 概述

bt_api_py 支持通过 WebSocket 订阅实时行情推送，包括：

- K 线数据 (kline/tick_bar)
- 深度数据 (depth)
- 实时成交 (trade)
- 行情快照 (ticker)

## 订阅方式

### 方式一：使用 subscribe 方法

```python
api.subscribe("BINANCE___SPOT___BTCUSDT", [
    {"topic": "kline", "symbol": "BTCUSDT", "period": "1m"},
    {"topic": "depth", "symbol": "BTCUSDT"},
])

```bash

### 方式二：使用便捷方法

```python

# 订阅 ticker

api.subscribe_ticker("BINANCE___SPOT", "BTCUSDT", callback=on_ticker)

# 订阅 kline

api.subscribe_kline("BINANCE___SPOT", "BTCUSDT", "1m", callback=on_kline)

# 订阅 depth

api.subscribe_depth("BINANCE___SPOT", "BTCUSDT", callback=on_depth)

```bash

## 主题类型

### kline (K 线)

订阅 K 线推送：

```python
api.subscribe("BINANCE___SPOT___BTCUSDT", [
    {"topic": "kline", "symbol": "BTCUSDT", "period": "1m"},
])

```bash
支持的周期：`1m`, `3m`, `5m`, `15m`, `30m`, `1H`, `1D`

### depth (深度)

订阅订单簿推送：

```python
api.subscribe("BINANCE___SPOT___BTCUSDT", [
    {"topic": "depth", "symbol": "BTCUSDT"},
])

```bash

### ticker (行情快照)

订阅 24 小时行情快照：

```python
api.subscribe("BINANCE___SPOT___BTCUSDT", [
    {"topic": "ticker", "symbol": "BTCUSDT"},
])

```bash

### trade (实时成交)

订阅实时成交推送：

```python
api.subscribe("BINANCE___SPOT___BTCUSDT", [
    {"topic": "trade", "symbol": "BTCUSDT"},
])

```bash

## 接收推送数据

### 从队列获取

```python
data_queue = api.get_data_queue("BINANCE___SPOT")

while True:
    data = data_queue.get()
    data.init_data()
    print(f"事件: {data.get_event()}")
    print(f"数据: {data}")

```bash

### 使用回调函数

```python
def on_ticker(ticker):
    ticker.init_data()
    print(f"价格更新: {ticker.get_last_price()}")

# 注册回调

api.event_bus.subscribe("ticker", on_ticker)

# 订阅并运行

api.subscribe_ticker("BINANCE___SPOT", "BTCUSDT", callback=on_ticker)
api.run()

```bash

## 事件类型

| 事件 | 说明 | 数据容器 |

|------|------|----------|

| `TickerEvent` | 行情快照 | `TickerData` |

| `KlineEvent` | K 线更新 | `BarData` |

| `DepthEvent` | 深度更新 | `OrderBookData` |

| `TradeEvent` | 实时成交 | `TradeData` |

| `OrderEvent` | 订单状态 | `OrderData` |

| `AccountEvent` | 账户变化 | `AccountData` |

| `PositionEvent` | 持仓变化 | `PositionData` |

## 运行模式

### 阻塞运行

```python
api.run()  # 阻塞，保持 WebSocket 连接

```bash

### 后台运行

```python
import threading

# 在后台线程运行 WebSocket

threading.Thread(target=api.run, daemon=True).start()

# 主线程继续执行其他操作

while True:

# 处理队列数据
    data = data_queue.get()

# ...

```bash

### 使用 asyncio

```python
import asyncio

async def main():

# 初始化 API
    api = BtApi(exchange_kwargs=...)

# 启动 WebSocket
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, api.run)

# 运行

asyncio.run(main())

```bash

## 心跳保持

WebSocket 连接会自动发送心跳包保持连接。如果连接断开，会自动重连。

## 交易所支持

| 交易所 | kline | depth | ticker | trade |

|--------|-------|-------|--------|-------|

| Binance | ✅ | ✅ | ✅ | ✅ |

| OKX | ✅ | ✅ | ✅ | ✅ |

| CTP | ✅ | ❌ | ❌ | ❌ |

| IB Web | ✅ | ❌ | ✅ | ✅ |

## 订阅限制

不同交易所对订阅数量有限制：

| 交易所 | 限制 |

|--------|------|

| Binance | 1024 个连接/IP |

| OKX | 240 个请求/秒 |

| IB Web | 取决于账户类型 |

## 最佳实践

1. **按需订阅**- 只订阅需要的交易对和主题

2.**及时取消订阅**- 不需要的订阅及时取消
3.**处理异常**- WebSocket 可能断连，需要做好异常处理
4.**使用回调**- 回调模式比轮询队列更高效
5.**控制订阅数量**- 避免超过交易所限制

## 完整示例

```python
from bt_api_py import BtApi
import queue

# 配置交易所

exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": "...",
        "secret": "...",
        "testnet": True,
    }
}

# 创建 API 实例

api = BtApi(exchange_kwargs=exchange_kwargs)
data_queue = api.get_data_queue("BINANCE___SPOT")

# 定义回调函数

def on_ticker(ticker):
    ticker.init_data()
    print(f"价格: {ticker.get_last_price()}")

def on_kline(bar):
    bar.init_data()
    print(f"K 线: {bar.get_close_price()}")

# 注册回调

api.event_bus.subscribe("ticker", on_ticker)
api.event_bus.subscribe("kline", on_kline)

# 订阅

api.subscribe_ticker("BINANCE___SPOT", "BTCUSDT")
api.subscribe_kline("BINANCE___SPOT", "BTCUSDT", "1m")

# 运行

api.run()

```bash

## 断线重连

WebSocket 断线后会自动重连，重连策略：

1.**立即重连**- 断线后立即尝试重连
2.**指数退避**- 重连失败后，间隔时间指数增长
3.**最大重试次数** - 达到最大次数后停止重连

```python

# 配置重连参数

exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": "...",
        "secret": "...",
        "ping_interval": 20,  # 心跳间隔（秒）
        "ping_timeout": 10,   # 心跳超时（秒）
        "reconnect": True,    # 启用重连
        "max_reconnect": 10,  # 最大重连次数
    }
}

```bash
