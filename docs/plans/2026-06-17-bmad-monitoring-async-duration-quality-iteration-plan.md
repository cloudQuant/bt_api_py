# 2026-06-17 BMad Monitoring Async Duration Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

本轮继续扫描 monitoring 相关装饰器测试覆盖。检查 `monitor_async_performance()` 后发现：

1. 同步 `monitor_performance()` 使用 `PerformanceTimer`，函数失败时也会记录 duration。
2. 异步 `monitor_async_performance()` 只在 await 成功后记录 duration。
3. 如果被监控的 async 函数抛异常，errors counter 会增加，但 duration histogram 不会记录该失败调用耗时。
4. 这会让异步接口在失败场景下的延迟观测不完整，也与同步装饰器行为不一致。

## 本轮实施

1. `bt_api_py/monitoring/decorators.py`
   - 将 `monitor_async_performance()` 的 `start_time` 移到 `try` 前。
   - 在 `finally` 中统一记录 duration。
   - 保持原有异常语义：失败时增加 errors counter 并重新抛出异常。

2. `tests/test_monitoring.py`
   - 新增 `test_monitor_async_performance_tracks_success_errors_and_duration()`。
   - 覆盖一次成功调用和一次失败调用。
   - 断言 calls、errors 和 duration histogram 都按预期更新。
   - 明确锁定失败调用也会计入 duration。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring.py
# 22 passed in 1.88s

ruff check bt_api_py/monitoring/decorators.py tests/test_monitoring.py
# All checks passed!

mypy bt_api_py/monitoring/decorators.py tests/test_monitoring.py
# Success: no issues found in 2 source files

ruff format --check bt_api_py/monitoring/decorators.py tests/test_monitoring.py
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
# 454 passed in 27.78s
```

## 后续候选项

1. 继续扫描被 `S110` / `S112` 忽略覆盖的静默异常路径，逐步增加可观测性。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
