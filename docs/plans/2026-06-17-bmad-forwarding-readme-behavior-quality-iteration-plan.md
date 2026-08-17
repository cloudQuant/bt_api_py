# BMAD 质量迭代计划 - Forwarding README 行为一致性

日期: 2026-06-17

## 背景

本轮继续按本地 `.claude/skills/bmad-help/workflow.md` 的方式执行质量迭代。标准 `_bmad/_config/bmad-help.csv` 在当前项目中不可用，因此无法加载完整 BMAD catalog。本轮从 forwarding 最近新增的行为边界继续检查 README 是否和实现保持一致。

前序迭代已经新增了 `MAX_MESSAGE_BYTES`、嵌入式 `ForwardingClient.command_timeout`、查询类方法超时缓存回退等行为。这些都是使用 forwarding 时需要知道的公共语义，但 README 的中英文 forwarding 章节仍只描述了模块组成，没有说明边界限制和超时行为。

## 问题

- README 未说明 forwarding payload 会在 JSON/ZMQ 传输路径前受 `MAX_MESSAGE_BYTES` 限制。
- README 未说明嵌入式 `ForwardingClient` 的同步命令超时配置。
- README 未说明余额、持仓、开放订单查询在 handler 缺失或 timeout 时会返回本地缓存。
- README 示例未展示 `command_timeout` / `command_timeout_ms` 参数。

## 方案

- 更新英文 forwarding 章节，补充 message size guard 和 command timeout/cache fallback 说明。
- 更新中文 forwarding 章节，保持同等信息量。
- 更新英文和中文示例:
  - `ForwardingClient(..., command_timeout=2.0)`
  - `ZmqForwardingClient(..., command_timeout_ms=2000)`

## 已完成变更

- 更新 `README.md`
  - 英文 Market data and order forwarding 章节。
  - 英文 Embedded forwarding runtime 示例。
  - 英文 ZeroMQ forwarding service 示例。
  - 中文 行情与交易转发 章节。
  - 中文 嵌入式转发运行时示例。
  - 中文 ZeroMQ 转发服务示例。

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
- pytest: 473 passed in 16.16s

## 后续候选

- 检查 backtrader README 是否也需要同步 forwarding backend 的超时和缓存语义。
- 给 forwarding 架构文档补充当前已实现项与规划项的状态标记。
- 增加文档片段测试或 README 示例 smoke test，避免示例参数和实际构造函数再次分叉。
