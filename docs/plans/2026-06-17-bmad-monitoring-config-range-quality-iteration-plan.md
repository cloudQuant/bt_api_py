# 2026-06-17 BMad Monitoring Config Range Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮为 `MonitoringConfig` 增加了未知 key 校验。继续检查配置值域后发现：

1. 端口配置仍可能传入 0、负数或超过 65535 的值。
2. timeout 和 interval 配置仍可能传入 0 或负数。
3. 日志文件大小和备份数配置没有范围约束。

这些错误越早暴露越好，否则会延迟到网络初始化或运行时行为中。

## 本轮实施

1. `bt_api_py/monitoring/config.py`
   - 新增 `_validate()`，在初始化完成后统一校验配置值。
   - 新增 `_validate_port()`，约束端口为 `1..65535` 的整数。
   - 新增 `_validate_positive_number()`，约束 interval 和 timeout 为正数。
   - 新增 `_validate_positive_int()`，约束 `log_max_size` 为正整数。
   - 新增 `_validate_non_negative_int()`，约束 `log_backup_count` 为非负整数。

2. `tests/test_monitoring_contracts.py`
   - 新增 `test_monitoring_config_rejects_invalid_port()`。
   - 新增 `test_monitoring_config_rejects_invalid_timeout()`。
   - 新增 `test_monitoring_config_rejects_invalid_backup_count()`。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 27 passed in 1.06s

ruff check bt_api_py/monitoring/config.py tests/test_monitoring_contracts.py
# All checks passed!

mypy bt_api_py/monitoring/config.py tests/test_monitoring_contracts.py
# Success: no issues found in 2 source files

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 446 passed in 15.70s
```

## 后续候选项

1. 评估 LogstashHandler 的失败响应 retry/backoff 策略，避免仅 warning 造成日志静默丢失。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 检查 `setup_logging_for_production()` 是否应该显式创建 log parent directory 失败时的错误测试。
