# BMAD 质量迭代计划 - ZmqForwardingRuntime start_sync 失败回滚

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮基于 `ZmqForwardingRuntime.start_sync()` / `stop_sync()` 幂等性继续检查启动失败路径。

`start_sync()` 会先连接 adapter，然后依次创建 market publisher、private publisher、command server 和 forwarder threads。如果 adapter 已连接后某个 ZMQ 资源创建失败，当前实现会直接抛错，已创建的 publisher 不会关闭，adapter 也不会断开。

## 问题

- market publisher 创建成功后，private publisher 创建失败会泄漏 market publisher。
- command server 创建失败也可能泄漏 publisher。
- adapter 已连接后，如果 ZMQ 资源初始化失败，adapter 仍保持连接。
- 失败后的 runtime 内部字段可能保留半初始化状态。

## 方案

- 提取 `_cleanup_sync_resources(disconnect_adapter: bool)`，统一关闭 threads、command server、publisher，并可选断开 adapter。
- `stop_sync()` 复用该 helper。
- `start_sync()` 在 adapter connect 成功后的 ZMQ 初始化阶段包裹 `try/except`。
- 初始化失败时调用 `_cleanup_sync_resources(disconnect_adapter=True)` 并重新抛出原异常。
- 增加测试模拟 private publisher 初始化失败，验证:
  - 原异常继续抛出。
  - 已创建的 market publisher 被关闭。
  - adapter 被断开。
  - runtime 内部 publisher/server/thread 状态清空。

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
  - 提取 `_cleanup_sync_resources(disconnect_adapter: bool)`。
  - `stop_sync()` 复用统一资源清理逻辑。
  - `start_sync()` 在 adapter connect 后的 ZMQ 初始化阶段捕获异常，清理已创建资源并断开 adapter，然后重新抛出原异常。
- 更新 `tests/test_forwarding_zmq_transport.py`
  - `CountingMockBrokerAdapter` 增加 connect/disconnect 计数。
  - 增加 `test_zmq_forwarding_runtime_start_sync_cleans_up_after_publisher_failure()`。
  - 模拟 private publisher 初始化失败，验证 market publisher 被关闭、adapter 被断开、runtime 内部状态清空。

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
- pytest: 495 passed in 19.81s

## 后续候选

- 为 command server 初始化失败增加同类回滚测试。
- 增加显式 `is_running` property，减少调用方读取内部状态。
