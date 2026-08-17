# BMAD 质量迭代计划 - Forwarding 同步命令超时边界

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮从 forwarding 的同步命令调用路径继续扫描运行时边界。

`ForwardingClient` 是策略框架侧的同步 facade，内部通过 `InMemoryForwardingBus.send_command_sync()` 把同步调用桥接到 async command handler。此前该桥接逻辑在已有事件循环中会启动后台线程并无超时等待。如果 handler 卡住，策略侧同步调用会一直阻塞。

## 问题

- `InMemoryForwardingBus.send_command_sync()` 没有 timeout 参数。
- `_run_awaitable_sync()` 在线程桥接路径中直接 `thread.join()`，没有退出边界。
- `ForwardingClient` 使用 in-memory bus 时没有类似 ZMQ client 的命令超时保护。

## 方案

- 为 `InMemoryForwardingBus.send_command_sync()` 增加可选 `timeout` 参数。
- 在 `_run_awaitable_sync()` 中通过 `asyncio.wait_for()` 执行 awaitable。
- 在线程 join 路径中根据 timeout 设置最大等待时间，极端情况下也能向调用方返回 `TimeoutError`。
- 为 `ForwardingClient` 增加 `command_timeout` 参数，默认 `2.0` 秒，并传递给 in-memory bus。
- 直接使用 bus 的调用方如果需要保持无限等待，可以传 `timeout=None`。

## 已完成变更

- 更新 `bt_api_py/forwarding/memory.py`
  - `send_command_sync(command, *, timeout=None)` 支持同步命令超时。
  - 新增 `_await_with_timeout()` 和 `_timeout_error()`。
  - 线程桥接路径增加 join timeout。
- 更新 `bt_api_py/forwarding/client.py`
  - `ForwardingClient(..., command_timeout=2.0)`。
  - `_send_command_sync()` 将超时配置传给 bus。
- 更新 `tests/test_forwarding_bus_router_client.py`
  - 增加事件循环内同步命令超时测试。
  - 增加 `ForwardingClient` 传递配置超时的测试。

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
- pytest: 470 passed in 14.78s

## 后续候选

- 统一 `ForwardingClient.command_timeout` 和 `ZmqForwardingClient.command_timeout_ms` 的命名和配置入口。
- 为命令超时增加 metrics/logging，便于生产排查慢 broker 或卡住的策略请求。
- 为 direct in-memory bus 的同步调用增加文档示例，说明 `timeout=None` 和有限 timeout 的差异。
