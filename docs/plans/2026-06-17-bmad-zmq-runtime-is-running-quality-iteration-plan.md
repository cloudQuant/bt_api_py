# BMAD 质量迭代计划 - ZmqForwardingRuntime is_running 状态属性

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮基于 ZMQ runtime 生命周期改进继续收敛状态判断。

当前 `ZmqForwardingRuntime` 使用 `_command_server is not None` 作为运行状态判断，`start_sync()`、`health()` 和调用方测试都隐含依赖这个私有字段。随着 lifecycle 逻辑增加，显式状态属性可以减少内部字段泄漏，并统一运行状态语义。

## 问题

- 运行状态判断分散在私有字段 `_command_server` 上。
- `health()` 直接读取私有字段表达 running。
- 测试或外部调用方如果需要状态，只能间接读取 health 或内部字段。

## 方案

- 为 `ZmqForwardingRuntime` 增加 `is_running` property。
- `start_sync()` 幂等判断使用 `is_running`。
- `health()["zmq"]["running"]` 使用 `is_running`。
- 增加测试覆盖 fresh/start/stop 三个状态。

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
  - 新增 `ZmqForwardingRuntime.is_running` property。
  - `start_sync()` 幂等判断改用 `is_running`。
  - `health()["zmq"]["running"]` 改用 `is_running`。
- 更新 `tests/test_forwarding_zmq_transport.py`
  - 增加 `test_zmq_forwarding_runtime_is_running_tracks_lifecycle()`。
  - 覆盖 fresh/start/stop 三个状态。

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
- pytest: 498 passed in 14.72s

## 后续候选

- 为 forwarder thread 运行时异常增加可观测日志。
- 为 runtime lifecycle 增加更细粒度状态枚举。
