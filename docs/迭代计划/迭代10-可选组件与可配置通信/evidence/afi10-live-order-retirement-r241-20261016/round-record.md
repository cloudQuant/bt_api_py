# R241：AFI10 live raw-ZMQ 下单路径退役证据归档

日期：2026-10-16
仓库：D:\bt_api_py / dev
AFI 源码仓库：D:\source_code\ai-for-investor / dev
证据目录：evidence/afi10-live-order-retirement-r241-20261016/
证据包范围：仅本 SDK 仓库的三个证据文件；未修改正式进度文档。

## 已提交 AFI 候选范围

AFI HEAD 00cf38fd1dbcd3bd860972bda7f2a4be6f83f138（父提交 ac52830cd66c8ca79547db5be56a971906ccef4c）。Root 已将最终 fixed-source R3 五路径候选提交到 AFI dev；其验收收据位于 raw archive 的 root/afi10-retirement-postcommit.json。本轮不提交 AFI 代码，不重跑已执行 QA。

候选五个精确路径：

- src/backend/app/services/direct_order_service.py — 20978 bytes，SHA-256 de64970d27c7a286955d253849fa7fa7ae79dd041e2913bff1f7c3b3b254a2ee，Git blob eb165db679ea69870ae59f6a6ef309027813339e。
- src/backend/app/services/ai_trading_service.py — 35270 bytes，SHA-256 36787e14ec6ebaae4860d6a800ea3431df57fd9035831427c41c0e031d20cc98，Git blob 2bca09f620263228cd4ba2a081d454f765b988c7。
- src/backend/tests/test_direct_order_service.py — 21886 bytes，SHA-256 50b4ebef8512fcff4b3adf35f206cd0cebb5b364a874c451b54dd08ede8c08e4，Git blob f014e0a074426c6772f66862c342059d2017560b。
- src/backend/tests/test_ai_trading.py — 52562 bytes，SHA-256 d0c568c5a93336b67e8dfd4eb8bd95e132fcd407844d0b0e80c4dc7975908f28，Git blob 7569a40fb037b18b246a4cb62e5dc32a61ed3738。
- src/backend/tests/test_afi10_live_order_retirement.py — 2285 bytes，SHA-256 1faed1f952801a5208cb7d4a9eb0e6b2800e9c6fc8851cece7e18ee102d97950，Git blob b98c179dac52a49b4e9f3ca4f518a499d8f7028f。

Root receipt 记录 AFI index 为空，五个已选 checkout 文件提交前后字节相同；这些 checkout bytes 是保留的混合所有者 paper WIP，不等同于整个文件候选。SDK 本轮只归档已固定候选与验收证据。

## 作者固定源验收

原始固定源 runner 输出按字节收录在 raw-evidence.zip（26 个归档成员）：

- 全量 suite：R2 production candidate，31/31 passed、0 failures/errors/skips，pytest exit 0；结果 SHA-256 47f21995758bfe799047a41ee10d9204fba328dfccb845416aecc50e0477d86f。它覆盖九个 live-retirement checks、18 个 paper service checks、4 个 map-order-type checks。R3 的 production bytes 与该候选相同。
- R3-only test delta：仅运行从 AFI HEAD 恢复的两个 order-size boundary test，参数展开 6/6 passed、0 failures/errors/skips，pytest exit 0；结果 SHA-256 e4422d08652784dc11b656a824b9f7f6e3d85373d5c1bf7c8b98c30c90f3e6e0。31 节点 suite 未在 R3 重跑。
- 两轮固定源输入记录均报告 source pins 与 repo HEAD 前后不变；R4 supplemental spec 报告 2,804 source pins、五个 HEAD 不变。

## 独立静态复核

raw-evidence.zip 的 reference/independent-static-review-reference.md 记录 reviewer /root/resume_pass_audit_reg_risk_cfg 的 GO_STATIC 结论与审查范围：纸面 helper closure 和两类 paper 测试保持 HEAD 语义，删除 live helpers 后恢复被纸面路径调用的 helper 闭包。该文件是本线程审阅结论引用，不是 reviewer 单独签发的原始 review 文件。最终 R3 在已审 R2 production bytes 上只增加两项 HEAD 测试。

## 证据边界

这是作者执行的固定源码/合成测试和静态复核证据，不是 Root 执行 QA，也不是真实 Host、provider、账户、service-v2 安装或正式 AFI10 PASS。此前 async 插件未加载导致的失败输出保留在原始临时目录，但不属于本包的接受证据。manager 和 quote 的旧 Gateway 消费者仍是后续独立迁移范围。

SDK 仓库本次无代码路径改动；创建证据包前 SDK HEAD 3b58addb5d9511d53977c3cc83cf031591ca1302。父仓提交状态 PENDING_ROOT_REVIEW_AND_SCOPED_COMMIT；由 Root 决定三文件提交。
