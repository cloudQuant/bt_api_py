# R237：DIM03 Risk 多维预留与可信 Greeks 核心验收

本轮冻结 Risk 的 DIM03 候选：在 DurableRiskGate 的真实 SQLite 预留事务中持久化集中度/暴露维度，并使用来源绑定的 Greeks 事实验证缺失、过期和不匹配输入；新增旧 reservation 缺少维度记录时 fail-closed 的兼容回归。源码来自固定 R3 导出，按 Risk ccac8818312c1474077c00bfe9d8f12521af39c9 及显式 DIM03 overlay 形成，不含预算策略绑定或 DIM04 CLOSE WIP。

Root 已接受作者本机 QA：D:/temp\it10dim03-risk-dimensions-20261016-r3，6 passed / 0 failed / 0 skipped，pytest exit 0，stderr 空，2.61 秒。142 个输入 source pins 与 SDK、Risk Base、Risk 三个 HEAD 在执行前后均匹配。 默认无额度项兼容的静态复核为 GO；Root 对最终测试 diff 的既有审阅未重跑。原始命令、runner、spec、source before/after、JUnit、stdout、stderr 与 result 保存在 raw-evidence.zip。R1/R2 失败输出仍在原目录，只作为保留历史；没有覆盖或重跑。

精确 partial-index 候选由 .codex-tmp/dim03-r237-dimensions-risk-source.patch 表示；对 Risk 当前空 index 执行的 git apply --cached --check 退出 0。本准备未 apply、未 stage、未 commit。Risk 子仓库提交后，SDK 父仓库应只更新 Risk gitlink 并纳入此轮三件证据包；progress 文件由 Root 按正式裁定单独处理。本记录末尾留待追加实际 Risk/SDK commit 与 postcommit 收据。

本轮证据级别为 DIM03_RISK_CORE_CONCENTRATION_AND_SOURCE_BOUND_GREEKS_SYNTHETIC_SQLITE_ONLY。本地合成 SQLite 测试不证明外部 provider、真实账户、过滤快照完整性、当前持仓重估或 DIM04 关闭后的维度释放；DIM03 完整正式条款不在此包内宣称通过，正式 tracker 未改。

Archive raw-evidence.zip: 161811 bytes / 65e7236b2d1773d9e113873c4e3af6882d797c3324419ed3ef1fda4ea6e3bc03。  
Manifest manifest.json: 10593 bytes / `90140f3f50c45a6f5a9a4bf64cc9cc70c51f6b9d92fc13a5b1029dbbeb44c701`。
