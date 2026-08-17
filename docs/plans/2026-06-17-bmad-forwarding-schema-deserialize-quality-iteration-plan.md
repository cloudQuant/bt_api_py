# 2026-06-17 BMad Forwarding Schema Deserialize Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

前序迭代已经加固了 forwarding state store。继续检查 forwarding schema 的外部输入边界后发现：

1. `deserialize_message()` 支持 bytes、str 和 Mapping。
2. 对 malformed JSON、非 UTF-8 bytes、非 object JSON payload，原实现会泄漏 `json.JSONDecodeError`、`UnicodeDecodeError` 或后续属性错误。
3. forwarding transport 调用该函数处理外部输入，错误类型应统一为项目层 `ForwardingError`，同时保留原始异常 cause 便于排查。

## 本轮实施

1. `bt_api_py/forwarding/schema.py`
   - 包装 bytes/string JSON 解析异常。
   - malformed payload 统一抛 `ForwardingError("Invalid forwarding message payload: ...")`。
   - 非 JSON object payload 统一抛 `ForwardingError("Forwarding message payload must be an object, ...")`。
   - 保留原始解析异常作为 `__cause__`。

2. `tests/test_forwarding_schema.py`
   - 新增 malformed JSON 和非 UTF-8 bytes 测试。
   - 新增非 object JSON payload 测试。
   - 保持已有 round-trip 测试不变。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_forwarding_schema.py
# 6 passed in 0.82s

ruff check bt_api_py/forwarding/schema.py tests/test_forwarding_schema.py
# All checks passed!

mypy bt_api_py/forwarding/schema.py tests/test_forwarding_schema.py
# Success: no issues found in 2 source files

ruff format --check bt_api_py/forwarding/schema.py tests/test_forwarding_schema.py
# 2 files already formatted

ruff format --check bt_api_py tests
# 109 files already formatted

ruff check bt_api_py tests
# All checks passed!

mypy bt_api_py tests
# Success: no issues found in 109 source files

bandit -q -r bt_api_py -c pyproject.toml
# exit code 0, no output

pytest -q
# 464 passed in 15.20s
```

## 后续候选项

1. 检查 `serialize_message()` 对非 JSON 可序列化 payload 的错误契约是否需要统一为 `ForwardingError`。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
