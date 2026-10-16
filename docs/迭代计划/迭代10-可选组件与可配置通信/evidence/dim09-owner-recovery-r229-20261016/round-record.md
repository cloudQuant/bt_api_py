# DIM09 R229 窄范围交付记录（2026-10-16）

## 本轮验收

仓库 `D:/bt_api_py`，`dev`，Root 实跑前 HEAD `9e4ab501158cbd87730faa964ae487e6136ffc4a`，父提交 `8e86867d`。固定 Root 规格 `.codex-tmp/iteration10-dim09-root-spec-r230head-20261016.json` SHA-256 `c92f6a565bcd1fa07b36dc71ebe7a25c32b04c7ff86018aac3701e571161dcdf`；原始结果来自 `D:/temp/iteration10-dim09-owner-recovery-root-20261016-r229-corrected`，本包逐字节归档其 11 个输出文件。

Root pytest exit 0，2 passed / 30.34 秒、无 warnings；85 个固定源码 pin 与 SDK、Base、Execution、Risk、Gateway、ZMQ 六个 repo HEAD 在运行前后均不变。节点为 `test_guard_reconciles_exact_orphan_hold_through_real_control_api` 和 `test_owner_recovers_through_distinct_query_registration_after_strategy_exit`。证据级别 `SOURCE_FAKE_DIM09_OWNER_RECOVERY_AUTHORITY_REGRESSION_AND_LOCAL_HOST_INTEGRATION_ONLY`；这是合成 provider 与本机 Host/SQLite 的源码验收，不是账户/provider 验收。

## 精确交付范围

本次固定候选导出含 3 个生产文件：`service_execution_group.py`（SHA-256 `90e9f4e8c61efd43af1db033b28bbd186813599b2e88591e1b9b191b5f8b34ff`）、`service_execution_group_authority.py`（`4dfe3357ce1b3a9f423a74d233a4b6d3841f785ef2148b9a6210d63ce15476de`；Git blob `fe14446c30fbe60ad7e4eff11e71e88942deed15`）及 `service_execution_group_proofs.py`（`311cd9050ec3685729d63f8786fd194502bd5c47675299f3ba595566add6bff0`）。另含集成包标记和一个 integration、两个 unit 测试，共 7 个源码/测试路径；Root 原始 QA 输出 11 个路径；本记录、清单和 20 条逐路径 `-text` 规则。总暂存范围 21 paths，清单明列，不包含其他 WIP。

Authority 候选取自 Root R229 固定导出，不取当前混合工作树整文件。候选把 `ExecutionStoreGroupCompletionVerifier` 恢复到已接受 SDK HEAD 版本，排除了未纳入本轮的 CANCELLED 完成校验改动；R229 两个节点正是针对这个精确候选源码运行。当前工作树 Authority SHA-256 `8a62d36eaf3b5207e4dbb8e001fb7d63055e7dc54ba2f8b295ba9a1d474731b3` 保持原样，取消相关 WIP 不纳入本轮暂存。

R1–R12 各代原始规格、已消费输出与源码副本均按原状保留；未执行代仍保持原状态。先前零上下文投影产生的 `13c164419297821c49f3d7adc7af1a2e7ec2a2d595ee0c3becb45e39b47396f1` 不属于 R229 候选或验收；R229 使用完整、可解析的固定导出文件。旧失败 QA 目录与 patch 文件留作历史，不纳入此最小提交。

## DIM09 正式条款映射与裁定

正式计划第 781 行要求三腿 C/P/F 比例/乘数、各阶段零/部分成交、第二/第三腿拒绝或 UNKNOWN、策略退出及协调重启，并核对残差向量与预算守恒；“实际期权成交另计”。第 13.5 节第 1044 行进一步要求：失败/UNKNOWN 时保留已成交腿和剩余预算，依据真实残差向量、可平量及风险预算选择补偿，复用稳定动作 ID；重启不得重发首腿，所有腿及处置义务确认后才能终结。

- R174 已接受 Step-1 三腿合同/协调器测试覆盖比率和乘数、阶段零/部分成交、残差向量、预算守恒及中断拒绝；属协调器层证据。已接受的 13 个节点（根裁定列出的 partial-first-fill、whole-group-reduction、zero-fill、lost-final-reply/original-ID recovery 等）没有一个执行确认的补偿订单；`UNWINDING` 只是合同状态枚举，协调器 `group_coordinator.py:829` 对补偿仅有“decisions”注释。
- R183 已接受的 5 个 Step-2 节点分别是 source-backed PAIRED、第二腿拒绝、第三腿 UNKNOWN+owner/coordinator restart、policy denial、wire whitelist；没有补偿成功生命周期节点。尤其 `whole_group_reduction`/partial fill 测试覆盖的是开仓链分段 sizing，不等于 UNWINDING 的反向补偿订单。R187 已接受生产 v3 三腿预算报价构建器与测试接线，修复 Step-2 当时的报价路径前置缺口。
- R229 补足策略退出后的服务端持续管理：原授权/hold 不变，通过不同但匹配授权边界的查询 registration 只读核对原 provider ID；UNKNOWN 期间 hold 保留，事实完整后经 Execution、proof 与 Risk receipt API 收敛；无重复派发/后续腿派发，正常 Host 关闭。它是恢复生命周期证据，不作为三腿补偿成功的替代。
- **DIM09 仍为 PARTIAL/HOLD，不裁正式 PASS。** 在已接受的 R174/R183/R187/R229 证据中，没有找到一个三腿非零残差场景实际生成并确认补偿订单，同时证明动作 ID 稳定、可平量与预算受限、补偿事实持久化且全部处置义务完成后才终结。R174/183 的残差和预算证据没有该成功补偿生命周期；R229 是原意图恢复，不是补偿订单。下一步最小补证应是一个三腿合成 provider / service integration：用真实部分成交形成残差，按可平量与风险预算生成稳定 action ID 的补偿，验证重启幂等、唯一补偿派发、源回报及 Risk receipt 持久化、残差/预算处置后才终结，并验证 UNKNOWN/拒绝继续保留 hold。实际期权账户成交仍按正式行另计。

未修改正式进度表或 tracker，未把 R229 记作 DIM09 PASS，未重跑已接受节点；本次打包未另启 Host，Root 的 R229 实跑按原 QA 记录完成了集成 Host 正常关闭。