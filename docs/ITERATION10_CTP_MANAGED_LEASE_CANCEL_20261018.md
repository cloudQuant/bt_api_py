# Iteration10：CTP native lease 显式撤单身份

本轮在现有 CTP dev 提交 `dd906d04ecaa64ed8f7f74c4f8d8166a5d0859f8`，父提交 `4d0f564d0dca33d535f50f4454212cd36da33bfb`。父 SDK 本轮基线为 `6c08803378e7b931769444db4edbe7c0a3319f1d`；本轮更新子包 gitlink 和本文证据，保留其他修改。

`submit_order_action_with_lease` 新增两个可选 keyword-only 参数：`runtime_action_id`、`managed_cancel_intent_id`。任一参数出现就验证完整 managed tuple，并将撤单意图精确绑定到 V2 envelope 的 managed action ID；restricted SimNow 必须显式提供。已有凭据、恢复授权和 write-intent verifier 仍在最终 native 调用前验证并消费。OrderActionRef 与 RequestID 独立，native 调用保持在 SDK 锁外；失败不确定性保留 poison 和不可重发行为。非 restricted 的既有省略参数调用兼容。

Root R1 为 3 passed / 9 failed / 1.36 秒：三项受影响旧路径兼容通过，新九项共用 fixture 将初始连接代数预置为 6，违反首次连接需为 0 的既有保护。R2 仅修正 fixture，使启动自然从 0 到 1，合成授权匹配代数 1；旧 helper 默认仍为 7，并加入 setup 失败清理。R2 只补跑九个失败检查，**9 passed / 1.22 秒 / exit 0**，未重复三项通过项。累计 12 个不同检查通过。

两次各 413 个固定输入、四个 QA 时 HEAD 不变；五个指定模块来源正确，列明的 network/process audit 拒绝事件为空，stderr 为空。各有一条预期 CTP Python fallback warning。源码与最终 fixture 已获独立 GO_STATIC。生产与两个测试文件的提交 blob/checkout 精确匹配 Root QA 字节；子包 index 为空。QA 使用现有 runner、明确导出 recipe 与 Python fallback，未加载 CTP binary。

证据级别：`LOCAL_CTP_LEASE_CANCEL_IDENTITY_WITH_SYNTHETIC_AUTHORITY_ONLY`。正向用例执行真实 TraderClient callback ingress、require/consume 和 lease；V2 binding verifier、settlement、credential/approval 与 native API 为合成测试叶节点。本轮不证明真实 Store/facade 撤单组合、真实凭据或交易所取消成功；本地 native 返回 0 不作为撤单 ACK。

正式计数仍为 **62/68**。还需接通新鲜订单查询 evaluator 与查询栅栏、CANCEL facade 的 claim 交接、首次 OrderRef cutover、会话生命周期与服务装配；真实 VENUE01/02、LOAD01 内存门槛和用户暂缓的双机验收条件不变。

原始 [Root QA ZIP](evidence/iteration10-ctp-managed-lease-cancel-20261018/root-qa.zip)：263941 bytes，SHA-256 `ba2ff78ec82f3c8007b5acb5f33a7a63179dd97751791393aaf81439207082cc`，34 个逐字节复核成员。包内含两轮原始输出、具体节点、固定源 recipe、候选/最终代码和子包提交核对；不是包含全部依赖的自包含运行环境。
