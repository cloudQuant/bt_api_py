# Iteration10 Risk DAY-order persistence and reconciliation

本轮修复：普通 LIMIT 委托现在可按 provider 明确声明使用 DAY，开仓和平仓均保留原始 TIF；内部组合补偿/退出仍要求 GTC。修复 V2 PROOF_READY 恢复只识别 V1 字段的问题：按 schema 严格重建并比较完整观察，重开账本后解除同一 Risk claim，不重新下单。

以下记录逐轮列出固定源码 QA 的实际结果；synthetic provider 证据不代表真实柜台或账户验收。

SDK 基线 `d304a05adede44b503ae07f47401c5ee1f5ef965`；正式状态仍为 **62/68**，本轮新增正式通过数为 0。

QA 结果：
- open-r1: exit 1，4/8 passed，4 failed，0 skipped，431 source pins，5 module origins，denials=[]。
- open-r2: exit 1，3/4 passed，1 failed，0 skipped，431 source pins，8 module origins，denials=[]。
- close-r1: exit 1，1/4 passed，3 failed，0 skipped，432 source pins，8 module origins，denials=[]。
- close-r2: exit 1，0/3 passed，3 failed，0 skipped，432 source pins，8 module origins，denials=[]。
- close-r3: exit 0，3/3 passed，0 failed，0 skipped，432 source pins，8 module origins，denials=[]。
- recovery-r1: exit 0，2/2 passed，0 failed，0 skipped，431 source pins，8 module origins，denials=[]。

证据级别：`LOCAL_NORMAL_DAY_ORDER_AND_V2_DURABLE_RECOVERY_WITH_SYNTHETIC_PROVIDER_ONLY`。

限制：
- 13 项是跨修复轮次的不同通过节点：首次拒绝场景 4 项、普通派发 3 项、兼容性 1 项、平仓 3 项、恢复及 V1 时间戳 2 项；不是某一次完整套件运行 13/13。通过项未为交付重复运行。
- 早期失败包括测试未映射 venue、错误读取 RiskOrderCommand/CTP 参数，以及实际 V2 恢复契约缺口；原始失败与当时源码保留。后续恢复生产改动仅限三个恢复函数，不影响已通过派发/平仓路径。
- 真实 BtApi/DirectBackend/FeedAdapter/CTP mapper、Risk Gate/Journal/Entry 在本机组合，provider 响应为合成；不代表 CTP native、SimNow 账户或 ServiceHost 端到端验收。
- 恢复以本机对象重建和故障注入证明，不代表真实操作系统进程崩溃；ACK 为 null 的兼容分支经静态复核，未单独执行。
- 原有 dirty 文件中两个恢复函数的重叠旧改动由本轮严格恢复逻辑替代，原字节留档；其他 WIP 字节保留，局部 index 仅写 HEAD-derived 候选。
- ARB05 双机部分、DIST01/02 按用户要求暂缓；VENUE01/02、LOAD01 待验收。LOAD01 工程稳定性口径与 9.875 GiB 门槛不变，保留现有 SimNow 会话限制。

最终候选源码与 QA 原始输出保存在 [root-qa.zip](evidence/iteration10-risk-day-orders-20261018/root-qa.zip)，1295486 bytes，SHA-256 `78343b1d1c3921cbcd3b2e1bbbb1dcd6149195d26212a844ccb433d08f3d014d`。

此 helper 仅生成文档与证据包，不修改源码、不 stage、不 commit。两个原有 dirty 文件的 HEAD 与 checkout 前像均留档；由 Root 独立按选定 postimage 做局部 index 更新。
