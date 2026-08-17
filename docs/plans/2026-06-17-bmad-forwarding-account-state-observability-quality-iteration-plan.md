# 2026-06-17 BMad Forwarding Account State Observability Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

本轮从当前 ruff 忽略项和异常处理模式继续扫描，发现 `OrderRouter._publish_account_state()` 存在静默吞错：

1. 下单成功后，router 会异步刷新账户和持仓状态，并发布 private events。
2. 如果账户或持仓查询失败，原实现会 `except Exception: return`。
3. 该策略不会打断已经成功的下单 ack，这是合理的。
4. 但完全静默会让策略侧和运维侧无法判断为什么缺少 account/position 事件。

## 本轮实施

1. `bt_api_py/forwarding/router.py`
   - 引入 `bt_api_base.logging_factory.get_logger`。
   - 新增模块级 `logger = get_logger("forwarding.router")`。
   - 在 `_publish_account_state()` 的异常分支记录 warning。
   - warning 包含 `account_id`、`strategy_id`、异常类型和异常消息。
   - 保持原有行为：状态刷新失败不改变下单 ack，也不向外抛异常。

2. `tests/test_forwarding_bus_router_client.py`
   - 新增 `test_order_router_logs_account_state_refresh_failure()`。
   - 用 fake adapter 模拟下单过程成功、后续账户状态刷新失败。
   - 断言 ack 仍然 accepted。
   - 断言 warning 信息包含必要上下文。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_forwarding_bus_router_client.py
# 10 passed in 0.92s

ruff check bt_api_py/forwarding/router.py tests/test_forwarding_bus_router_client.py
# All checks passed!

mypy bt_api_py/forwarding/router.py tests/test_forwarding_bus_router_client.py
# Success: no issues found in 2 source files

ruff format --check bt_api_py/forwarding/router.py tests/test_forwarding_bus_router_client.py
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
# 453 passed in 18.71s
```

## 后续候选项

1. 继续扫描被 `S110` / `S112` 忽略覆盖的静默异常路径，逐步增加可观测性。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
