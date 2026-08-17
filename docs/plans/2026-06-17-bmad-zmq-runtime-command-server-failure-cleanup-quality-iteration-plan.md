# BMAD 质量迭代计划 - ZmqForwardingRuntime command server 失败回滚覆盖

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮基于上一轮 `start_sync()` 初始化失败回滚继续补齐测试覆盖。

上一轮已经模拟 private publisher 初始化失败，验证 `start_sync()` 会清理已创建资源并断开 adapter。但 command server 初始化失败发生得更晚，此时 market publisher 和 private publisher 都已经创建，需要确保同一回滚逻辑也覆盖这个路径。

## 问题

- command server 创建失败时的回滚路径尚未被测试覆盖。
- 该路径需要关闭两个 publisher，并断开 adapter。
- 如果后续 cleanup 逻辑回归，仅 private publisher 失败测试可能无法覆盖 command server 之后的资源状态。

## 方案

- 在 `tests/test_forwarding_zmq_transport.py` 增加 command server 初始化失败测试。
- monkeypatch `ZmqCommandServer` 构造函数抛错。
- 验证:
  - 原异常继续抛出。
  - market/private publisher 均被关闭。
  - adapter 被断开。
  - runtime 内部资源字段清空。

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

- 更新 `tests/test_forwarding_zmq_transport.py`
  - 增加 `test_zmq_forwarding_runtime_start_sync_cleans_up_after_command_server_failure()`。
  - monkeypatch `ZmqCommandServer` 构造失败。
  - 验证 market/private publisher 均关闭、adapter 已断开、runtime 内部状态清空。

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
- pytest: 496 passed in 32.04s

## 后续候选

- 增加显式 `is_running` property，减少调用方读取内部状态。
- 为 forwarder thread 启动失败增加回滚覆盖。
