# 2026-06-17 BMad Monitoring Logging Setup Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

前序迭代已经覆盖了 monitoring setup 的失败清理、ELK 参数传递和配置校验。继续检查 `setup_logging_for_production()` 后发现：

1. 该函数会为生产日志文件创建父目录。
2. 该函数会调用 `logging.basicConfig()` 设置 filename、level 和 format。
3. 这是生产监控初始化入口的一部分，但此前没有直接测试覆盖。

## 本轮实施

1. `tests/test_monitoring_contracts.py`
   - 新增 `test_setup_logging_for_production_creates_parent_and_configures_logging()`。
   - 使用 `tmp_path` 验证日志父目录会被创建。
   - monkeypatch `monitoring_config.logging.basicConfig` 捕获配置参数，避免污染全局 logging 状态。
   - 断言 `filename`、`level` 和 `format` 都符合预期。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 31 passed in 0.70s

ruff check bt_api_py/monitoring/config.py tests/test_monitoring_contracts.py
# All checks passed!

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 450 passed in 20.89s
```

## 后续候选项

1. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
2. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
3. 检查 `MonitoringConfig` 是否需要对 log level 枚举值进行校验，避免拼写错误回退到 INFO。
