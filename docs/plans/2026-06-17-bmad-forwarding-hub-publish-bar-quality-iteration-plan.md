# BMAD 质量迭代计划 - MarketDataHub publish_bar 完整性

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮检查 `MarketDataHub` 的公开发布 helper 是否覆盖 forwarding client 已支持的行情类型。

`ForwardingClient` 已支持 `poll_bar()`，`BtApiForwardingAdapter` 也会把 kline/candle 归一化为 `bar` 事件。但 `MarketDataHub` 只有 `publish_tick()` 和 `publish_orderbook()`，没有 `publish_bar()`，导致测试、本地模拟和嵌入式 runtime 发布 K 线时必须手动构造 `MarketEvent`。

## 问题

- schema/client 已支持 bar 事件。
- hub 缺少对应的 `publish_bar()`。
- 本地策略 runner 和测试代码不能用和 tick/orderbook 一致的 helper 发布 bar。

## 方案

- 给 `MarketDataHub` 增加 `publish_bar()`。
- 参数使用 `open_price` 避免覆盖 Python 内置 `open` 名称，最终 payload 字段仍为标准 `open/high/low/close`。
- 沿用前一轮规则：显式 OHLCV 字段优先于 payload 同名字段。
- 增加测试验证 `ForwardingClient.poll_bar()` 可消费该 helper 发布的事件。

## 已完成变更

- 更新 `bt_api_py/forwarding/hub.py`
  - 新增 `publish_bar()`。
- 更新 `tests/test_forwarding_bus_router_client.py`
  - 增加 `test_market_data_hub_publishes_bars_for_forwarding_client()`。

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
- pytest: 486 passed in 13.79s

## 后续候选

- README forwarding 示例可增加 bar 发布和消费示例。
- 检查 `ForwardingClient` 对 tick/orderbook/bar 的缓存队列是否需要最大长度限制。
- 检查 `MarketDataHub` 是否需要统一校验空 symbol 和空 event_type。
