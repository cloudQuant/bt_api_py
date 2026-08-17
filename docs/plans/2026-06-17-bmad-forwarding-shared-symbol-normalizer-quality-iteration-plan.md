# BMAD 质量迭代计划 - Forwarding 共享 Symbol 归一化规则

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮基于前两轮 symbol key 修正，进一步消除重复实现。

此前 `schema.market_topic()`、`MarketDataHub._key()`、`ForwardingClient` 和 `ZmqForwardingClient` 都各自维护了 `symbol.replace("/", "-")` 的逻辑。虽然当前行为已经一致，但重复规则容易在后续修改时再次分叉。

## 问题

- market symbol 归一化规则散落在多个模块。
- `schema`、`hub`、`client` 中的 topic/key 规则缺少单一来源。
- 外部调用方如果想使用相同规则，只能复制实现。

## 方案

- 在 `bt_api_py.forwarding.schema` 增加 `normalize_market_symbol()`。
- `market_topic()`、`MarketDataHub._key()`、`ForwardingClient`、`ZmqForwardingClient` 全部复用该函数。
- 从 `bt_api_py.forwarding` 包导出该 helper，供客户端和文档示例复用。
- 增加测试锁定 schema helper 和 package export 的一致性。

## 已完成变更

- 更新 `bt_api_py/forwarding/schema.py`
  - 新增 `normalize_market_symbol()`。
  - `market_topic()` 使用共享 helper。
- 更新 `bt_api_py/forwarding/hub.py`
  - `_key()` 使用共享 helper。
- 更新 `bt_api_py/forwarding/client.py`
  - 删除本地 symbol key helper。
  - embedded 和 ZMQ client 的行情订阅、poll、pending、supports、缓存路径复用共享 helper。
- 更新 `bt_api_py/forwarding/__init__.py`
  - 导出 `normalize_market_symbol`。
- 更新 `tests/test_forwarding_schema.py`
  - 增加共享 symbol 归一化测试。

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
- pytest: 484 passed in 18.46s

## 后续候选

- 检查空 symbol 是否应在行情 API 层显式拒绝。
- 为 `normalize_market_symbol()` 在 README forwarding 章节增加说明。
- 检查 private topic 的 account/strategy 字符是否也需要统一归一化规则。
