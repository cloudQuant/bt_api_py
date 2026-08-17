# BMAD 质量迭代计划 - Forwarding Schema 消息大小边界

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮基于上一轮遗留候选项，继续扫描 forwarding schema 的输入边界。

`bt_api_py.forwarding.schema` 是行情和交易转发层的公共序列化边界。此前已经补齐 malformed JSON、non-object JSON、unsupported type、unserializable payload 的错误包装，但没有对传输 payload 做大小限制。异常大的消息会进入 JSON 编码、解码和下游传输路径，错误表现不够明确，也可能拖慢进程。

## 问题

- `serialize_message()` 对任意 Mapping 或 dataclass 消息编码后直接返回 bytes。
- `deserialize_message()` 对 bytes 或 str 输入直接进入 UTF-8 解码和 JSON 解析。
- 没有统一的字节数上限，调用方无法在 schema 边界得到稳定、可识别的超限错误。

## 方案

在 schema 层引入统一的最大消息字节数:

- 新增 `MAX_MESSAGE_BYTES = 1_000_000`。
- 新增 `_ensure_message_size(data: bytes)`，超限时抛出 `ForwardingError`。
- `serialize_message()` 在 JSON 编码完成后检查字节数。
- `deserialize_message()` 在 bytes 或 str 输入进入 JSON 解析前检查字节数。
- Mapping 输入保持现有行为，因为它是进程内对象路径，不是原始传输 payload。

## 已完成变更

- 更新 `bt_api_py/forwarding/schema.py`
  - 添加 `MAX_MESSAGE_BYTES` 常量。
  - 添加 `_ensure_message_size()`。
  - 序列化路径在返回 bytes 前执行大小检查。
  - 反序列化 bytes/str 路径在 JSON 解析前执行大小检查。
- 更新 `tests/test_forwarding_schema.py`
  - 增加超大 payload 序列化拒绝测试。
  - 增加超大 bytes 反序列化提前拒绝测试。

## 验收结果

已执行并通过:

```bash
ruff format --check bt_api_py tests
ruff check bt_api_py tests
mypy bt_api_py tests
bandit -q -r bt_api_py -c pyproject.toml
pytest -q
```

结果:

- ruff format: 109 files already formatted
- ruff check: All checks passed
- mypy: Success, no issues found in 109 source files
- bandit: exit 0
- pytest: 468 passed in 15.27s

## 后续候选

- 为 forwarding transport 层增加更明确的高水位、队列拥塞和丢弃策略文档或配置。
- 为 schema dataclass 输入补充字段类型和必填字段的显式校验。
- 为 ZMQ transport 增加 payload 超限错误在服务端响应中的集成测试。
