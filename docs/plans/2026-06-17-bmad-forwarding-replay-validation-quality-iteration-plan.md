# BMAD 质量迭代计划 - Forwarding Replay 参数校验

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮沿着上一轮 timeout 参数校验，继续检查 forwarding 数值配置是否存在静默修正或隐式忽略。

`InMemoryForwardingBus(replay_size=...)` 控制回放缓存大小，`subscribe_market(..., replay=...)` / `subscribe_private(..., replay=...)` 控制新订阅者回放条数，`ForwardingClient(replay=...)` 是策略侧公开入口。此前负数 `replay_size` 会被静默压成 `0`，负数 `replay` 会被当成不回放，这会隐藏错误配置。

## 问题

- `replay_size=-1` 被静默转换为 `0`。
- 订阅时 `replay=-1` 不报错，只是不执行 replay。
- `ForwardingClient(replay=-1)` 构造期不报错，直到订阅行为才间接受影响。

## 方案

- 在 `InMemoryForwardingBus.__init__()` 中显式拒绝负数 `replay_size`。
- 在 `subscribe_market()` / `subscribe_private()` 中显式拒绝负数 `replay`。
- 在 `ForwardingClient.__init__()` 中显式拒绝负数 `replay`。
- 增加测试锁定这些边界。

## 已完成变更

- 更新 `bt_api_py/forwarding/memory.py`
  - 新增 `_normalize_non_negative_int()`。
  - `replay_size` 和订阅 `replay` 改为显式非负校验。
- 更新 `bt_api_py/forwarding/client.py`
  - `ForwardingClient.replay` 构造期改为显式非负校验。
- 更新 `tests/test_forwarding_bus_router_client.py`
  - 增加 `test_in_memory_bus_rejects_negative_replay_size()`。
  - 增加 `test_in_memory_bus_rejects_negative_subscription_replay()`。
  - 增加 `test_forwarding_client_rejects_negative_replay()`。

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
- pytest: 480 passed in 14.55s

## 后续候选

- 继续检查 `SQLiteStateStore.list_private_events(limit=...)` 之外的其它 limit/count 参数。
- 为 README 补充 replay 参数说明，尤其是 `replay=0` 和非负约束。
- 检查 forwarding topic 和 subscription prefix 是否需要显式空字符串策略。
