# BMAD 质量迭代计划 - ForwardingClient 实时事件缓存可观测性

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮基于上一轮 `ForwardingClient` 增加 `event_cache_size` 后的诊断能力继续检查。

`event_cache_size` 可以限制慢消费者的内存增长，但当缓存达到上限时，`deque(maxlen=...)` 会自动丢弃最旧事件。如果没有可观测指标，策略 runner 很难区分“没有事件”和“消费太慢导致旧事件被覆盖”。

## 问题

- 客户端没有公开 `stats()` 或类似诊断接口。
- 缓存达到上限并丢弃旧事件时没有计数。
- 运维或策略 runner 无法看到当前 pending 事件数量，也无法判断慢消费者是否已经造成事件覆盖。

## 方案

- 为 `ForwardingClient` 增加 `stats()`。
- 暴露:
  - `connected`
  - `event_cache_size`
  - `market_subscription_count`
  - `private_subscription_count`
  - `pending_event_counts`
  - `dropped_event_counts`
- 在缓存 append 前检测 `queue.maxlen` 和当前长度，发生覆盖时累计对应事件类型的 drop 计数。
- tick、orderbook、bar、broker update 均使用同一 helper 统计。
- ZMQ 客户端继承同一行为。

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
  - 新增 `ForwardingClient.stats()`。
  - 新增 `pending_event_counts`，报告 tick、orderbook、bar、broker update 当前待消费数量。
  - 新增 `dropped_event_counts`，报告本地缓存达到 `event_cache_size` 后覆盖旧事件的累计次数。
  - tick、orderbook、bar、broker update 统一通过 `_append_cached_event()` 统计覆盖。
  - `ZmqForwardingClient` 继承同一统计行为。
- 更新 `tests/test_forwarding_bus_router_client.py`
  - 增加 `test_forwarding_client_stats_reports_event_cache_pressure()`，覆盖慢消费者导致的 pending/drop 统计。
- 更新 `README.md`
  - 补充 `ForwardingClient.stats()` 用于诊断慢消费者和事件覆盖。

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
- pytest: 490 passed in 15.31s

## 后续候选

- 将客户端 stats 汇总到 runtime health。
- 为 ZMQ 慢消费者增加端到端丢弃计数测试。
- 将 dropped 计数接入 metrics 后端。
