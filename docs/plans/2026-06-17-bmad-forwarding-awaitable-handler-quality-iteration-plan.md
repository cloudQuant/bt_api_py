# 2026-06-17 BMad Forwarding Awaitable Handler Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

继续扫描 forwarding 命令处理路径时发现：

1. `CommandHandler` 类型别名声明允许返回 `CommandAck | Awaitable[CommandAck]`。
2. `InMemoryForwardingBus.send_command()` 原先只用 `asyncio.iscoroutine(result)` 判断是否需要 await。
3. `ZmqCommandServer` 内部 `_run_handler()` 也只支持 coroutine。
4. 如果 handler 返回 `asyncio.Future` 或自定义 awaitable，会被当作 `CommandAck` 原样返回，运行时行为与类型契约不一致。

这会影响策略命令转发的 handler 扩展能力。

## 本轮实施

1. `bt_api_py/forwarding/memory.py`
   - 引入 `inspect`。
   - 将 `asyncio.iscoroutine(result)` 改为 `inspect.isawaitable(result)`。
   - 支持 `Future`、coroutine 和自定义 awaitable。

2. `bt_api_py/forwarding/transport.py`
   - 引入 `inspect`。
   - `_run_handler()` 改为识别 generic awaitable。
   - 新增 `_await_handler_result()` 小 wrapper，用 `asyncio.run()` 运行通用 awaitable。

3. `tests/test_forwarding_bus_router_client.py`
   - 新增 `test_in_memory_bus_awaits_future_command_handler()`。
   - 覆盖 handler 返回 `asyncio.Future[CommandAck]` 的场景。

4. `tests/test_forwarding_zmq_transport.py`
   - 新增 `test_zmq_command_handler_accepts_generic_awaitable_ack()`。
   - 覆盖 handler 返回自定义 awaitable 的场景。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_forwarding_bus_router_client.py tests/test_forwarding_zmq_transport.py
# 17 passed in 2.19s

ruff check bt_api_py/forwarding/memory.py bt_api_py/forwarding/transport.py tests/test_forwarding_bus_router_client.py tests/test_forwarding_zmq_transport.py
# All checks passed!

mypy bt_api_py/forwarding/memory.py bt_api_py/forwarding/transport.py tests/test_forwarding_bus_router_client.py tests/test_forwarding_zmq_transport.py
# Success: no issues found in 4 source files

ruff format --check bt_api_py/forwarding/memory.py bt_api_py/forwarding/transport.py tests/test_forwarding_bus_router_client.py tests/test_forwarding_zmq_transport.py
# 4 files already formatted

ruff format --check bt_api_py tests
# 109 files already formatted

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 456 passed in 28.65s
```

## 后续候选项

1. 继续扫描 forwarding transport 中 command server 的异常可观测性，目前错误会回给 client，但 server 侧没有日志。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
