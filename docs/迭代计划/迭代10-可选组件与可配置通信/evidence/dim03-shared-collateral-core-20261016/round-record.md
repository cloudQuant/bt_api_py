# 第217轮：共享抵押品域 Risk 核心

已提交 Risk dev `71be56e29f867a5a5aaac60331f9eca46c7e09f0`（parent `dc30c1c118cf587538150fc5016c8956e30c60a9`），仅 core/admission.py、新测试及包内轮次记录三路径。核心按共享域聚合跨 AccountScope/产品资源的 ACTIVE、SETTLED、UNKNOWN 占用，同一 reservation 不双计；资源归属与 revision 固定，未完整声明或 ledger 身份不符时拒绝新增额度。REDUCE/CANCEL 和终态释放保留。

Root R1 六项通过、一项因测试多传 API 不支持的 notional_unit 参数失败（2.46s）；仅删除该参数，R2 只运行此前失败的 UNKNOWN 节点并通过（1.05s）。七个唯一用例分两次通过，不宣称一次七项全绿。两次各有一条既有 pytest 配置 warning、stderr 空，147 pins 及 Risk/base HEAD 前后不变。独立静态增量复核 GO，原失败与两轮完整输出均归档。

范围为本地 SQLite core，竞争测试是同进程两个 Gate 实例/线程。ledger_identity_ref 仅是本地账本一致性引用；可信 composer 及部署层唯一 owner 尚未接线，DIM03 不新增正式 PASS，累计仍39/68。没有真实账户调用，没有新开发 worktree，未重复其余六项已通过测试。独立 analytics WIP 保留。

父仓现有 dev parent `f29db99b3bcee887b75603c5f3f1bce3692023e8`，本轮仅更新 Risk gitlink、三份证据和进度。实际父提交见 Git 与 `.codex-tmp/iteration10-r217-postcommit.json`。
