# 2026-06-17 BMad ELK Disconnect Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮将 ELK disconnect 幂等性和 root logger handler 移除行为列为后续候选项。扫描 `ELKIntegration.disconnect()` 后发现：

1. 原实现会先移除 root logger 上的 Logstash handler。
2. 随后依次调用 `elasticsearch_client.disconnect()` 和 `logstash_handler.disconnect()`。
3. 如果 Elasticsearch 关闭失败，Logstash 关闭不会执行，`_connected` 也不会重置。

这会造成 shutdown 阶段的资源释放不完整。

## 本轮实施

1. `bt_api_py/monitoring/elk.py`
   - `ELKIntegration.disconnect()` 改为分别尝试关闭 Elasticsearch 和 Logstash。
   - 第一个关闭失败不会阻断后续关闭动作。
   - 清理异常通过 debug 日志记录。
   - 所有关闭尝试结束后将 `_connected` 置为 `False`。
   - 如任一关闭失败，最终抛出 `RuntimeError("Failed to disconnect ELK stack")`，并保留原始异常作为 cause。

2. `tests/test_monitoring_contracts.py`
   - 新增 `test_elk_disconnect_attempts_all_resources_when_one_disconnect_fails()`。
   - 覆盖 Elasticsearch 关闭失败时仍会尝试关闭 Logstash。
   - 同时确认 root logger handler 已移除、`_connected` 已重置、原始异常保留为 cause。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 22 passed in 0.69s

ruff check bt_api_py/monitoring/elk.py tests/test_monitoring_contracts.py
# All checks passed!

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 441 passed in 15.31s
```

## 后续候选项

1. 评估 LogstashHandler 的失败响应 retry/backoff 策略，避免仅 warning 造成日志静默丢失。
2. 检查 monitoring 配置对象与 ELK request timeout 之间的传参契约覆盖。
3. 扫描 tests 中新 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
