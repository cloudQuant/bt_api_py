# 交易所注册表

`ExchangeRegistry` 是 bt_api_py 实现"即插即用"架构的核心，基于 Registry 设计模式管理所有交易所的 Feed 类、WebSocket 流类和余额处理函数。

## 工作原理

```
新交易所注册 → ExchangeRegistry
                   ├── register_feed()        # 注册 REST Feed 类
                   ├── register_stream()      # 注册 WebSocket 流类
                   └── register_balance_handler()  # 注册余额解析函数

BtApi 使用 → ExchangeRegistry
                   ├── create_feed()          # 自动创建 Feed 实例
                   └── get_stream_class()     # 获取流类
```

## 添加新交易所

在 `bt_api_py/exchange_registers/` 目录下创建新文件即可，无需修改核心代码：

```python
# bt_api_py/exchange_registers/my_exchange.py
from bt_api_py.registry import ExchangeRegistry
from .my_exchange_feed import MyExchangeFeed
from .my_exchange_stream import MyExchangeStream

ExchangeRegistry.register_feed("MYEXCHANGE___SPOT", MyExchangeFeed)
ExchangeRegistry.register_stream("MYEXCHANGE___SPOT", "subscribe", MyExchangeStream)
```

## 查询已注册交易所

```python
from bt_api_py import BtApi

# 列出所有已注册的交易所
exchanges = BtApi.list_available_exchanges()
print(exchanges)
```

---

::: bt_api_py.registry.ExchangeRegistry
    options:
      show_root_heading: true
      show_source: false
      members_order: source
      heading_level: 2
      show_if_no_docstring: false
      filters:
        - "!^_"
        - "!^__"
