# 2026-06-17 BMad Forwarding Schema Serialize Quality 迭代计划

## 背景

本轮继续执行 `bmad-help` 风格的重复质量迭代。标准 BMad catalog 仍不可用：

- `command -v bmad-help` 无输出。
- 本地未发现 `_bmad/_config/bmad-help.csv`。

因此本轮继续基于本地 `.claude/skills/bmad-help` 流程说明、当前代码扫描和质量门禁结果推进。

## 本轮输入证据

上一轮统一了 `deserialize_message()` 的 malformed payload 错误契约。继续检查 schema 序列化边界后发现：

1. `serialize_message()` 对不支持的消息类型原先抛 `TypeError`。
2. 如果 payload 内含不可 JSON 序列化对象，`json.dumps()` 的底层异常会直接泄漏。
3. forwarding schema 是 transport 边界的一部分，错误类型应统一为项目层 `ForwardingError`，并保留 cause 方便排查。

## 本轮实施

1. `bt_api_py/forwarding/schema.py`
   - 不支持的 message 类型改为抛 `ForwardingError("Unsupported forwarding message type: ...")`。
   - 包装 `json.dumps()` 的 `TypeError` / `ValueError`。
   - 不可 JSON 序列化 payload 统一抛 `ForwardingError("Forwarding message payload is not JSON serializable: ...")`。
   - 保留原始异常作为 `__cause__`。

2. `tests/test_forwarding_schema.py`
   - 新增 `test_serialize_message_rejects_unsupported_message_type()`。
   - 新增 `test_serialize_message_wraps_unserializable_payload_errors()`。

## 验收记录

已在本地完成以下验收：

```bash
pytest -q tests/test_forwarding_schema.py
# 8 passed in 0.72s

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
# 466 passed in 14.88s
```

## 后续候选项

1. 检查 forwarding schema 是否需要 message size guard，避免异常大 payload 进入 transport。
2. 扫描 tests 中 fake aiohttp/session 支架是否可以进一步收敛，避免后续重复。
3. 继续评估 LogstashHandler 是否需要显式 retry/backoff 配置。
