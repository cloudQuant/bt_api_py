# 2026-06-17 BMad Forwarding State Close Idempotency Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮为 `SQLiteStateStore` 增加了 context manager 支持。继续检查 close 生命周期后发现：

1. `close()` 直接调用 `sqlite3.Connection.close()`。
2. 重复调用 close 会暴露 SQLite 底层行为。
3. 关闭后继续访问 state store 时，也会暴露 sqlite 底层错误。
4. 对资源清理 API 来说，幂等 close 更适合 cleanup/finally/context manager 场景。

## 本轮实施

1. `bt_api_py/forwarding/state.py`
   - 新增 `_closed` 状态位。
   - `close()` 改为幂等：重复调用直接返回。
   - 新增 `_ensure_open()`。
   - `get_command_ack()`、`save_command_ack()`、`save_private_event()`、`list_private_events()` 和 `_init_schema()` 在使用连接前检查状态。
   - 关闭后访问抛 `ForwardingError("SQLiteStateStore is closed")`，避免泄露 sqlite 底层错误。

2. `tests/test_forwarding_bus_router_client.py`
   - 更新 context manager 测试，断言关闭后访问抛 `ForwardingError`。
   - 新增 `test_sqlite_state_store_close_is_idempotent()`。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_forwarding_bus_router_client.py
# 15 passed in 0.82s

ruff check bt_api_py/forwarding/state.py tests/test_forwarding_bus_router_client.py
# All checks passed!

mypy bt_api_py/forwarding/state.py tests/test_forwarding_bus_router_client.py
# Success: no issues found in 2 source files

ruff format --check bt_api_py/forwarding/state.py tests/test_forwarding_bus_router_client.py
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
# 460 passed in 14.94s
```

## 后续候选项

1. 检查 state store 是否需要支持 limit=0 表示不返回事件，而不是强制最小 1。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
