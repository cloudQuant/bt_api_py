# Iteration10 cancel readback and Risk reconciliation component checks

本轮聚焦的行为是：撤单先返回 ACK/UNKNOWN 等未决结果后，后续读取到原撤单的终态时，Risk claim 仅解析一次，系统不重新派发撤单；重复请求只读并返回已持久化结果。R3 的 4 个 GTC 用例通过了这条路径，feed 和撤单观察为合成输入。

R1 的 3 项检查单独验证生产 CTP 撤单回调读取器，只证明回调读回，不证明 Risk claim 已解锁。R2 的 4 项检查曾全部失败：Root 定位为共享 fixture actor mismatch，另记录 1 次 `provider_import` denial；原始结果和候选字节作为失败历史保留，不计通过。两轮分别覆盖不同组件行为。

R3 在 5.54s 内通过，校验 434 个固定输入、8 个精确模块来源且无 denial。它走真实 ExecutionFacade、RiskEntry、RiskGate 和 RiskJournal 路径，但不经过 CTP 回调或 native CTP 链。R1 与 R3 合计 7 项本地组件检查，不增加正式通过数，正式状态保持 **62/68**。Execution 依赖提交为 `658b4c73fe9cf6a8364dae0e13dd4b0dfde5ab7c`，SDK 基线为 `9d4efaf81afd6c70db751d97bf94a6b8a75fab44`。

账户使用确认、DAY→Risk 缺口说明、R1/R2/R3 原始结果及候选、各轮固定源码 spec/recipe、Root adjudication 和最终源码均保存在 [root-qa.zip](evidence/iteration10-ctp-late-cancel-reconciliation-20261018/root-qa.zip)。压缩包 453685 bytes，SHA-256 `d86b05f637d4b15bb58f4722bad3096d8a8891a046afbee62e10b15436155f54`，共 80 个成员并逐字节校验。

DAY 合约从 CTP 到 Risk 的撤单证明仍是明确业务缺口；借用会话的服务组合与生命周期、账户切换及真实 provider 验收也未完成。用户确认可在指定后续测试中使用现有 SimNow 7×24 账户；该确认不证明历史上未使用、没有既有订单/持仓或 retained host 已终止。未重跑已接受 QA；正式通过数不变。
