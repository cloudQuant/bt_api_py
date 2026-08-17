# BMAD 质量迭代计划 - Forwarding 查询超时缓存回退

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮基于上一轮新增同步命令 timeout 后的行为影响继续扫描。

`ForwardingClient` 的余额、持仓、开放订单查询都是策略侧常用的同步读取接口。此前这些方法在没有 command handler 时会捕获 `RuntimeError` 并返回本地缓存。新增同步命令 timeout 后，如果查询命令超时，查询类方法应保持同样的降级语义，避免因为临时慢 broker 或转发拥塞直接打断策略读取流程。

## 问题

- `get_balance()` 只捕获 `RuntimeError`，不捕获 `TimeoutError`。
- `get_positions()` 只捕获 `RuntimeError`，不捕获 `TimeoutError`。
- `fetch_open_orders()` 只捕获 `RuntimeError`，不捕获 `TimeoutError`。
- 下单和撤单路径需要继续暴露 timeout，不能统一吞掉所有命令超时。

## 方案

- 查询类方法捕获 `(RuntimeError, TimeoutError)`，并返回已有缓存。
- 交易类方法 `submit_order()` 和 `cancel_order()` 保持原行为，timeout 继续抛给调用方。
- 新增测试模拟 bus 查询超时，验证余额、持仓、开放订单均返回缓存快照。

## 已完成变更

- 更新 `bt_api_py/forwarding/client.py`
  - `get_balance()` 查询超时时返回 `_account_cache`。
  - `get_positions()` 查询超时时返回 `_positions_cache`。
  - `fetch_open_orders()` 查询超时时返回 `_orders_cache`。
- 更新 `tests/test_forwarding_bus_router_client.py`
  - 增加 `test_forwarding_client_returns_cached_query_snapshots_when_command_times_out()`。

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
- pytest: 471 passed in 14.49s

## 后续候选

- 查询类方法可以增加超时计数指标，区分无 handler、timeout 和 broker 业务错误。
- 可以把查询缓存回退语义补入 README 或 forwarding 架构文档。
- 可以为 `ZmqForwardingClient` 的 command timeout 添加同类查询缓存回退集成测试。
