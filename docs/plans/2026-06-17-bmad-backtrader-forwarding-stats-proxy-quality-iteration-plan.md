# BMAD 质量迭代计划 - Backtrader ForwardingStore stats 代理

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮基于 `bt_api_py.forwarding` 新增 `ForwardingClient.stats()` 后继续检查 Backtrader 集成层。

`ForwardingClient.stats()` 已能报告实时事件 pending/dropped 计数，Backtrader `ForwardingStore` 也已经可以配置 `event_cache_size`。但策略端通过 `ForwardingStore` 没有公开代理方法读取这些诊断信息，使用者只能访问内部 `_api`。

## 问题

- `ForwardingStore` 没有 `stats()` 方法。
- 策略 runner 无法通过 Store 公共 API 查看 forwarding client 的 pending/dropped 事件计数。
- 使用内部 `_api.stats()` 会把策略代码耦合到底层 client 实现。
- 自定义 client 可能没有 `stats()`，代理需要保持兼容。

## 方案

- 在 sibling `backtrader/backtrader/stores/forwardingstore.py` 增加 `stats(*args, **kwargs)`。
- 如果底层 client 提供 `stats`，透传参数并返回结果。
- 如果底层 client 不提供 `stats`，返回 `{}`，保持自定义 client 兼容。
- 增加单元测试覆盖:
  - 默认 client 的 stats 代理。
  - 自定义无 stats client 的兼容 fallback。
- 更新 sibling backtrader README 的 forwarding 配置说明。

## 验收口径

执行并通过:

```bash
pytest tests/unit/stores/test_forwardingstore.py -q
python -m py_compile backtrader/stores/forwardingstore.py tests/unit/stores/test_forwardingstore.py
```

当前 `bt_api_py` 仓库继续执行:

```bash
ruff format --check bt_api_py tests
ruff check bt_api_py tests
mypy bt_api_py tests
bandit -q -r bt_api_py -c pyproject.toml
pytest -q
```

## 已完成变更

- 更新 sibling `backtrader/backtrader/stores/forwardingstore.py`
  - 新增 `ForwardingStore.stats(*args, **kwargs)`。
  - 底层 client 支持 `stats` 时透传调用。
  - 自定义 client 不支持 `stats` 时返回 `{}`，保持兼容。
- 更新 sibling `backtrader/tests/unit/stores/test_forwardingstore.py`
  - 增加默认 forwarding client stats 代理测试。
  - 增加自定义无 stats client fallback 测试。
- 更新 sibling `backtrader/README.md`
  - 补充 `store.stats()` 可读取 forwarding client 诊断信息。

## 验收结果

sibling `backtrader` 已执行并通过:

```bash
pytest tests/unit/stores/test_forwardingstore.py -q
python -m py_compile backtrader/stores/forwardingstore.py tests/unit/stores/test_forwardingstore.py
git diff --check -- backtrader/stores/forwardingstore.py tests/unit/stores/test_forwardingstore.py README.md
```

结果:

- pytest: 9 passed in 1.00s
- py_compile: exit 0
- git diff --check: exit 0

当前 `bt_api_py` 仓库已执行并通过:

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
- pytest: 492 passed in 22.87s

## 后续候选

- 将 Store stats 接入 Backtrader live runner 日志或 observer。
- 为 ForwardingBroker 暴露最近一次 order command/ack 诊断快照。
