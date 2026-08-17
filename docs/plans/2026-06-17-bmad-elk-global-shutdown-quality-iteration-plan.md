# 2026-06-17 BMad ELK Global Shutdown Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

前序迭代已经修复了 `setup_elk_integration()` 在 connect 失败后的全局缓存清理问题。继续检查全局 shutdown 入口后发现：

1. `shutdown_elk_integration()` 会调用 `_elk_integration.disconnect()`。
2. 原实现只有在 disconnect 成功后才会将 `_elk_integration` 置为 `None`。
3. 如果 disconnect 抛错，全局变量会继续保留失败实例，后续 setup/shutdown 可能复用该脏对象。

这是全局状态一致性问题，尤其会影响监控系统故障恢复。

## 本轮实施

1. `bt_api_py/monitoring/elk.py`
   - 将 `shutdown_elk_integration()` 的全局清理改为 `try/finally`。
   - disconnect 异常仍向外传播。
   - 无论 disconnect 成功或失败，都会清空 `_elk_integration`。

2. `tests/test_monitoring_contracts.py`
   - 新增 `test_shutdown_elk_integration_clears_global_after_disconnect_failure()`。
   - 通过 fake integration 模拟 disconnect 失败。
   - 断言异常会向外抛出，同时 `get_elk_integration()` 返回 `None`。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 28 passed in 0.94s

ruff check bt_api_py/monitoring/elk.py tests/test_monitoring_contracts.py
# All checks passed!

mypy bt_api_py/monitoring/elk.py tests/test_monitoring_contracts.py
# Success: no issues found in 2 source files

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 447 passed in 15.17s
```

## 后续候选项

1. 评估 LogstashHandler 的失败响应 retry/backoff 策略，避免仅 warning 造成日志静默丢失。
2. 检查 `setup_logging_for_production()` 的目录创建和 logging.basicConfig 行为是否需要更明确的测试覆盖。
3. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
