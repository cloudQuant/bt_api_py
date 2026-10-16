# R230：DIM04 本机自成交 owner-path 场景

本轮仅记录一个已消费的 Root 节点：tests/integration/test_dim04_strategy_account_isolation.py 在固定 R6 源码导出中通过，Root 实际使用的规范 SHA-256 为 359c1e49d6d123203e2fc83262f9ddf12ee71de0b34c38001509d06e82884c30。结果为 1 passed、10.41 秒、无 warnings；362 个源码 pin 前后未变，SDK 及五个子包 HEAD 前后相同。

测试覆盖同一真实 SDK ServiceHost 和 SQLite Risk/journal owner 下，两策略在共享账户上发出相反 BTC 开仓意图，均被 SELF_TRADE_CONFLICT 拒绝；查询读到对应 REJECTED 记录且零 dispatch。另一个策略的 ETH 开仓正常成交一次。测试随后检查 provider 写入计数为一、预置的策略 A BTC close permit 仍为 ACTIVE 且归属/版本未变、两策略可平量 snapshot 未变化，并要求两个客户端及 Host 正常关闭、无强制清理。Provider 是本地 synthetic fixture；这只证明冻结源码中的 SDK owner 路径，不证明交易所/真实账户事实或正式 DIM04 PASS。

R4 和 R5 均保留原始失败输出。R4 的 fixture 把拒绝意图的查询误判为成功；实际合同返回 ADMISSION_DENIED，同时 found=true、记录 REJECTED、dispatch_attempts=0。R5 修正了该查询判断，两个 BTC 拒绝/查询、ETH FILLED 单次 dispatch、两客户端正常关闭均通过，之后测试用未定义的 _dim01 变量调用 Host provider_write_count 而失败；Host 计数/Host 关闭和最终持久状态断言当时未运行。两次输出均记录了捕获到的 Proactor RuntimeWarning，stderr 文件为空。R6 修复变量名后单节点完整通过。R4/R5 是测试夹具缺陷，不是 production acceptance；R6 不会抹去这些已消费失败记录。

正式计划原文（正式迭代计划.md:776）要求“多策略同时平同一合约，净/双向/今昨仓、晚到成交、自成交冲突、策略级批量撤单”，验收是“真实可平量预留与归属对账；不平他人仓、不扩大账户控制权限”。本轮只覆盖其中的本机自成交冲突与 owner 保留断言，不能将其登记为整个 DIM04 完成或增加正式计数。

最短后续实现边界：

1. 真实 SDK CLOSE：当前公开 client 的订单意图已接受 CLOSE、CLOSE_TODAY、CLOSE_YESTERDAY，但 service_risk_order_entry.py 仍只允许 OPEN、将 AdmissionOrder 固定成 OPEN，且 risk-only provider contract 仅声明 OPEN。需要由可信账户 owner 为 (account scope, instrument, strategy) 安装当前可平量声明，将 authenticated strategy scope 派生为 IntentAction.REDUCE 并绑定数量/单位/仓位效果，再沿现有 Risk admission、provider contract、journal 和 fill observation 路径完成一次自身仓位减仓。无可信 position facts 或不支持的双向/今昨映射时必须 fail closed，不能用 synthetic 声明伪装真实可平量。
2. 策略级批量撤单：Risk 的 release_strategy_unclaimed 只释放未 dispatch 的该策略 reservation，不会取消 provider 订单；现有 orders.cancel client 已存在，但 RiskOrderEntryService 当前将其返回 CAPABILITY_UNAVAILABLE，能力投影也固定 false。需要让账户 owner 从 authenticated WireScope 选取本策略未终结订单，为每个子单持久化 cancel ID/结果并走既有单笔 provider cancel；逐条记录部分成功/失败/UNKNOWN，UNKNOWN 只按原业务 ID 查询且保留占用，绝不盲目重发或释放。账户级 cancel-all 必须继续由单独权限控制。无需新增通用 group framework；现有 service.group_reservations 是 reserve/query/release 端口，不能替代 provider 撤单。
3. 范围边界：service_risk_order_entry.py 是真实 CLOSE 与单笔 CANCEL 的共同执行边界，service_risk_order_journal.py 需要扩展持久 cancel attempt/查询记录；它们与共享 DIM03 order-entry 路径重叠。service_execution.py 已提供单笔 cancel() 客户端接口。service_runtime.py、service_host.py、service_execution_group.py、service_group_reservations.py 都是共享注册/组组件，不应作为 DIM04 独占路径顺手改动。当前可独占授权的代码文件只有一个新增的 DIM04 专用集成测试；新增 cancel helper/service 本身可独占，但不能独立闭合生产路径，必须等共享 order-entry 文件锁释放后再一起接线。

本证据包不改进度表或正式计数、不提交代码；单文件候选提交清单放在 .codex-tmp/iteration10-dim04-r230-single-test-commit-inventory-20261016.json。当前证据等级为 LOCAL_SYNTHETIC_PROVIDER_SERVICEHOST_DIM04_SELFTRADE_OWNER_PATH，正式 DIM04 仍未完成。

- Raw archive：raw-evidence.zip，245915 bytes，SHA-256 2a535548a586623c2896db2d4979419087505831d0b1b3ee3e318ca315cd5afb。
- Manifest：manifest.json，17439 bytes，SHA-256 20da5d8e67a020a66e9e05fa1ba6291d42a69c320cca46f371a541a1b6834b10。R6 的派生汇总字段已按原始 JUnit 更正为 1 test / 1 passed / 0 failures / 10.41s；原始结果、JUnit、ZIP 和源码均未改动，旧 manifest 保存在 `.codex-tmp/iteration10-r230-manifest-pre-counter-correction.json`。
