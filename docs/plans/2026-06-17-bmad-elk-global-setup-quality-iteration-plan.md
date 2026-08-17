# 2026-06-17 BMad ELK Global Setup Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮修复了 `ELKIntegration.connect()` 的部分连接失败清理。继续检查全局 setup 入口后发现：

1. `setup_elk_integration()` 会先创建并缓存全局 `_elk_integration`。
2. 随后调用 `_elk_integration.connect()`。
3. 如果 connect 失败，原实现会保留这个失败对象，后续调用可能复用半初始化实例。

这会放大部分初始化失败的影响范围，属于全局状态一致性问题。

## 本轮实施

1. `bt_api_py/monitoring/elk.py`
   - 在 `setup_elk_integration()` 中包住 `connect()` 调用。
   - connect 失败时将 `_elk_integration` 置回 `None`。
   - 保留并重新抛出原始异常。

2. `tests/test_monitoring_contracts.py`
   - 新增 `test_setup_elk_integration_clears_global_after_connect_failure()`。
   - 测试通过替换 `ELKIntegration` 类模拟 connect 失败，并确认失败后 `get_elk_integration()` 返回 `None`。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_monitoring_contracts.py
# 21 passed in 0.68s

ruff check bt_api_py/monitoring/elk.py tests/test_monitoring_contracts.py
# All checks passed!

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 440 passed in 14.69s
```

## 后续候选项

1. 继续扫描 ELK disconnect 的幂等性和 root logger handler 移除行为。
2. 评估 LogstashHandler 的失败响应 retry/backoff 策略，避免仅 warning 造成日志静默丢失。
3. 检查 monitoring 配置对象与 ELK request timeout 之间的传参契约覆盖。
