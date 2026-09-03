# Core API contract

## Requests

Use typed requests for cross-transport trading operations:

```python
from decimal import Decimal

from bt_api_py import CancelOrderRequest, OrderRequest, OrderType, QueryOrderRequest, Side

order = OrderRequest(
    symbol="BTC-USDT",
    side=Side.BUY,
    order_type=OrderType.LIMIT,
    quantity=Decimal("1"),
    price=Decimal("100"),
    account_id="paper",
    client_order_id="client-1",
    time_in_force="GTC",
    reduce_only=False,
    idempotency_key="example-1",
)
cancel = CancelOrderRequest(symbol="BTC-USDT", account_id="paper", order_id="order-1")
query = QueryOrderRequest(symbol="BTC-USDT", account_id="paper", order_id="order-1")
```

`BtApi.make_order`, `cancel_order`, `cancel_all`, and `query_order` accept these requests. String positional forms remain for direct legacy compatibility; they are converted to typed requests in ZMQ mode.

## Read consistency

```python
from bt_api_py import Consistency

snapshot = api.get_tick("SIM___SPOT", "BTC-USDT", consistency=Consistency.LIVE)
cached = api.get_tick("SIM___SPOT", "BTC-USDT", consistency=Consistency.CACHE_OK)
```

In direct mode the Feed owns the result shape. In ZMQ mode these methods return contract snapshots with `Freshness`:

- `LIVE`: waits for a post-call market event; timeout is `LiveQueryFailedError`.
- `CACHE_OK`: accepts only a bounded cache entry and marks it stale; missing/expired cache is `StaleDataUnavailableError`.

## Commands

ZMQ commands return a forwarding `CommandAck`. If transport timeout leaves completion uncertain, catch `CommandResultUnknownError` and reconcile:

```python
from bt_api_py import CommandResultUnknownError

try:
    api.make_order("SIM___SPOT", order)
except CommandResultUnknownError as error:
    status = api.get_command_status("SIM___SPOT", error.command_id)
    print(status.status)
```

Do not blindly resend an order after timeout. Reusing an idempotency key with different intent is a protocol-correlation failure.
