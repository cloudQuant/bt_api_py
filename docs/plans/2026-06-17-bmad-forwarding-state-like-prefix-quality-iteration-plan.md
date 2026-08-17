# 2026-06-17 BMad Forwarding State LIKE Prefix Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

继续检查 forwarding SQLite state store 时发现：

1. `SQLiteStateStore.list_private_events(topic_prefix)` 使用 SQL `LIKE ?` 查询 topic 前缀。
2. 原查询参数为 `f"{topic_prefix}%"`。
3. 如果 `topic_prefix` 包含 `_` 或 `%`，SQLite 会把它们当成 LIKE 通配符。
4. strategy id / account id 可能进入 topic，例如 `strategy.s_1.orders`。
5. 因此查询 `strategy.s_1.` 可能误匹配 `strategy.sa1.` 等非目标 topic。

这是持久化查询语义问题，会导致策略侧读取到不属于自己的 private events。

## 本轮实施

1. `bt_api_py/forwarding/state.py`
   - 新增 `_escape_like_prefix()`。
   - 转义 `\`、`%`、`_`。
   - 将查询改为 `WHERE topic LIKE ? ESCAPE '\\'`。
   - 保持 public API 不变。

2. `tests/test_forwarding_bus_router_client.py`
   - 新增 `test_sqlite_state_store_treats_topic_prefix_as_literal()`。
   - 创建 `strategy.s_1.orders` 和 `strategy.sa1.orders` 两类事件。
   - 断言查询 `strategy.s_1.` 只返回 literal prefix 对应事件。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_forwarding_bus_router_client.py
# 12 passed in 1.50s

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
# 457 passed in 28.87s
```

## 后续候选项

1. 继续检查 SQLiteStateStore 是否需要自动创建数据库父目录。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
