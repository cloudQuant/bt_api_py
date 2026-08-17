# 2026-06-17 BMad Monitoring Config Validation Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮将 `MonitoringConfig` 的配置约束列为后续候选项。扫描调用点后发现：

1. 现有 `MonitoringConfig(...)` 调用点都使用明确的已知参数。
2. 原实现对传入 kwargs 只读取已知 key，未知 key 会被静默忽略。
3. 生产配置中如果拼错配置名，程序不会报错，而是继续使用默认值。

这属于配置可观测性和故障前移问题。

## 本轮实施

1. `bt_api_py/monitoring/config.py`
   - 在 `MonitoringConfig.__init__()` 中显式维护 defaults 映射。
   - 初始化前计算未知 kwargs。
   - 如存在未知配置名，抛出 `ValueError("Unknown monitoring config option(s): ...")`。

2. `tests/test_monitoring_contracts.py`
   - 新增 `test_monitoring_config_rejects_unknown_options()`。
   - 确认未知配置项不会被静默忽略。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 24 passed in 0.70s

ruff check bt_api_py/monitoring/config.py tests/test_monitoring_contracts.py
# All checks passed!

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 443 passed in 14.70s
```

## 后续候选项

1. 继续评估 `MonitoringConfig` 值类型校验，例如端口、timeout、interval 的合法范围。
2. 评估 LogstashHandler 的失败响应 retry/backoff 策略，避免仅 warning 造成日志静默丢失。
3. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
