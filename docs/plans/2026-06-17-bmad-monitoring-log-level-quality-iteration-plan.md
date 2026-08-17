# 2026-06-17 BMad Monitoring Log Level Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮将 `MonitoringConfig` 的 log level 枚举校验列为后续候选项。继续扫描后发现：

1. `setup_logging_for_production()` 原先对未知 level 使用 `getattr(logging, ..., logging.INFO)`，拼写错误会静默回退到 INFO。
2. `MonitoringConfig(log_level=...)` 原先不校验 log level 值。
3. `pyproject.toml` 声明 `target-version = "py39"`，而前序数值校验里出现了 `isinstance(value, int | float)`，该写法不适合作为 Python 3.9 运行时代码。

## 本轮实施

1. `bt_api_py/monitoring/config.py`
   - 新增 `LOG_LEVELS` 映射，显式支持 `CRITICAL`、`FATAL`、`ERROR`、`WARNING`、`WARN`、`INFO`、`DEBUG`、`NOTSET`。
   - 新增 `_resolve_log_level()`，统一解析和校验 log level。
   - `setup_logging_for_production()` 改为使用 `_resolve_log_level()`，未知 level 会抛 `ValueError`。
   - `MonitoringConfig._validate()` 增加 log level 校验。
   - 将 `isinstance(value, int | float)` 改为 `isinstance(value, (int, float))`，保持 Python 3.9 运行时兼容。

2. `tests/test_monitoring_contracts.py`
   - 新增 `test_monitoring_config_rejects_invalid_log_level()`。
   - 新增 `test_setup_logging_for_production_rejects_invalid_level()`。
   - 继续保留 lowercase `debug` 的 logging setup 覆盖，确认大小写兼容。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 33 passed in 0.80s

ruff check bt_api_py/monitoring/config.py tests/test_monitoring_contracts.py
# Fixed import order, then passed

mypy bt_api_py/monitoring/config.py tests/test_monitoring_contracts.py
# Success: no issues found in 2 source files

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 452 passed in 15.00s
```

## 后续候选项

1. 扫描项目中其他 Python 3.9 不兼容语法或运行时表达式。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
