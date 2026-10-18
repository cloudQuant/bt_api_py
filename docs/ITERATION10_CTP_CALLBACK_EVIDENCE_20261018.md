# Iteration10：生产 CTP 回调来源校验

本轮在既有 dev 完成。SDK 父提交 `2775ab02705f7b7519bad8c2cfe8bbca651f9755`；Execution 已提交 `5827ea771f159ca7c69a1306b562ac057005a444`，父提交 `17ed96c862a5843abf79cd04361c5b3471ddbfbc`。子包仅修改 Store，SDK 交付新 verifier/factory、集成测试、gitlink、本记录、证据包与两份进度记录。无新 worktree，其他修改保留。

生产 verifier 绑定当前 TraderClient、Store 发出的 owner/adapter、活动会话、原生 API/SPI 来源以及同一 Store 中下一条未处理的持久入站记录。它用实际 mapper 重建完整回调/成交 payload，规范化比较后签发短时效证据；Store 保留事务内的会话、租约、来源、游标和命令复核。伪造字段、等值但非原始的事件对象、已消费事件和变化的 Trader 来源被拒绝。

成功的 `OnRspOrderInsert` 现在只留下请求响应账本和已处理游标，不推进 provider 订单状态；后续 `OnRtnOrder` 才能确认订单。撤单等待或拒绝状态与订单本身的状态分开处理；订单仍排队时不会误判为已撤。CANCEL 的 native OrderRef 使用精确目标引用，并继续与持久原生请求匹配。

Root 聚焦验收累计 **7 个不同用例通过**。R1 为 **6 passed / 1 failed，4.90 秒**；唯一失败是负例读取 mapper 未输出的 LimitPrice，在 verifier 前抛出 KeyError。R2 仅把该参数改为已有 InstrumentID，**1 passed / 2.30 秒**；六个成功用例没有重跑。两轮各 433 个输入及五仓库 HEAD 保持，8 个模块来源核验通过，所列拒绝事件为空，stderr 为空；各一条预期 CTP Python fallback 警告。最终生产源和测试增量独立静态审查 GO。

证据级别为 `LOCAL_PRODUCTION_CTP_INGRESS_VERIFIER_WITH_SYNTHETIC_NATIVE_ONLY`。正向路径使用实际 Python TraderClient、回调 sink、Store、生产 verifier、Risk/facade/managed port，原生 API 为模拟叶节点。覆盖成交累计/去重、来源拒绝、请求 ACK 与订单 ACK 区分，以及撤单请求 ACK/拒绝；不证明订单终态撤销、Risk claim 终态解锁、真实账户或 ServiceHost。下一必要代码为单一 TraderClient 的 account-owner 生命周期工厂与已有服务入口装配。

正式 **62/68** 不变；仍待 ARB05 双机部分、DIST01、DIST02、VENUE01、VENUE02、LOAD01。双机按用户要求暂缓；LOAD01 按已确认工程稳定性门槛记录 p50/p95/p99，不作 HFT 结论。内存门槛与旧 SimNow retained-session 限制保持。

[Root QA ZIP](evidence/iteration10-ctp-callback-evidence-20261018/root-qa.zip)：743250 bytes，SHA-256 `f2653755008f0883048ad57940026b21ba1d1208762a135242573f6d14640e29`，54 个成员逐字节回读匹配。包括原始失败/成功结果、有限诊断、选定源码和导出 recipe；不包含完整依赖环境。
