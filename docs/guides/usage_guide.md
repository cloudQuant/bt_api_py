# 使用 BtApi

## 选择 transport

默认 direct mode 适用于本进程已加载 Feed 的既有调用。ZMQ mode 适用于已部署 forwarding service 的策略进程；它不会尝试连接本地 Feed 作为失败回退。

```python
from bt_api_py import BtApi

api = BtApi(exchange_kwargs={"BINANCE___SPOT": {"testnet": True}})
tick = api.get_tick("BINANCE___SPOT", "BTCUSDT")
```

调用前不要把该示例理解成交易所连接或订单授权证明：凭证、网络和 venue plugin 都是部署侧依赖。

## 下单与撤单

```python
from decimal import Decimal

from bt_api_py import CancelOrderRequest, OrderRequest, OrderType, Side

request = OrderRequest(
    symbol="BTCUSDT",
    side=Side.BUY,
    order_type=OrderType.LIMIT,
    quantity=Decimal("0.001"),
    price=Decimal("50000"),
    account_id="paper",
    client_order_id="strategy-a-1",
    idempotency_key="strategy-a-1",
)
ack_or_result = api.make_order("BINANCE___SPOT", request)

cancel = CancelOrderRequest(
    symbol="BTCUSDT", account_id="paper", order_id="venue-order-id"
)
api.cancel_order("BINANCE___SPOT", cancel)
```

The compatibility form `make_order(exchange, symbol, volume, price, "buy-limit")` remains available for old direct integrations. It is not a substitute for an explicit `Side` in new code.

## ZMQ cache and failures

In forwarding mode, request `Consistency.LIVE` only when a publisher is expected to emit a new scoped event. Use `Consistency.CACHE_OK` only when the caller accepts a bounded stale result and handles `StaleDataUnavailableError`.

For command timeouts, catch `CommandResultUnknownError` and call `get_command_status`; do not create a fresh idempotency key and blindly retry.
