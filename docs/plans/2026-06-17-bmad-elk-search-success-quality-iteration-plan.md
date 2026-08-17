# 2026-06-17 BMad ELK Search Success Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮补齐了 ELK search/index 失败响应路径覆盖。继续扫描 `ELKIntegration.search_logs()` 后发现：

1. 无过滤条件时会走 `match_all` 查询，但没有测试锁定该行为。
2. 多过滤条件组合时会按 level、exchange、component、时间范围构建 bool must 查询，但没有测试约束最终发送给 Elasticsearch 的 payload。
3. 查询结构属于对外可观察契约，后续优化或重构时需要测试保护。

## 本轮实施

1. `tests/test_monitoring_contracts.py`
   - 新增 `datetime` 和 `timezone` 导入，用于构造稳定的 ISO 8601 时间范围。
   - 新增 `test_elk_search_logs_uses_match_all_without_filters()`，覆盖无过滤条件搜索时的 URL、`match_all`、默认排序和默认 size。
   - 新增 `test_elk_search_logs_builds_filtered_query()`，覆盖 level、exchange、component、start/end time 和 size 组合查询。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 18 passed in 0.91s

ruff check bt_api_py/monitoring/elk.py tests/test_monitoring_contracts.py
# All checks passed!

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 437 passed in 14.76s
```

## 后续候选项

1. 继续扫描 monitoring 模块中 stdlib logging 与 spdlog proxy 的兼容边界。
2. 检查 ELKIntegration connect/disconnect 的部分连接失败清理行为，避免 Elasticsearch 已连接但 Logstash 失败时资源泄漏。
3. 评估 LogstashHandler 的失败响应 retry/backoff 策略，避免仅 warning 造成日志静默丢失。
