# BMAD 质量迭代计划 - MarketDataHub 显式字段优先级

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮检查 `MarketDataHub` 对外发布 tick/orderbook 时的 payload 合并语义。

`publish_tick(price=..., volume=..., direction=..., payload=...)` 和 `publish_orderbook(bids=..., asks=..., payload=...)` 都有显式参数，也允许传入原始 payload。此前实现使用 `dict.setdefault()`，导致 payload 中的同名字段会覆盖显式参数。

## 问题

- `publish_tick(price=3500, payload={"price": 1})` 实际 payload 里的 `price` 是 `1`。
- `publish_orderbook(bids=[...], payload={"bids": []})` 实际 payload 里的 `bids` 是空列表。
- 公共 API 的显式参数和最终事件内容不一致，容易造成策略端收到错误行情。

## 方案

- 保留 payload 作为额外字段来源。
- 显式参数始终覆盖 payload 中的同名字段:
  - tick: `price`, `volume`, `direction`
  - orderbook: `bids`, `asks`
- 增加测试覆盖同名字段覆盖和额外字段保留。

## 已完成变更

- 更新 `bt_api_py/forwarding/hub.py`
  - `publish_tick()` 使用显式字段覆盖 payload。
  - `publish_orderbook()` 使用显式字段覆盖 payload。
- 更新 `tests/test_forwarding_bus_router_client.py`
  - 增加 `test_market_data_hub_explicit_market_fields_override_payload()`。

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
- pytest: 485 passed in 14.61s

## 后续候选

- 检查 bar/kline 发布入口是否需要补齐并遵循同样字段优先级。
- 为 README forwarding 示例说明 payload 是额外字段，显式参数优先生效。
- 检查 `BtApiForwardingAdapter.normalize()` 对原始 payload 中冲突字段的处理是否需要更明确。
