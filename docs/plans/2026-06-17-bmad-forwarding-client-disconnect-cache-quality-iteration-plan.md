# BMAD 质量迭代计划 - ForwardingClient 断开连接清理事件缓存

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮检查 `ForwardingClient.disconnect()` 后的缓存状态。

`ForwardingClient` 会缓存实时 tick、orderbook、bar 和 broker update，供策略同步 poll。此前 `disconnect()` 只关闭订阅并清空订阅列表，但没有清空这些实时事件队列。重连后，策略可能读到断开前的旧行情或旧订单事件。

## 问题

- `disconnect()` 后 `_ticks`、`_orderbooks`、`_bars` 仍保留旧事件。
- `disconnect()` 后 `_broker_updates` 仍保留旧私有事件。
- 查询缓存 `_account_cache`、`_positions_cache`、`_orders_cache` 需要保留用于查询降级，不能一并清空。

## 方案

- 新增 `_clear_event_caches()`，只清理实时事件队列:
  - `_ticks`
  - `_orderbooks`
  - `_bars`
  - `_broker_updates`
- embedded `ForwardingClient.disconnect()` 和 `ZmqForwardingClient.disconnect()` 均调用该 helper。
- 保留账户、持仓、订单查询缓存。
- 增加测试覆盖断开后旧事件不可再 poll，但查询快照仍可返回。

## 已完成变更

- 更新 `bt_api_py/forwarding/client.py`
  - 新增 `_clear_event_caches()`。
  - embedded 和 ZMQ disconnect path 均清理事件缓存。
- 更新 `tests/test_forwarding_bus_router_client.py`
  - 增加 `test_forwarding_client_disconnect_clears_event_caches_but_keeps_query_snapshots()`。

## 验收结果

已执行并通过:

```bash
ruff format --check bt_api_py tests
ruff check bt_api_py tests
mypy bt_api_py tests
bandit -q -r bt_api_py -c pyproject.toml
pytest -q
```

结果:

- ruff format: 109 files already formatted
- ruff check: All checks passed
- mypy: Success, no issues found in 109 source files
- bandit: exit 0
- pytest: 487 passed in 15.40s

## 后续候选

- 为 `ZmqForwardingClient` 增加同类断开缓存清理的直接测试。
- 检查实时事件缓存是否需要最大长度，避免慢消费者无界增长。
- 检查 reconnect 后是否需要显式重放请求或 gap 检测。
