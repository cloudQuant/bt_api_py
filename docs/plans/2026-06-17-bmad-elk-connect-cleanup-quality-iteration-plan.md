# 2026-06-17 BMad ELK Connect Cleanup Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮将 `ELKIntegration.connect()` 的部分连接失败清理列为后续候选项。扫描 `bt_api_py/monitoring/elk.py` 后发现：

1. `connect()` 会依次连接 Elasticsearch、创建 index template、连接 Logstash。
2. 如果 Elasticsearch 已连接后，index template 创建失败，原实现不会主动关闭 Elasticsearch session。
3. 如果 Logstash 连接失败，原实现同样不会主动关闭前面已经成功打开的 Elasticsearch 资源。

这是典型的部分初始化失败资源泄漏风险。

## 本轮实施

1. `bt_api_py/monitoring/elk.py`
   - 将 `ELKIntegration.connect()` 的多资源初始化包入 `try/except`。
   - 任一步失败时，分别尝试 `elasticsearch_client.disconnect()` 和 `logstash_handler.disconnect()`。
   - 清理失败仅记录 debug 日志，保留并重新抛出原始连接异常。
   - 失败后显式保持 `_connected = False`。

2. `tests/test_monitoring_contracts.py`
   - 新增 `test_elk_connect_cleans_up_when_index_template_fails()`。
   - 新增 `test_elk_connect_cleans_up_when_logstash_connect_fails()`。
   - 两个测试均约束调用顺序、异常透传和 `_connected` 状态。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 20 passed in 1.05s

ruff check bt_api_py/monitoring/elk.py tests/test_monitoring_contracts.py
# All checks passed!

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 439 passed in 17.66s
```

## 后续候选项

1. 继续扫描 ELK disconnect 的幂等性和 root logger handler 移除行为。
2. 评估 LogstashHandler 的失败响应 retry/backoff 策略，避免仅 warning 造成日志静默丢失。
3. 检查全局 `_elk_integration` 在 connect 失败后的缓存状态，避免下次 setup 复用半初始化对象。
