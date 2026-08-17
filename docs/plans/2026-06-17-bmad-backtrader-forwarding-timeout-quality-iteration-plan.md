# BMAD 质量迭代计划 - Backtrader Forwarding 超时配置一致性

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮检查同级 `~/Documents/new_projects/backtrader` 中的 forwarding 接入文档和实现是否与 `bt_api_py.forwarding` 的最新行为一致。

`bt_api_py` 已经为嵌入式 `ForwardingClient` 增加 `command_timeout`，并为 ZMQ 客户端保留 `command_timeout_ms`。backtrader 的 `ForwardingStore` 对外暴露的是 `command_timeout_ms`，但此前该参数只传给 `ZmqForwardingClient`，嵌入式 `ForwardingClient` 路径没有使用它。

## 问题

- `ForwardingStore(command_timeout_ms=...)` 在 ZeroMQ path 生效。
- 同一个参数在 embedded bus path 被忽略，实际只使用 `ForwardingClient` 默认值。
- README 没有说明该参数对两种 path 的行为，也没有说明查询缓存回退和交易命令失败暴露语义。

## 方案

- 在 backtrader 的 `ForwardingStore` embedded path 中把 `command_timeout_ms` 转成秒，传给 `ForwardingClient(command_timeout=...)`。
- 在 backtrader README 英文和中文 forwarding 章节中说明:
  - `command_timeout_ms` 同时作用于 ZeroMQ 和 embedded client。
  - 余额、持仓、开放订单查询超时时可以回退缓存。
  - 下单和撤单仍向策略暴露命令失败。
- 在 ZeroMQ 和 embedded 示例中显式展示 `command_timeout_ms=2000`。
- 增加单元测试锁定 embedded path 的 timeout 传递。

## 已完成变更

- 更新 `/Users/yunjinqi/Documents/new_projects/backtrader/backtrader/stores/forwardingstore.py`
  - embedded `ForwardingClient` 增加 `command_timeout=command_timeout_ms / 1000.0`。
- 更新 `/Users/yunjinqi/Documents/new_projects/backtrader/README.md`
  - 英文和中文 forwarding 章节补充 timeout/cache fallback 语义。
  - ZeroMQ 和 embedded 示例补充 `command_timeout_ms=2000`。
- 更新 `/Users/yunjinqi/Documents/new_projects/backtrader/tests/unit/stores/test_forwardingstore.py`
  - 增加 `test_forwarding_store_passes_command_timeout_to_embedded_client()`。

## 验收结果

backtrader targeted checks 已执行并通过:

```bash
ruff format --check backtrader/stores/forwardingstore.py tests/unit/stores/test_forwardingstore.py
ruff check backtrader/stores/forwardingstore.py tests/unit/stores/test_forwardingstore.py
mypy backtrader/stores/forwardingstore.py tests/unit/stores/test_forwardingstore.py
pytest -q tests/unit/stores/test_forwardingstore.py
```

结果:

- ruff format: 2 files already formatted
- ruff check: All checks passed
- mypy: Success, no issues found in 2 source files
- pytest: 5 passed in 1.22s

当前 bt_api_py 完整门禁也已执行并通过:

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
- pytest: 473 passed in 14.78s

## 后续候选

- 为 backtrader forwarding examples 增加可执行 smoke test，避免 README 示例和构造参数分叉。
- 检查 `ForwardingStore` 是否需要暴露 `replay` 参数的 README 示例。
- 检查 bt_api_py 与 backtrader 双仓库中的 forwarding 文档是否可以抽取为一份共享协议说明。
