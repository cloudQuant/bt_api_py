# Iteration10：CTP 精确订单查询与撤单目标

本轮 Execution dev 提交 `9410b4772df7e7a7b3a3c73a9247137b9a55572e`（父 `e8800924d5312cc57051a3bf10ac5fce778ff751`）包含 Store、类型导出与测试三文件。SDK 本轮父提交为 `31e118f138c1604d196edf1ddac6c0eac5af1a56`，新增查询 evaluator/组合测试并更新子包 gitlink。所有修改直接在既有 dev；无新开发 worktree，其他在途修改保持。

`CtpOrderTargetQueryEvaluator` 借用已有 ACTIVE owner，通过真实 TraderClient 查询、callback ingress 与持久回读签发精确撤单目标。过滤条件和返回行的 Broker/Investor、合约、交易所、OrderSysID、OrderRef、交易日与会话均须一致；当前 query readback 的来源、完整性、时效及回调窗口再次验证。查询返回后先有界应用审计回调，再验证结果。错误、迟到、重复目标或无来源副本均不签发证明。

Store 保留永久 economic-query 标记。只有同 Store 签发、绑定当前 owner/source/writer/session、连续且已应用查询窗口的短时证明能让对应 CANCEL 通过；SUBMIT 继续被阻止。证明只能绑定一个撤单命令，重启后需新查询。fence/window 有容量上限与失效清理；失败查询 fence 保留至 TTL，不授权自动重试。未改数据库 schema。

Root 固定源码检查累计 **14 个不同节点通过**：R1 为 2 passed/12 failed，R2 为 9 passed/3 failed，R3 为 1 passed/2 failed，R4 最后两项 passed、exit 0。每轮只补失败节点，未为交付重跑已通过项。R1 修复新代码读取不存在 owner 字段及未建立已发送订单的 fixture；R2 后修复重放 RequestID 碰撞和测试单调时钟回退；R3 后将重放断言改为既有 Store 的精确 IntentConflictError/原因。生产最终为 R2 字节，此后只改测试。

四轮源码与四仓库 QA 时 HEAD 均不变，固定输入数为 421/422/422/422；九个模块来源正确，列明的进程/网络拒绝事件为空，stderr 为空。R1 有 112 个未登记 unit marker 警告与一条预期 CTP Python fallback warning；R2 注册既有 marker 后，各轮仅一条 fallback warning。独立 reviewer 对最终源码及后续测试小改动均 GO_STATIC。Execution 提交 blob/checkout 匹配接受字节，index 为空，其他 tracked diff 保持。

证据级别：`LOCAL_REAL_TRADER_QUERY_FAKE_NATIVE_AND_STORE_CANCEL_FENCE_ONLY`。SDK 检查使用真实 Facade、BasicGate、owner、TraderClient、callback ingress/Store，native API 为 fake；Store 合同检查使用显式可信 fake verifier/authority，并完成 CANCEL claim/native binding 的单次消费。它不证明真实 SimNow 查询、CANCEL Risk claim/facade 交接、native ReqOrderAction 或交易所撤单 ACK。

正式 **62/68** 不变。下一处代码缺口是 CANCEL facade 向 CTP authority 传递已核验的 Risk claim；仍需首次 OrderRef cutover、会话生命周期与服务装配。真实 VENUE01/02、LOAD01 内存门槛及用户暂缓的双机条件保持。

原始 [Root QA ZIP](evidence/iteration10-ctp-order-query-target-20261018/root-qa.zip)：1068142 bytes，SHA-256 `8914ca8a809ab27b0e7d6311049fa59619e5ad52b8f60072750a56ddfe6e1cd2`；89 个成员逐字节回读匹配。包含四轮原始结果、输入 pins、选定候选、导出 recipe、审阅与子包提交核对；不是含所有依赖的独立运行环境。
