# 2026-06-17 BMad ELK Log Handler Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- `find .. -path '*/_bmad/_config/bmad-help.csv'` 无输出。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

本轮开始时基础门禁通过：

```bash
ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output
```

扫描 spdlog/logger 兼容性后，发现 ELK log handler 仍有两个运行时风险：

1. `LogstashHandler.format_to_logstash()` 动态导入不存在的 `bt_api_py.logging_system`，直接调用会失败。
2. `LogstashHandler.emit()` 使用 `asyncio.create_task(self._send_log(...))`。在同步 logging 场景且没有运行中的 event loop 时，会抛 `RuntimeError`，并可能留下未 awaited coroutine warning。

## 本轮实施

1. `bt_api_py/monitoring/elk.py`
   - 在模块内新增 `correlation_id_var`、`request_id_var`、`session_id_var`、`user_id_var` 四个 `ContextVar`。
   - `format_to_logstash()` 改为使用本地 contextvars，不再依赖不存在的 `bt_api_py.logging_system`。
   - `emit()` 改为先获取 running loop，再创建 task；没有 running loop 时进入 `handleError(record)`，避免创建未 awaited coroutine。

2. `tests/test_monitoring_contracts.py`
   - 新增 contextvars 格式化测试，确认 `correlation_id` 和 `request_id` 会进入 Logstash payload。
   - 新增同步 `emit()` 无 running loop 测试，确认会调用 `handleError()` 且不产生 `RuntimeWarning`。

3. `tests/test_monitoring_contracts.py`
   - 新增有 running loop 时 `LogstashHandler.emit()` 正常调度 `_send_log()` 的测试。
   - 测试确认 payload 被异步发送，且不会误触发 `handleError()`。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 13 passed in 0.94s

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 432 passed in 16.21s

pytest -q tests/test_monitoring_contracts.py
# 14 passed in 1.11s

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 433 passed in 14.94s
```

## 后续候选项

当前 ELK log formatting 和 emit 的运行时问题已收敛。后续仍可继续：

1. 对 ELK search/index response failure 做测试覆盖。
2. 对 monitoring 模块中使用 stdlib logging 与 spdlog proxy 的边界继续做兼容性扫描。
3. 对 LogstashHandler 的 HTTP response failure payload 和 retry/backoff 策略做后续设计。
