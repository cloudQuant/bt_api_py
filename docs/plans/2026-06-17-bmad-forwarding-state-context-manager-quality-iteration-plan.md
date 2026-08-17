# 2026-06-17 BMad Forwarding State Context Manager Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

前序迭代已经修复了 `SQLiteStateStore` 的 topic prefix 查询语义和数据库父目录初始化。继续检查生命周期管理后发现：

1. `SQLiteStateStore` 持有 SQLite connection。
2. 当前使用者必须手动调用 `close()`。
3. 测试中已有多处 `try/finally: state_store.close()`，说明资源释放需要调用者持续记忆。
4. 增加 context manager 支持可以降低遗漏关闭连接的概率，同时保持现有 API 兼容。

## 本轮实施

1. `bt_api_py/forwarding/state.py`
   - 新增 `SQLiteStateStore.__enter__()`，返回自身。
   - 新增 `SQLiteStateStore.__exit__()`，调用 `close()`。
   - 保持现有 `close()` 行为不变。

2. `tests/test_forwarding_bus_router_client.py`
   - 新增 `test_sqlite_state_store_context_manager_closes_connection()`。
   - 在 `with SQLiteStateStore(...) as state_store` 中写入并读取 private event。
   - 退出上下文后再次访问，断言 SQLite connection 已关闭。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_forwarding_bus_router_client.py
# 14 passed in 0.75s

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
# 459 passed in 18.94s
```

## 后续候选项

1. 检查 SQLiteStateStore 是否需要在 `close()` 后提供更明确的错误消息或幂等 close。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
