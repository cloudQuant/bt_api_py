# BMAD 质量迭代计划 - ZmqForwardingRuntime start_sync 幂等性

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮检查 forwarding runtime 生命周期。

`ForwardingClient.connect()` 和 `ZmqForwardingClient.connect()` 都是幂等的；重复调用会直接返回。但 `ZmqForwardingRuntime.start_sync()` 当前没有同类保护，重复调用时会尝试重新创建 publisher、command server 和 forwarder threads，并重复绑定同一组 ZMQ endpoint。

## 问题

- 重复调用 `start_sync()` 可能触发 ZMQ endpoint 重复绑定错误。
- 即使未立即报错，也可能创建重复 forwarder threads，导致运行状态难以诊断。
- 服务生命周期 API 与 client `connect()` 的幂等语义不一致。

## 方案

- 在 `ZmqForwardingRuntime.start_sync()` 开头检查 `_command_server is not None`。
- 如果已经启动，直接返回，不重复创建 publisher/server/thread。
- 增加测试覆盖重复 `start_sync()` 不改变 forwarder thread 数量、health 仍为 running。

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
  - `ZmqForwardingRuntime.start_sync()` 在 `_command_server is not None` 时直接返回。
  - 避免重复绑定 ZMQ endpoint、重复创建 publisher/server/thread。
- 更新 `tests/test_forwarding_zmq_transport.py`
  - 增加 `test_zmq_forwarding_runtime_start_sync_is_idempotent()`。
  - 验证重复 `start_sync()` 后 runtime 仍 running，forwarder thread 数量不增加。

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
- pytest: 493 passed in 26.84s

## 后续候选

- 检查 `stop_sync()` 未启动场景是否需要显式 no-op。
- 检查 `start_sync()` 中途失败时是否需要回滚已创建资源。
