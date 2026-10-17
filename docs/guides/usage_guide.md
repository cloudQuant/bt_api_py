# 使用 BtApi

## 选择运行入口

默认 direct mode 适用于本进程已加载 Feed 的既有调用。共享行情与账户读取使用已配置的 service-v2 读服务；受管实盘订单通过 Backtrader 的 `ServiceExecutionSession`、`ServiceHost` 和 `service.order_entry` 执行。旧 forwarding service 已退役，服务故障不会自动切回 direct。入口边界见 [Core contract](../reference/core-api.md) 和[运行时架构](../explanation/architecture.md)。

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

## 缓存与请求结果

service-v2 读取支持 `Consistency.LIVE` 与 `Consistency.CACHE_OK`。只有调用方允许有界的旧数据，并处理 `StaleDataUnavailableError` 时，才使用 `CACHE_OK`；具体读取边界见 [Core contract](../reference/core-api.md)。

旧 forwarding 的 `CommandAck` / `get_command_status` 路径已退役。受管订单的预留、派发日志与恢复由 service-v2 session 和 Host 持有。请求超时后保留原业务标识，使用对应服务的查询与恢复入口核对事实；结果尚未确认时，不创建新的幂等键重复下单。
