# BMAD 质量迭代计划 - ForwardingClient stats 刷新语义

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮基于上一轮新增 `ForwardingClient.stats()` 后的语义检查继续推进。

`ForwardingClient.stats()` 用于诊断实时事件积压和本地缓存覆盖。但当前实现只统计已经从订阅队列 drain 到客户端本地缓存的事件。如果上游已经发布事件，而策略尚未调用 `poll_tick()`、`poll_orderbook()`、`poll_bar()` 或 `_drain_private()`，`stats()` 会显示 pending/dropped 都是 0，这会误导慢消费者诊断。

## 问题

- `stats()` 不会自动刷新市场订阅队列。
- `stats()` 不会自动刷新私有事件订阅队列。
- 使用方必须先调用 poll 或内部 `_drain_private()` 才能得到真实缓存压力，公开 API 语义不完整。
- 仍需要支持“只看当前本地缓存、不触碰订阅队列”的低副作用诊断模式。

## 方案

- 将 `ForwardingClient.stats()` 改为 `stats(*, refresh: bool = True)`。
- `refresh=True` 时:
  - drain 当前已订阅的所有市场 symbol。
  - drain 当前私有事件订阅。
  - 再统计 pending/dropped。
- `refresh=False` 时保留旧行为，只统计当前本地缓存。
- ZMQ 客户端通过现有多态 `_drain_market()` / `_drain_private()` 继承相同语义。
- 更新测试和 README，明确默认刷新和 `refresh=False`。

## 验收口径

执行并通过:

```bash
ruff format --check bt_api_py tests
ruff check bt_api_py tests
mypy bt_api_py tests
bandit -q -r bt_api_py -c pyproject.toml
pytest -q
```

## 已完成变更

- 更新 `bt_api_py/forwarding/client.py`
  - `ForwardingClient.stats()` 改为 `stats(*, refresh: bool = True)`。
  - 默认刷新当前已订阅市场 symbol 和私有事件订阅后再统计 pending/dropped。
  - `stats(refresh=False)` 保留本地缓存快照模式。
  - ZMQ 客户端通过现有 `_drain_market()` / `_drain_private()` 覆盖继承同一刷新语义。
- 更新 `tests/test_forwarding_bus_router_client.py`
  - 扩展 `test_forwarding_client_stats_reports_event_cache_pressure()`，验证 `refresh=False` 不拉取订阅队列，默认 `stats()` 会刷新并报告 drop。
- 更新 `README.md`
  - 明确 `stats()` 默认刷新当前订阅。
  - 补充 `stats(refresh=False)` 的低副作用快照用途。

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
- pytest: 490 passed in 21.15s

## 后续候选

- 为 ZMQ 客户端增加 `stats()` 刷新语义的端到端测试。
- 评估是否要在 stats 中增加 `last_refreshed_at` 或 transport poll 错误计数。
