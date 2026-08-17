# BMAD 质量迭代计划 - ZmqForwardingRuntime thread 启动失败清理

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮基于 `start_sync()` 初始化失败回滚继续检查更晚阶段的失败路径。

`ZmqForwardingRuntime.start_sync()` 会创建两个 forwarder thread 并依次调用 `start()`。如果第一个线程已经启动，而第二个线程启动失败，`_cleanup_sync_resources()` 会遍历 `_threads` 并对每个线程执行 `join()`。Python 对未启动线程调用 `join()` 会抛出 `RuntimeError`，从而可能遮蔽原始线程启动错误，并中断资源清理。

## 问题

- cleanup 对未启动线程调用 `join()`。
- 线程启动失败路径可能抛出二次异常，掩盖原始启动失败。
- 二次异常可能中断后续 command server、publisher 和 adapter 清理。

## 方案

- 在 `_cleanup_sync_resources()` 中只 join 已启动线程。
- 通过 `thread.ident is not None` 判断线程是否已经启动过。
- 增加测试模拟第二个 forwarder thread 启动失败，验证:
  - 原始启动异常继续抛出。
  - 已启动线程被 join。
  - 未启动线程不会 join。
  - command server/publisher 清理且 adapter 断开。

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
  - `_cleanup_sync_resources()` 只对 `thread.ident is not None` 的已启动线程调用 `join()`。
  - 避免未启动线程 `join()` 抛出 `RuntimeError` 并遮蔽原始启动失败。
- 更新 `tests/test_forwarding_zmq_transport.py`
  - 增加 `test_zmq_forwarding_runtime_start_sync_cleans_up_after_thread_start_failure()`。
  - monkeypatch `threading.Thread` 模拟第二个 forwarder thread 启动失败。
  - 验证已启动线程被 join、未启动线程不 join、adapter 断开、runtime 内部资源清空。

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
- pytest: 497 passed in 15.18s

## 后续候选

- 增加显式 `is_running` property，减少调用方读取内部状态。
- 为 forwarder thread 运行时异常增加可观测日志。
