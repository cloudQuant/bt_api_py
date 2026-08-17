# 2026-06-17 BMad ELK Connect Idempotency Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

前序迭代已经修复了 ELK setup、connect、disconnect 和 shutdown 的失败清理路径。继续检查重复调用路径后发现：

1. `setup_elk_integration()` 会复用已有的全局 `ELKIntegration` 实例。
2. 复用实例时仍会调用 `connect()`。
3. 原 `ELKIntegration.connect()` 没有幂等 guard，重复调用会重复初始化 Elasticsearch/Logstash，并再次把同一个 handler 添加到 root logger。

这会导致日志重复发送和 root logger handler 列表膨胀。

## 本轮实施

1. `bt_api_py/monitoring/elk.py`
   - 在 `ELKIntegration.connect()` 开头增加 `_connected` guard。
   - 如果实例已经连接，直接返回，不重复初始化资源，也不重复挂载 Logstash handler。

2. `tests/test_monitoring_contracts.py`
   - 新增 `test_elk_connect_is_idempotent_when_already_connected()`。
   - 覆盖重复 `connect()` 只初始化一次资源。
   - 断言 root logger 中同一个 Logstash handler 只出现一次。
   - 通过 finally 调用 `disconnect()`，确保测试后清理 root logger 状态。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 29 passed in 0.66s

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
# 448 passed in 14.27s
```

## 后续候选项

1. 检查 `disconnect()` 在未连接状态下是否应避免调用底层资源关闭。
2. 评估 LogstashHandler 的失败响应 retry/backoff 策略，避免仅 warning 造成日志静默丢失。
3. 检查 `setup_logging_for_production()` 的目录创建和 logging.basicConfig 行为是否需要更明确的测试覆盖。
