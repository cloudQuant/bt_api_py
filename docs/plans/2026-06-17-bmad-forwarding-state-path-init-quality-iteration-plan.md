# 2026-06-17 BMad Forwarding State Path Init Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮修复了 `SQLiteStateStore.list_private_events()` 的 LIKE prefix 通配符问题。继续检查 state store 初始化路径后发现：

1. `SQLiteStateStore(path)` 会直接调用 `sqlite3.connect(self.path)`。
2. 如果 `path` 是嵌套目录下的数据库文件，并且父目录不存在，初始化会失败。
3. 生产环境中 state store 路径通常来自配置，自动创建父目录可以让初始化行为更稳定。
4. `:memory:` 是 SQLite 特殊路径，不应被当成普通文件路径处理。

## 本轮实施

1. `bt_api_py/forwarding/state.py`
   - 在连接 SQLite 前判断 `self.path != ":memory:"`。
   - 对普通文件路径执行 `Path(self.path).parent.mkdir(parents=True, exist_ok=True)`。
   - 保持 `:memory:` 特殊语义不变。

2. `tests/test_forwarding_bus_router_client.py`
   - 新增 `test_sqlite_state_store_creates_parent_directory()`。
   - 使用嵌套临时目录初始化 `SQLiteStateStore`。
   - 写入并读取 private event，确认目录创建和 schema 初始化都可用。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_forwarding_bus_router_client.py
# 13 passed in 1.63s

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
# 458 passed in 33.34s
```

## 后续候选项

1. 继续检查 SQLiteStateStore 是否需要上下文管理器支持，降低忘记 close 的概率。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
