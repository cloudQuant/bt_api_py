# Iteration10：CTP 受管撤单派发与回调读取

SDK 本轮父提交 `a757360cc03256160b034ed24571094bdd782f0c`；Execution dev 已提交 `17ed96c862a5843abf79cd04361c5b3471ddbfbc`，父提交 `debd1f2fccfb10f40427cdc70d65b772837b7740`。均在既有 dev 修改，无新 worktree。子包两文件先提交，SDK 更新该 gitlink、五个代码/测试文件及本记录、证据包和两份进度文档；无关修改保持。

受管撤单链现在把实际 Risk claim、同一 Store 的目标查询证明、CANCEL command、会话和一次性 native lease 接起来。Store 分配 ActionRef；RequestID 在客户端字段校验前明确绑定。成功的本地 native 返回只证明派发，缺少回调仍是 UNKNOWN，重放不再次查询或发送。原生调用抛出不确定异常时，保留 CLAIMED/inflight 与 Risk claim，owner 被隔离，后续操作拒绝；没有把这个状态伪装成已持久化的 UNKNOWN。

只读结果通过 Store 同一事务重读原 SUBMIT 与 CANCEL 的会话、请求、callback ledger、ingress/application 和投影。指定 CANCEL→SUBMIT 的精确 scope、交易日和 EXACT postcondition 可以被本次只读查询查看；不删除、不解决该记录。默认查询、其他命令、UNKNOWN、生命周期及 POISONED owner 的限制保持。Action ACK 不代表原订单已取消。

Root 聚焦验收累计 **8 个不同检查通过**：R1 已通过的两个拒绝路径与 SUBMIT 兼容节点不重复；R7 的剩余 3 个 SDK 用例和 2 个受影响 Store 用例 **5 passed / 5.09 秒**。R7 的 439 个输入和五仓库 HEAD 保持，17 个模块来源正确，列明的进程/网络拒绝事件为空，stderr 为空。一个预期 RuntimeWarning 来自主动排除 CTP C++ 扩展并使用 Python source fallback。独立静态复核 GO_STATIC，审查者未运行 QA。

失败记录完整保留：R1–R3 暴露 CANCEL 主 OrderRef 与 RequestID 绑定问题；R4–R5 修正测试 DTO、合成 ErrorID 映射和异常预期，并由同次调用定位只读查询的 ACCOUNT_FENCED 冲突；R6 是 Root 导出漏掉已提交的 `_legacy_execution_schema`，在收集阶段失败，R7 只补该依赖后通过。未重复已通过用例来增加计数。

证据级别为 `LOCAL_CTP_CANCEL_COMPONENT_WITH_SYNTHETIC_NATIVE_AND_CALLBACK_VERIFIER`。Risk、Store、派发和回调处理组件使用实际代码，native API、callback/trade verifier 是合成叶节点。R1–R4 原始 evidence label 未充分表达合成 verifier 边界，原始字节保留，以本裁定为准。没有真实 SimNow、CTP native 或 VENUE 验收结论。生产 callback/trade verifier 及其实际装配仍待完成。

正式 **62/68** 不变。剩余为 ARB05 双机部分、DIST01、DIST02、VENUE01、VENUE02、LOAD01；双机按用户要求暂缓。LOAD01 按已确认的工程稳定性门槛验收，记录 p50/p95/p99，不作为 HFT 结论；当前内存和旧 SimNow retained-host 条件仍未解除。

[Root QA ZIP](evidence/iteration10-ctp-managed-cancel-port-20261018/root-qa.zip)：993035 bytes，SHA-256 `d01101a9de8193dd1ed768fd1c10080d137286f7a5680254cac13fdd8e3fe5bb`，152 个成员逐字节回读匹配。保留 R1–R7 原始结果、有限诊断、选定源代码与导出 recipe，不是完整依赖环境。
