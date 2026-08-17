# 2026-06-17 BMad Logstash Failure Response Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

前序迭代已经修复了 ELK 生命周期和配置校验问题。继续检查 `LogstashHandler._send_log()` 后发现：

1. 写入 Logstash 时，如果 HTTP response status 大于等于 400，当前策略是记录 warning。
2. 该方法不向调用者抛出异常，以避免 logging handler 内部失败导致日志递归或业务路径被打断。
3. 这个策略合理，但缺少测试契约；后续重构可能误改为静默吞掉失败或向外抛错。

## 本轮实施

1. `tests/test_monitoring_contracts.py`
   - 新增 `test_logstash_handler_send_log_warns_on_failed_http_response()`。
   - 复用现有 fake HTTP response/session 支架模拟 503 响应。
   - 断言发送 URL 和 payload 正确。
   - 断言失败响应会记录 warning，且 `_send_log()` 不向调用者抛错。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 30 passed in 1.80s

ruff check bt_api_py/monitoring/elk.py tests/test_monitoring_contracts.py
# All checks passed!

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 449 passed in 14.48s
```

## 后续候选项

1. 检查 `setup_logging_for_production()` 的目录创建和 logging.basicConfig 行为是否需要更明确的测试覆盖。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
