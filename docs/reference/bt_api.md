# BtApi

`BtApi` 是 bt_api_py 框架的核心入口类，统一管理所有交易所的连接、行情查询、交易操作和账户管理。

## 使用示例

```python
from bt_api_py import BtApi

api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {"api_key": "...", "secret": "..."},
    "OKX___SWAP":     {"api_key": "...", "secret": "...", "passphrase": "..."},
})

# 查行情
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()
print(ticker.get_last_price())

# 下单
order = api.make_order("BINANCE___SPOT", "BTCUSDT", 0.001, 50000, "limit")
```

---

::: bt_api_py.bt_api.BtApi
    options:
      show_root_heading: true
      show_source: false
      members_order: source
      heading_level: 2
      show_if_no_docstring: false
      filters:
        - "!^_"
        - "!^__"
        - "!^log$"
        - "!^init_logger$"
        - "!^push_bar_data_to_queue$"
