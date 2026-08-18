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

# 下单（v1 标准形式：OrderRequest）
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
```

> `make_order(exchange_name, OrderRequest(...))` 是唯一标准下单形式。旧位置参数
> `make_order(exchange_name, symbol, volume, price, order_type)` 仅在 `order_type`
> 能推导买卖方向时兼容（如 `"buy-limit"`）；裸 `"limit"`/`"market"` 无法推导
> side，会在调用交易所前抛出 `LegacyOrderApiError`。

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
