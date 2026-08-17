# 2026-06-17 BMad Monitoring ELK Timeout Config Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

前序迭代已经为 ELK client 和 Logstash handler 增加 timeout 配置，并补齐了 aiohttp timeout 测试。继续检查配置入口后发现：

1. `MonitoringConfig` 已包含 `elk_request_timeout`。
2. `setup_monitoring()` 已将 `elk_request_timeout` 传给 `setup_elk_integration(request_timeout=...)`。
3. 该传参链路没有测试保护，后续重构配置初始化或 ELK setup 参数时可能被遗漏。

## 本轮实施

1. `tests/test_monitoring_contracts.py`
   - 新增 `test_setup_monitoring_passes_elk_request_timeout()`。
   - 隔离 logging、metrics、Prometheus、ELK 和 Grafana 初始化。
   - 断言 `setup_monitoring()` 会把 Elasticsearch、Logstash 和 `request_timeout` 的配置完整传给 `setup_elk_integration()`。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 23 passed in 0.70s

ruff check bt_api_py/monitoring/config.py tests/test_monitoring_contracts.py
# All checks passed!

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 442 passed in 14.74s
```

## 后续候选项

1. 评估 LogstashHandler 的失败响应 retry/backoff 策略，避免仅 warning 造成日志静默丢失。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续检查配置类的类型约束，目前 `MonitoringConfig(**kwargs: object)` 会接受未知 key 且不做类型校验。
