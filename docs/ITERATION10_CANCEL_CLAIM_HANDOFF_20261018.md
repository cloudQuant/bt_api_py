# Iteration10：撤单 Risk claim 交接

Execution dev `debd1f2fccfb10f40427cdc70d65b772837b7740`（父 `9410b4772df7e7a7b3a3c73a9247137b9a55572e`）交付两个文件；SDK 本轮父提交为 `b5ef558e68be4a5e7107d08813548ebd31f2d040`。修改均在既有 dev，无新 worktree；仅交付本轮通过检查的范围。

ManagedCancellationFacade 优先调用可选的 `cancel_with_claim(intent, receipt)`，传递已核验的同一 receipt 对象。缺少 receipt 时记录 UNKNOWN，不调用新接口或回退到旧接口，不自动重试。没有新接口的 dispatcher 保留既有 cancel/callable 路径，claim 后 guard、writer lease 与不确定结果处理保持。

RiskExecutionCancelAdmissionGate 新增只读 `verify_dispatch_claim_receipt`：重建精确 RiskIntent，验证 receipt 的 permit、scope、cancel ID 与 fingerprint，再由实际 DurableRiskGate 重读持久 claim 和活动冻结锁。它不再次 claim、不释放或修改冻结，也不独立授权 native 调用；CTP 短时授权、会话与最终 Store fence 仍须满足。

Root 固定源码 QA 一次 **17 passed / 6.72 秒**，无警告，stderr 为空。341 个输入与四仓库 HEAD 保持，六个模块 origin 正确，列明的进程/网络/CTP import 拒绝事件为空。SDK 测试组合真实 SQLite Store、RiskGate、journal 与 facade，provider 回报为合成叶节点；Execution 合同测试使用显式 fake admission。验证精确交接/重放、只读回查、替换或缺失 claim 拒绝、claim 后 guard、无 claim 不回退和 legacy 兼容。released 负例是已释放但从未 claim 的 permit，不是已完成 claim 的终态解除证明。独立审查 GO_STATIC。

导出准备阶段发现旧 expiry 测试尚未提交，故将其排除，未启动 QA；实际 QA 只使用两份本轮测试和已提交的 provider-failure 兼容节点。Execution 提交时 cancellation.py 经 Git 正常 CRLF→LF 转换；Root 已核对唯一精确换行差异、AST/编译代码相等、已验收 checkout 字节、两文件提交范围及无关改动保持。未重跑已通过 QA。

证据级别 `LOCAL_REAL_RISK_CLAIM_HANDOFF_AND_READBACK_WITH_SYNTHETIC_PROVIDER_ONLY`。正式 **62/68** 不变；下一步将此 claim 接入 CTP authority/owner 的 CANCEL 路径，再验证实际派发与回调。真实 VENUE、LOAD01 内存及双机条件仍待满足。

原始 [Root QA ZIP](evidence/iteration10-cancel-claim-handoff-20261018/root-qa.zip)：103296 bytes，SHA-256 `33e158882a825f976cda320a5e5a1b4f653b887063db435db1cb1a98446ff4b9`，24 个成员逐字节回读匹配。包含原始结果、选定源码和导出 recipe；不是完整依赖环境。
