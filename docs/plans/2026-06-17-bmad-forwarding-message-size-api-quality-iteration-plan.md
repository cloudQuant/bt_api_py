# BMAD 质量迭代计划 - Forwarding 消息大小限制公共 API

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮从上一轮新增的 forwarding message size guard 继续检查公共 API 一致性。

`MAX_MESSAGE_BYTES` 定义了 forwarding transport 边界允许的最大消息大小，是调用方调试、测试和配置周边系统时需要知道的公共行为。此前常量只存在于 `bt_api_py.forwarding.schema` 内部模块，`bt_api_py.forwarding` 和顶层 `bt_api_py` 没有导出，和其它 forwarding 类型的公共导出风格不一致。

## 问题

- `bt_api_py.forwarding` 已导出 `MarketEvent`、`OrderCommand`、`ForwardingClient` 等公共类型，但没有导出 `MAX_MESSAGE_BYTES`。
- 顶层 `bt_api_py` 已重新导出 forwarding 的主要公共类型，但没有同步导出该边界常量。
- 调用方如果需要引用消息大小限制，只能依赖内部 `schema` 模块路径。

## 方案

- 在 `bt_api_py.forwarding.__init__` 中导入并加入 `MAX_MESSAGE_BYTES` 到 `__all__`。
- 在顶层 `bt_api_py.__init__` 中同步重新导出 `MAX_MESSAGE_BYTES`。
- 增加测试锁定 `bt_api_py.forwarding.MAX_MESSAGE_BYTES` 和 `bt_api_py.MAX_MESSAGE_BYTES` 与 schema 常量一致。

## 已完成变更

- 更新 `bt_api_py/forwarding/__init__.py`
  - 导出 `MAX_MESSAGE_BYTES`。
- 更新 `bt_api_py/__init__.py`
  - 顶层重新导出 `MAX_MESSAGE_BYTES`。
- 更新 `tests/test_forwarding_schema.py`
  - 增加 forwarding 包公共导出测试。
- 更新 `tests/test_bt_api_quality.py`
  - 增加顶层包公共导出测试。

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
- pytest: 473 passed in 15.19s

## 后续候选

- 检查 forwarding 其它公共配置项是否也应该通过 `bt_api_py.forwarding` 统一导出。
- 给 README 的 forwarding 章节补充 message size guard 和 command timeout 行为说明。
- 检查 `ZmqForwardingClient` 与 `ForwardingClient` 的 timeout 参数命名是否需要统一。
