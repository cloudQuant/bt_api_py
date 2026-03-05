# 事件总线

`EventBus` 提供轻量级的发布/订阅事件分发机制，是 bt_api_py 事件驱动架构的核心组件。

## 事件类型

| 事件类型 | 描述 | 典型数据 |
|---------|------|---------|
| `BarEvent` | K 线数据更新 | `Bar` 容器 |
| `TickEvent` | 行情 Tick 更新 | `Ticker` 容器 |
| `OrderEvent` | 订单状态变化 | `Order` 容器 |
| `TradeEvent` | 成交通知 | `Trade` 容器 |
| `PositionEvent` | 持仓变化 | `Position` 容器 |
| `AccountEvent` | 账户资金变化 | `Account` 容器 |

## 使用示例

```python
from bt_api_py import BtApi, EventBus

# 创建自定义事件总线
event_bus = EventBus()

# 注册事件处理函数
def on_bar(bar):
    bar.init_data()
    print(f"新 K 线: {bar.get_close_price()}")

def on_order(order):
    order.init_data()
    print(f"订单状态: {order.get_status()}")

event_bus.on("BarEvent", on_bar)
event_bus.on("OrderEvent", on_order)

# 将事件总线传入 BtApi
api = BtApi(
    exchange_kwargs={"BINANCE___SPOT": {"api_key": "...", "secret": "..."}},
    event_bus=event_bus,
)

# 手动触发事件（用于测试）
event_bus.emit("BarEvent", bar_data)

# 取消订阅
event_bus.off("BarEvent", on_bar)
```

---

::: bt_api_py.event_bus.EventBus
    options:
      show_root_heading: true
      show_source: false
      members_order: source
      heading_level: 2
      show_if_no_docstring: false
      filters:
        - "!^_"
        - "!^__"
