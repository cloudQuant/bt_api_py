# BMAD 质量迭代计划 - BtApiForwardingAdapter max_items 边界校验

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮从 forwarding 外部数据入口继续检查数值参数是否存在静默修正。

`BtApiForwardingAdapter.forward_once(exchange_name, max_items=...)` 负责从现有 `BtApi` 数据队列中取数据并转发到 `MarketDataHub`。此前 `max_items` 会通过 `max(int(max_items), 0)` 静默处理，负数会被当作 `0`，从而隐藏调用方配置错误。

## 问题

- `max_items=-1` 不报错，只是不转发任何数据。
- `max_items=0` 和错误配置负数表现相同，调用方难以区分主动 no-op 和配置错误。

## 方案

- 保留 `max_items=0` 作为合法 no-op。
- 对负数 `max_items` 显式抛出 `ValueError("max_items must be non-negative")`。
- 增加测试覆盖 `0` 和负数两个边界。

## 已完成变更

- 更新 `bt_api_py/forwarding/btapi_adapter.py`
  - 新增 `_normalize_non_negative_int()`。
  - `forward_once()` 在读取队列前校验 `max_items`。
- 更新 `tests/test_forwarding_bus_router_client.py`
  - 增加 `test_btapi_forwarding_adapter_max_items_boundaries()`。

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
- pytest: 481 passed in 14.98s

## 后续候选

- 检查 `MarketDataHub.subscribe()` / `unsubscribe()` 是否应拒绝空 symbol 或空 event_type。
- 检查 `BtApiForwardingAdapter.normalize()` 对缺失 symbol 的处理是否需要显式错误或 metrics。
- 继续扫描 forwarding 入口中其它 count/limit 参数的一致性。
