# Core API contract

## Requests

Use typed requests for direct provider operations and for constructing intents
that a managed live session will submit:

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

`BtApi.make_order`, `cancel_order`, `cancel_all`, and `query_order` accept these
requests on the direct provider path. Managed live orders use Backtrader's
`ServiceExecutionSession`, which submits through service-v2 `ServiceHost` and
`service.order_entry`.

## Read consistency

```python
from bt_api_py import Consistency

snapshot = api.get_tick("SIM___SPOT", "BTC-USDT", consistency=Consistency.LIVE)
cached = api.get_tick("SIM___SPOT", "BTC-USDT", consistency=Consistency.CACHE_OK)
```

The direct Feed owns the result shape. Remote market and account reads belong to
the configured service-v2 read services; `BtApi` no longer opens a forwarding
transport or presents forwarding consistency modes.

## Commands

The former forwarding `CommandAck` and `get_command_status` path is retired.
For managed live orders, the service session and Host own reservation,
dispatch journaling, and uncertain-outcome reconciliation. Callers must not
retry an uncertain live intent as a new order.
