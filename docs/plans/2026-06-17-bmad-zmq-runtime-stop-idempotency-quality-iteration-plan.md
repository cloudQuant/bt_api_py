# BMAD 质量迭代计划 - ZmqForwardingRuntime stop_sync 幂等性

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮基于上一轮 `start_sync()` 幂等保护继续检查 runtime lifecycle。

`ZmqForwardingRuntime.stop_sync()` 当前会无条件调用 `asyncio.run(self.stop())`。如果 runtime 从未启动，或已经完成停止，再次调用 `stop_sync()` 会继续调用 broker adapter 的 `disconnect()`。Mock adapter 能容忍这种行为，但真实 adapter 的 disconnect-before-connect 或重复 disconnect 不一定是安全 no-op。

## 问题

- 未启动的 runtime 调用 `stop_sync()` 仍会触发 adapter disconnect。
- 已停止的 runtime 再次调用 `stop_sync()` 仍会重复触发 adapter disconnect。
- lifecycle API 与 `start_sync()` 的幂等语义不对称。

## 方案

- 在 `stop_sync()` 开头判断是否没有 command server、publisher 和 forwarder threads。
- 如果没有运行资源，直接返回，不调用 `self.stop()`。
- 保留部分启动失败场景的清理能力：只要存在 publisher/server/thread 任一资源，就继续执行清理路径。
- 增加测试覆盖未启动 stop no-op，以及启动-停止后再次 stop no-op。

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

- 更新 `bt_api_py/forwarding/service.py`
  - `ZmqForwardingRuntime.stop_sync()` 在没有 command server、publisher 和 forwarder threads 时直接返回。
  - 未启动或已完全停止的 runtime 不再重复调用 `order_router.disconnect()`。
  - 保留部分启动失败场景下的资源清理能力。
- 更新 `tests/test_forwarding_zmq_transport.py`
  - 增加 `CountingMockBrokerAdapter`。
  - 增加 `test_zmq_forwarding_runtime_stop_sync_is_idempotent()`。
  - 验证未启动 stop 不触发 disconnect，启动-停止后重复 stop 只触发一次 disconnect。

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
- pytest: 494 passed in 23.20s

## 后续候选

- 检查 `start_sync()` 中途失败时是否需要更细粒度的资源回滚。
- 为 runtime lifecycle 增加显式 `is_running` property。
