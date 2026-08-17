# BMAD 质量迭代计划 - Forwarding Timeout 参数校验

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮从前序 command timeout 行为继续扫描配置输入边界。

`ForwardingClient.command_timeout`、`InMemoryForwardingBus.send_command_sync(timeout=...)` 和 `ZmqCommandClient.send(timeout_ms=...)` 都是调用方可以直接传入的超时配置。此前负数 timeout 没有被显式拒绝，会落入 `asyncio.wait_for()` 或 ZeroMQ poll 的底层语义，容易造成“立即超时”或特殊等待行为，不利于排查配置错误。

## 问题

- embedded sync bridge 没有校验 `timeout` 是否为非负有限数。
- `ForwardingClient(command_timeout=...)` 没有构造期校验。
- `ZmqForwardingClient(command_timeout_ms=...)` 没有构造期校验。
- `ZmqCommandClient.send(timeout_ms=...)` 没有调用期校验。

## 方案

- 在 in-memory sync bridge 层增加 `_normalize_timeout()`，拒绝负数和非有限数。
- 在 `ForwardingClient` 构造期校验 `command_timeout`。
- 在 `ZmqForwardingClient` 构造期校验 `command_timeout_ms`。
- 在 `ZmqCommandClient.send()` 调用期校验 `timeout_ms`，并在发送消息前失败。
- 对无效 timeout 导致提前失败的 awaitable 做关闭处理，避免未 await 的 coroutine 警告。

## 已完成变更

- 更新 `bt_api_py/forwarding/memory.py`
  - `send_command_sync()` 先校验 timeout 再创建 command coroutine。
  - `_await_with_timeout()` 和 `_run_awaitable_sync()` 统一校验 timeout。
  - 无效 timeout 时关闭传入 awaitable。
- 更新 `bt_api_py/forwarding/client.py`
  - `ForwardingClient.command_timeout` 要求为 `None` 或非负有限数。
  - `ZmqForwardingClient.command_timeout_ms` 要求非负。
- 更新 `bt_api_py/forwarding/transport.py`
  - `ZmqCommandClient.send(timeout_ms=...)` 在发送前拒绝负数。
- 更新测试
  - `tests/test_forwarding_bus_router_client.py`
  - `tests/test_forwarding_zmq_transport.py`

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
- pytest: 477 passed in 15.10s

## 后续候选

- 将 timeout validation 规则同步到 README 的参数说明中。
- 检查 replay、limit、replay_size 等其它数值配置是否仍存在“静默归零”或未校验边界。
- 给 ZMQ transport 增加 socket HWM 配置和慢消费者策略说明。
