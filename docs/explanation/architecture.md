# 运行时架构

```text
application
    |
  BtApi
    |
  OperationBackend
    |---------------------------|
DirectBackend                 ZmqBtApiBackend
    |                           |
installed Feed / mapper       ForwardingClient -> router / market events
```

## 单一公共门面

`BtApi` 是唯一的公共入口。同步 v1 操作会经过 `OperationBackend`：

- direct：`DirectBackend` 调用已注册 Feed；legacy 调用保留其原生返回值和 `extra_data`/Feed 参数；
- zmq：`ZmqBtApiBackend` 只使用 forwarding client，不会回落到本地 Feed。

这意味着 ZMQ 未实现的操作必须返回 `CapabilityNotSupportedError`，而不是因本地没有 Feed 而变成 `ExchangeNotFoundError`。

## 市场与私有读取

Forwarding market events 带 exchange、market type、symbol、sequence 和时间。客户端保留两类缓存：

- `poll_*` 队列供策略逐条消费；
- `peek`/latest 事件供快照读取，不消费前者。

`LIVE` 基于调用时的 sequence 只接收后续事件，并受 `ForwardingConfig.market_read_timeout_ms` 限制。`CACHE_OK` 仅接受未超过 `max_cache_age_ms` 的事件，返回的 `Freshness` 标为 `source="cache"`、`stale=true` 并带原因。

私有 account、position、order、fill 同样受 account/strategy scope 约束。跨 scope 的事件不会被当作当前账户缓存使用。

## 命令与对账

`OrderRequest` 的 account、client order id、idempotency key、time in force、reduce only 和价格会序列化到 `OrderCommand`。router 对请求意图计算 fingerprint：

- 相同 idempotency key + 相同 fingerprint：返回同一关联结果；
- 相同 key + 不同 fingerprint 或账户/策略 scope：`ProtocolCorrelationError`；
- transport timeout：`CommandResultUnknownError`，调用方用 `get_command_status(command_id)` 对账，不盲重试。

router 的 receipt 是有 TTL 的运行时记录，不是长期审计或发布认证系统。
