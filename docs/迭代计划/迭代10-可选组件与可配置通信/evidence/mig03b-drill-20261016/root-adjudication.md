# 第188轮根裁定：MIG03 incr-B 受控迁移演练测试（增量归档级）

- 裁定：GO-with-disclosures（增量级）。范围 = 新测试 `tests/unit/test_service_migration_drill.py` + 本证据目录 + 进度/汇总文档；生产零改动（pins before==after）；**不改 PASS 状态**（MIG03 仍部分，见下）。
- 运行事实：run-03-final 2 passed / 15.003s / pytest exit 0、stderr 空、两 observation JSON 均 `status: PASS`；回归 6 套 68 tests（migration-gate 12、reg03 1、host-integration 1、execution-integration 1、host 32、registry 21）全 0F/0E/0S 且每套 exit:0；ruff "All checks passed!" exit 0。开发期 run-00（fixture `CLIENT_ALIAS_MOUNT_MISMATCH`×2）、run-01（恰一次真实语义修正）、run-02（2 passed）原件保留于 `D:/temp/it10mig03b-20261016-r1/`。
- 测试内容（2 节点，sha256 `f5cb66102575abe22217d6caba7ea952552fafe6875c9caa7f0e000af2c91d8f`，87816 B）：节点一 = 真实 spawn 进程/ZMQ-CURVE/SQLite 两 epoch 受控迁移演练——旧 epoch 写入 `STALE_EPOCH` 拒绝与重登记、跨进程真实锁的 `ORDER_OWNER_ALREADY_ACTIVE` 双 owner 拒绝、UNKNOWN 事实跨 epoch 保持不删除、迁移后订单查询/结算/派发/ACK 回读、封存（seal）后 `REVERSE_MIGRATION_REFUSED` 拒绝回旧版、改名 wire scope 依 REG04 转记义务被拒且无第二订单分区/无重复额度；节点二 = 回退窗口与导入失败负例（`CUTOVER_MARKER_CONFLICT`、`INTENT_CONFLICT`、封存前回退、旧部署重启）。
- 关键语义发现（run-01，保留如实）：迁移后 in-flight 冻结项在首笔新 epoch 写入时被拒（`admission_denied`、attempts 0、零 risk 行），quota 守恒（increase_count 1、预留/冻结不动）、provider 计数 {1,0} 不重派——属生产 durable gate 语义而非 fixture 缺陷。
- 独立复核（review-r1/review.md，本目录 sha256 清单收录）：GO-with-disclosures，零证伪矛盾；全部 2162 行通读、哈希全量复算一致、断言可溯源、无 skip/xfail/monkeypatch、生产零改动。非阻塞披露：(1) 无 result.json/stderr.txt 落盘（sha 仅在 00-INDEX.md）；(2) run-01 无 observation JSON、其守恒细节由 run-03 再证；(3) workspace 既有脏文件（load01 行尾、杂散目录）不入本提交。
- 条款映射（正式迭代计划.md:784 + §11.3 :854）：受控迁移与调用计数、双 owner 拒绝、不删除 UNKNOWN、首写后拒绝回旧版、改名不产生第二订单分区/重复额度 → 本测试闭合；MIG03 持久映射构建的其余审计义务（§11.3 行）留给翻转审计轮，**MIG03 仍部分**，正式仍 30/68。
- vehicle/证据等级：合成 provider + 真实本机进程/SQLite/ZMQ-CURVE 会话；无真实账户、无跨机；不宣称服务栈外证据。
