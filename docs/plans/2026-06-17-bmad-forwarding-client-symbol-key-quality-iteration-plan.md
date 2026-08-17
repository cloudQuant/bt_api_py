# BMAD 质量迭代计划 - ForwardingClient Symbol Key 归一化

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮延续上一轮 MarketDataHub topic 归一化，继续检查客户端行情订阅和缓存 key 是否与 topic 规则一致。

forwarding 的 market topic 会把 symbol 中的 `/` 替换为 `-`，例如 `BTC/USDT` 的 topic 片段是 `BTC-USDT`。此前 `ForwardingClient` 和 `ZmqForwardingClient` 的 `_market_subscriptions`、`_ticks`、`_orderbooks`、`_bars` 等内部 key 仍使用调用方传入的原始 symbol。

## 问题

- `client.subscribe("BTC/USDT")` 和 `client.subscribe("BTC-USDT")` 会被当成两个不同订阅 key。
- `MarketEvent(symbol="BTC/USDT")` 被缓存到 `BTC/USDT`，但 `poll_tick("BTC-USDT")` 查不到。
- `supports_live_ticks("BTC-USDT")` 和 `supports_live_ticks("BTC/USDT")` 结果可能不一致。
- ZMQ client path 有同样的订阅 key 差异。

## 方案

- 增加客户端内部 `_normalize_symbol_key()`。
- 对以下行情路径统一使用归一化 symbol key:
  - `subscribe()`
  - `poll_tick()`
  - `poll_orderbook()`
  - `poll_bar()`
  - `has_pending_tick()`
  - `has_pending_orderbook()`
  - `supports_live_ticks()`
  - `supports_live_orderbook()`
  - `_drain_market()`
  - `_cache_market_event()`
- 只处理行情订阅和缓存 key，不改订单命令 payload 里的原始 symbol。

## 已完成变更

- 更新 `bt_api_py/forwarding/client.py`
  - `ForwardingClient` 和 `ZmqForwardingClient` 的行情订阅 key 与缓存 key 统一归一化。
  - 新增 `_normalize_symbol_key()`。
- 更新 `tests/test_forwarding_bus_router_client.py`
  - 增加 `test_forwarding_client_normalizes_market_symbol_keys_like_topics()`。

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
- pytest: 483 passed in 15.56s

## 后续候选

- 将 market symbol 归一化抽到 schema 层，避免 `schema.market_topic`、`MarketDataHub` 和 `ForwardingClient` 各自维护规则。
- 检查空 symbol 是否应在行情 API 层显式拒绝。
- 为 ZMQ client 增加 slash/dash symbol 的端到端 smoke test。
