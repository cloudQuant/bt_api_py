# 事件系统参考

bt_api_py 内置的 EventBus 事件总线系统。

## 概述

EventBus 是一个发布-订阅模式的事件系统，允许解耦数据推送和处理逻辑。

## 基本用法

### 获取事件总线

```python
event_bus = api.get_event_bus()

```bash

### 订阅事件

```python
def handler(event_data):
    print(f"收到事件: {event_data}")

event_bus.subscribe("ticker", handler)

```bash

### 发布事件

```python
event_bus.publish("ticker", ticker_data)

```bash

### 取消订阅

```python
event_bus.unsubscribe("ticker", handler)

```bash

## 内置事件类型

| 事件名称 | 触发时机 | 数据类型 |

|----------|----------|----------|

| `ticker` | 行情更新时 | `TickerData` |

| `kline` | K 线更新时 | `BarData` |

| `depth` | 深度更新时 | `OrderBookData` |

| `trade` | 有新成交时 | `TradeData` |

| `order` | 订单状态变化时 | `OrderData` |

| `account` | 账户变化时 | `AccountData` |

| `position` | 持仓变化时 | `PositionData` |

| `error` | 发生错误时 | `Exception` |

## 事件数据

事件数据是交易所特定的数据容器，使用前需要调用 `init_data()`：

```python
def on_ticker(ticker):
    ticker.init_data()  # 重要：初始化数据
    price = ticker.get_last_price()
    print(f"价格: {price}")

```bash

## 高级用法

### 通配符订阅

订阅所有事件：

```python
def handler(event_name, data):
    print(f"事件: {event_name}, 数据: {data}")

event_bus.subscribe_all(handler)

```bash

### 过滤器

使用条件过滤事件：

```python
def on_ticker(ticker):
    ticker.init_data()

# 只处理特定交易对
    if ticker.get_symbol_name() == "BTCUSDT":
        print(ticker.get_last_price())

event_bus.subscribe("ticker", on_ticker)

```bash

### 异步处理

在异步环境中处理事件：

```python
import asyncio

async def on_ticker(ticker):
    ticker.init_data()
    await process_price(ticker.get_last_price())

# 注册异步处理器

event_bus.subscribe("ticker", on_ticker)

```bash

### 多个处理器

一个事件可以有多个处理器：

```python
def handler1(ticker):
    print(f"Handler 1: {ticker.get_last_price()}")

def handler2(ticker):
    print(f"Handler 2: {ticker.get_last_price()}")

event_bus.subscribe("ticker", handler1)
event_bus.subscribe("ticker", handler2)

```bash

## 完整示例

### 简单策略框架

```python
from bt_api_py import BtApi

class SimpleStrategy:
    def __init__(self, api):
        self.api = api
        self.event_bus = api.get_event_bus()
        self.setup_handlers()

    def setup_handlers(self):
        """设置事件处理器"""
        self.event_bus.subscribe("ticker", self.on_ticker)
        self.event_bus.subscribe("order", self.on_order)
        self.event_bus.subscribe("error", self.on_error)

    def on_ticker(self, ticker):
        """处理行情更新"""
        ticker.init_data()
        price = ticker.get_last_price()
        print(f"价格更新: {price}")

# 根据价格自动交易
        if price > 50000:
            self.sell()

    def on_order(self, order):
        """处理订单更新"""
        order.init_data()
        status = order.get_order_status()
        print(f"订单状态: {status}")

    def on_error(self, error):
        """处理错误"""
        print(f"发生错误: {error}")

    def buy(self):
        """买入"""
        self.api.make_order("BINANCE___SPOT", "BTCUSDT", 0.001, 50000, "limit")

    def sell(self):
        """卖出"""
        self.api.make_order("BINANCE___SPOT", "BTCUSDT", 0.001, 50000, "limit")

# 使用

api = BtApi(exchange_kwargs={...})
strategy = SimpleStrategy(api)

api.subscribe_ticker("BINANCE___SPOT", "BTCUSDT")
api.run()

```bash

## 事件优先级

EventBus 支持事件优先级，高优先级的事件先处理：

```python

# 高优先级事件

event_bus.publish("order", order_data, priority=1)

# 低优先级事件

event_bus.publish("ticker", ticker_data, priority=10)

```bash

## 线程安全

EventBus 是线程安全的，可以在多线程环境中使用：

```python
import threading

def publisher():
    while True:
        event_bus.publish("ticker", get_ticker())

def subscriber():
    event_bus.subscribe("ticker", handler)

# 启动发布线程

threading.Thread(target=publisher).start()

# 主线程订阅

subscriber()

```bash

## 注意事项

1. **避免阻塞**- 处理器中避免耗时操作，可以使用队列异步处理

2.**异常处理**- 处理器中应该捕获异常，避免影响其他处理器
3.**取消订阅**- 不需要时记得取消订阅，避免内存泄漏
4.**数据初始化** - 使用数据前务必调用 `init_data()`
