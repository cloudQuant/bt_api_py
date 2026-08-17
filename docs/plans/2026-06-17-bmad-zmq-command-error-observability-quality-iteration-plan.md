# 2026-06-17 BMad ZMQ Command Error Observability Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮修复了 forwarding command handler 对 generic awaitable 的支持。继续检查 ZMQ command server 错误路径后发现：

1. `ZmqCommandServer._run()` 会捕获 handler 异常，并向 client 返回 rejected `CommandAck`。
2. 该 wire protocol 行为合理，client 能拿到失败原因。
3. 但 server 侧原先没有日志，服务端运维视角无法看到 handler 异常上下文。

交易命令转发场景里，server 侧缺少错误可观测性会增加排查成本。

## 本轮实施

1. `bt_api_py/forwarding/transport.py`
   - 引入 `bt_api_base.logging_factory.get_logger`。
   - 新增模块级 `logger = get_logger("forwarding.transport")`。
   - 在 `ZmqCommandServer._run()` 的异常转 rejected ack 分支记录 warning。
   - warning 包含 `command_id`、`idempotency_key`、异常类型和异常消息。
   - 保持原有 wire protocol：client 仍收到 rejected `CommandAck`。

2. `tests/test_forwarding_zmq_transport.py`
   - 扩展 `test_zmq_router_dealer_error_ack_preserves_command_identity()`。
   - monkeypatch transport logger，断言 server 侧 warning 被记录。
   - 继续断言 rejected ack 保留 command identity。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_forwarding_zmq_transport.py
# 6 passed in 2.17s

ruff check bt_api_py/forwarding/transport.py tests/test_forwarding_zmq_transport.py
# All checks passed!

mypy bt_api_py/forwarding/transport.py tests/test_forwarding_zmq_transport.py
# Success: no issues found in 2 source files

ruff format --check bt_api_py/forwarding/transport.py tests/test_forwarding_zmq_transport.py
# 2 files already formatted

ruff format --check bt_api_py tests
# 109 files already formatted

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 456 passed in 28.42s
```

## 后续候选项

1. 扫描 forwarding state store 的 SQLite 边界错误处理和事务行为。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
