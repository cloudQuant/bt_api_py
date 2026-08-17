# BMAD 质量迭代计划 - ForwardingClient 实时事件缓存上限

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮基于上一轮 `ForwardingClient.disconnect()` 清理事件缓存后的后续候选继续检查。

`ForwardingClient` 会把策略尚未 poll 的 tick、orderbook、bar 和 broker update 缓存在内存队列中。当前这些队列都是无界 `deque`，当策略处理速度低于行情或私有事件推送速度时，缓存会持续增长。

## 问题

- `_ticks`、`_orderbooks`、`_bars` 使用无界 `deque`，慢消费者可能导致内存持续增长。
- `_broker_updates` 也使用无界 `deque`，订单/成交/错误事件积压时同样存在风险。
- ZMQ 多进程部署更容易出现消费者暂停或延迟，默认无界缓存不适合作为稳健默认值。
- 仍需要保留显式无界模式，便于测试或少数需要完整事件积压的本地场景。

## 方案

- 为 `ForwardingClient` 增加 `event_cache_size: int | None = 4096`。
  - 默认保留最近 4096 条未消费实时事件，限制慢消费者内存风险。
  - `None` 表示显式无界缓存。
  - `0` 表示不缓存实时事件。
- `ZmqForwardingClient` 同步支持 `event_cache_size` 并传给父类。
- 使用 helper 创建事件队列，保证 tick、orderbook、bar 和 broker update 使用相同 maxlen。
- 增加测试覆盖:
  - 缓存满后保留最新事件，丢弃最旧事件。
  - 负数 `event_cache_size` 被拒绝。

## 验收口径

执行并通过:

```bash
ruff format --check bt_api_py tests
ruff check bt_api_py tests
mypy bt_api_py tests
bandit -q -r bt_api_py -c pyproject.toml
pytest -q
```

## 已完成变更

- 更新 `bt_api_py/forwarding/client.py`
  - `ForwardingClient` 新增 `event_cache_size` 参数，默认 `4096`。
  - `event_cache_size=None` 保留显式无界缓存能力。
  - `event_cache_size=0` 支持完全不缓存实时事件。
  - tick、orderbook、bar 和 broker update 队列统一通过 helper 创建，保证 maxlen 语义一致。
  - `ZmqForwardingClient` 同步支持 `event_cache_size`。
- 更新 `tests/test_forwarding_bus_router_client.py`
  - 增加慢消费者场景测试，验证缓存上限保留最新事件。
  - 增加负数 `event_cache_size` 校验测试。
- 更新 `README.md`
  - 补充客户端实时事件缓存默认上限和 `event_cache_size=None` 的使用边界。

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
- pytest: 489 passed in 22.07s

## 后续候选

- 为事件缓存丢弃计数增加可观测指标。
- 为 ZMQ 客户端增加端到端慢消费者缓存上限测试。
- 评估是否需要按 tick/orderbook/bar/broker update 分别设置缓存上限。
