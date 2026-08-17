# 2026-06-17 BMad ELK Response Failure Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮文档将 ELK search/index response failure 覆盖列为后续候选项。继续扫描 `bt_api_py/monitoring/elk.py` 后，确认：

1. `ElasticsearchClient.index_document()` 已在 Elasticsearch 非 200/201 响应时抛出 `RuntimeError`。
2. `ELKIntegration.search_logs()` 已在 Elasticsearch search 非 200 响应时抛出 `RuntimeError`。
3. 这两个失败响应路径缺少测试约束，后续重构可能破坏错误信息、请求 URL 或查询 payload 而不被发现。

## 本轮实施

1. `tests/test_monitoring_contracts.py`
   - 新增 `FakeHTTPResponse` 和 `FakeHTTPSession` 测试支架，覆盖 async context manager 形式的 aiohttp response/session。
   - 新增 `test_elasticsearch_index_document_reports_failed_response()`，确认索引失败时错误信息包含 HTTP status 和响应正文，并确认写入 URL 与 JSON payload。
   - 新增 `test_elk_search_logs_reports_failed_response()`，确认搜索失败时错误信息包含 HTTP status 和响应正文，并约束 search URL、过滤条件、全文检索字段、排序和 size。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 16 passed in 0.98s

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 435 passed in 15.89s
```

## 后续候选项

1. 继续扫描 monitoring 模块中 stdlib logging 与 spdlog proxy 的兼容边界。
2. 为 ELK search success path 增加查询组合覆盖，包括时间范围、exchange、component 和 match_all。
3. 评估 LogstashHandler 的失败响应 retry/backoff 策略，避免仅 warning 造成日志静默丢失。
