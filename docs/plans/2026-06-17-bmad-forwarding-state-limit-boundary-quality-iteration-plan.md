# 2026-06-17 BMad Forwarding State Limit Boundary Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

前序迭代已经加固了 `SQLiteStateStore` 的路径初始化、context manager 和 close 语义。继续检查查询边界后发现：

1. `list_private_events(topic_prefix, limit)` 原先执行 `limit = max(int(limit), 1)`。
2. 调用者传 `limit=0` 时，实际会被改成 1。
3. 这会导致“请求 0 条事件”却返回最近一条事件。
4. 负数 limit 也会被静默改成 1，不利于尽早暴露调用错误。

## 本轮实施

1. `bt_api_py/forwarding/state.py`
   - `limit` 改为先转成 `int`。
   - 负数 limit 抛 `ValueError("limit must be non-negative")`。
   - `limit=0` 在确认 store 未关闭后直接返回空列表。
   - 正数 limit 保持原有查询行为。

2. `tests/test_forwarding_bus_router_client.py`
   - 新增 `test_sqlite_state_store_private_event_limit_boundaries()`。
   - 覆盖 `limit=0` 返回空列表。
   - 覆盖负数 limit 显式抛错。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_forwarding_bus_router_client.py
# 16 passed in 0.86s

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
# 461 passed in 17.34s
```

## 后续候选项

1. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
2. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
3. 检查 forwarding schema 序列化是否需要压缩/反压或大小限制。
