# 2026-06-17 BMad Forwarding Hardening 迭代计划

## 背景

用户要求使用 `bmad-help` 分析当前项目可改进优化项，并将改进放入迭代计划后完成开发和验收。

本仓库存在 `.claude/skills/bmad-help`，但缺少 `bmad-help` 工作流要求的 `_bmad/_config/bmad-help.csv` catalog，因此无法按标准 BMad catalog 自动推荐下一 workflow。已按 `bmad-help` 的规则读取本地 skill 说明、项目知识文档和当前代码状态后，进行等价分析。

## 项目事实

- `bt_api_py` 是统一多交易所 API 框架，核心能力包括 REST、异步 REST、WebSocket、CTP、插件注册和标准化数据模型。
- 最近已新增 `bt_api_py.forwarding`，提供行情扇出、订单路由、ZeroMQ runtime/client、SQLite 幂等持久化和 backtrader 接入。
- 架构文档 `docs/architecture/market-trade-forwarding-plan.md` 的第二阶段明确要求补充健康检查、结构化状态、断线/慢消费者风险处理和运行可观测性。
- 当前 forwarding 相关测试已覆盖 schema、内存 bus、ZMQ transport、runtime、SQLite state 和 backtrader forwarding store。

## BMad 分析结论

### 主要改进机会

1. **Forwarding 可观测性不足**
   - Runtime、bus、hub、router 缺少统一 health/status API。
   - 运维或策略进程无法快速判断 command handler 是否注册、订阅数量、ack 缓存数量、state store 是否启用。

2. **ZMQ command 错误 ack 信息不足**
   - `ZmqCommandServer` 在 handler 异常时返回 `command_id="unknown"`，如果命令已经成功解析，会丢失 `command_id` 和 `idempotency_key`，不利于客户端诊断和重试。

3. **Forwarding 新模块 lint 基线未收敛**
   - `ruff check bt_api_py/forwarding tests/test_forwarding*.py` 暴露 import 顺序、未使用 import、typing modernization 问题。
   - 这些问题不会影响运行，但会阻塞质量门禁。

4. **文档已覆盖功能，但缺少运行状态/验收说明**
   - README 已说明 forwarding 用法。
   - 还需要在迭代计划中明确健康检查、错误 ack 和测试验收结果。

## 本轮迭代范围

### 必做项

1. 修复 `bt_api_py.forwarding` 与 forwarding 测试的 ruff 问题。
2. 为 `InMemoryForwardingBus` 增加 `stats()`。
3. 为 `MarketDataHub` 增加 `stats()`。
4. 为 `OrderRouter` 增加异步 `health()`。
5. 为 `ForwardingRuntime` 和 `ZmqForwardingRuntime` 增加 `health()`。
6. 改进 `ZmqCommandServer` handler 异常时的 `CommandAck`，尽量保留原命令的 `command_id`、`idempotency_key`、`account_id` 和 `strategy_id`。
7. 增加测试覆盖以上行为。
8. 跑通 forwarding 相关测试、ruff 检查和完整测试基线。

### 暂不纳入本轮

- REST/gRPC 控制面。
- Prometheus 指标导出。
- XPUB/XSUB 动态上游订阅代理。
- NATS/Kafka 持久化替代方案。
- 真实交易所 live 测试。

## 验收标准

1. `ruff check bt_api_py/forwarding tests/test_forwarding_schema.py tests/test_forwarding_bus_router_client.py tests/test_forwarding_zmq_transport.py` 通过。
2. forwarding 相关 pytest 通过。
3. `pytest -q` 完整测试基线通过。
4. 新增 health/status API 能在测试中证明：
   - bus 能报告订阅数量、replay topic 数量和 command handler 状态。
   - hub 能报告 active subscription refcounts。
   - router 能报告 adapter 健康状态、幂等缓存数量、state store 启用状态。
   - ZMQ runtime 能报告 endpoint 和运行状态。
5. ZMQ command handler 异常时返回的 ack 保留原始命令标识，便于调用方关联失败请求。

## 实施结果

本轮已完成以下开发项：

1. `InMemoryForwardingBus.stats()`：报告 replay 配置、市场/私有订阅数量、replay topic 数量、sequence topic 数量和 command handler 注册状态。
2. `MarketDataHub.stats()`：报告 active subscription refcount，并嵌入 bus 运行统计。
3. `OrderRouter.health()`：报告 adapter 健康状态、幂等 ack 缓存数量、sequence 状态、state store、bus 和风控规则配置。
4. `ForwardingRuntime.health()` / `ZmqForwardingRuntime.health()`：报告 runtime 类型、市场/交易路由健康状态、ZMQ endpoint、线程和 publisher 运行状态。
5. `ZmqCommandServer` 异常 ack：当命令已成功反序列化但 handler 抛错时，ack 保留原始 `command_id`、`idempotency_key`、`account_id` 和 `strategy_id`。
6. forwarding 测试补充覆盖 bus/hub stats、router/runtime health、ZMQ runtime health 和异常 ack 关联信息。

## 验收记录

已在本地完成以下验收：

```bash
ruff check bt_api_py/forwarding tests/test_forwarding_schema.py tests/test_forwarding_bus_router_client.py tests/test_forwarding_zmq_transport.py
# All checks passed!

pytest -q tests/test_forwarding_schema.py tests/test_forwarding_bus_router_client.py tests/test_forwarding_zmq_transport.py
# 17 passed in 2.70s

pytest -q
# 417 passed, 1 warning in 26.30s
```

完整测试中的 1 个 warning 来自 `tests/test_ensemble_model.py::TestRiskEnsembleModel::test_stacking_method` 触发的 scikit-learn / SciPy deprecation warning，和本轮 forwarding 改动无关。
