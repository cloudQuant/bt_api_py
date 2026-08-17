# BMAD 质量迭代计划 - MarketDataHub Symbol 归一化一致性

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮检查 forwarding 的 topic 生成和订阅统计 key 是否一致。

`MarketEvent` 的 topic 生成会把 symbol 中的 `/` 替换为 `-`，例如 `BTC/USDT` 会变为 `BTC-USDT`。但 `MarketDataHub._key()` 此前用于订阅 refcount 的 symbol 没有做同样处理，导致 `subscribe("BTC/USDT")` 和 `unsubscribe("BTC-USDT")` 使用不同 key。

## 问题

- 发布 topic 使用 `BTC-USDT`。
- 订阅 refcount key 可能保留 `BTC/USDT`。
- health/stats 中的 active subscription key 和真实 topic 表达不一致。
- 用 dash 格式退订 slash 格式订阅时，refcount 不会被清理。

## 方案

- `MarketDataHub._key()` 的 symbol 归一化与 topic 生成保持一致，统一执行 `replace("/", "-")`。
- 增加测试覆盖 slash 订阅、dash 退订能正确清理 refcount。

## 已完成变更

- 更新 `bt_api_py/forwarding/hub.py`
  - `_key()` 对 symbol 执行 `/` 到 `-` 的归一化。
- 更新 `tests/test_forwarding_bus_router_client.py`
  - 增加 `test_market_data_hub_normalizes_subscription_symbol_like_topics()`。

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
- pytest: 482 passed in 20.09s

## 后续候选

- 检查 `ForwardingClient.subscribe()` 和 `MarketDataHub.subscribe()` 是否应该共享同一个 topic builder。
- 检查空 symbol 的语义是否应在 public API 层显式拒绝。
- 给 health/stats 增加 topic 示例或订阅 key 的文档说明。
