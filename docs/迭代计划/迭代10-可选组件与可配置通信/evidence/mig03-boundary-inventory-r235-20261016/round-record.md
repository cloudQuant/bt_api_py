# MIG03 R235 边界清点与状态守恒组件交付准备

本包在 `D:/bt_api_py` 现有 `dev` 与 AFI 现有 `dev` 候选上准备，尚未 stage 或 commit。交付仅包括 SDK 的 MIG03 边界模块及其 unit/integration 测试、AFI 的只读边界模块及测试，以及本目录的原始Root QA归档、manifest和本记录。当前SDK HEAD为 `53f33158429f89fd9b4dce83c068056c99d6778f`，AFI HEAD为 `ac52830cd66c8ca79547db5be56a971906ccef4c`；仓库中其他并行改动不属于本轮。

R8在冻结最终候选上只执行 `test_mig03_full_boundary_inventory_handoffs_same_paths_and_conserves_state`：1 passed，12.85s，exit 0，无pytest warning/stderr；2337个来源pin前后相同，六个仓库HEAD前后相同。原始输出及R2–R7历史均保存在 `raw-evidence.zip`。历史运行是顺序修复，不是一次全套通过：R2为9P/6F，R3为4P/2F，R4 nodepath修正前0收集/exit 4，R5为2P/2F，R6为1P/1F，R7为0P/1F，R8为1P/0F。各轮原stdout、stderr、JUnit、exit、spec、runner与前后pin清单未改写；R4未收集的结果不算通过，失败轮保持原样。

可复用的通过节点按其原运行分别保留：R2七个SDK unit/AFI unit节点与两个AFI节点；R3 SQLite只读边界节点和三个AFI拒绝节点；R5两个不可变JSON/typed numeric节点；R6 AFI operation/approval/fill/fee/position正例；R8 SDK两epoch完整集成。它们不是一个合并后的同次QA。本包不重跑任何已接受节点。

本轮固定八类参与者：registry/session/resource pool；execution订单、成交、成本、人工book及UNKNOWN；Risk预留、预算、冻结及政策；group腿/期限/预留；Gateway outbox/checkpoint；全部consumer；AFI operation/approval；AFI paper ledger。SDK四个SQLite参与者采用受限只读schema/row projection；AFI快照通过固定账号/实例关联读取业务操作、审批事件、paper订单、成交、费用、持仓与权益快照。缺少参与者、缺失schema/身份或不可读状态在arm前拒绝。实际集成使用本机spawn Host、CURVE SDK Runtime、SQLite投影及source-bound map importer；停旧Host和consumer后先捕获迁移前状态，离线注册并readback五类map关联，arm同一路径状态，然后在新epoch恢复，复核已完成订单/原provider ID、UNKNOWN/原reservation与freeze、业务金额/持仓、游标和stream epoch。旧epoch在新写attempt后被拒绝，provider synthetic dispatch不重复。

形式条款审计：§11.3步骤1/4/6及MIG03矩阵中的受控迁移、身份映射、账本核对与首次新写后旧epoch拒绝，均有脱敏合成部署断言；步骤2/3以旧SDK client/projection/scheduler停用、旧Host正常退出和服务端旧epoch拒绝提供本机证据；步骤5覆盖新epoch启动、客户端重登记、同epoch cursor恢复与读取。测试没有单独挑战配置变更封窗，也没有在首个新epoch写attempt前先执行一套只读投影/政策核验并显式开启写入的流程：首个新submit触发`dispatch` write-attempt seal，随后依赖历史终态去重保持provider仅派发一次。原地保留同路径SQLite符合合同，不要求copyDB或跨库原子事务。限制：groups通过实际配置检查记为NOT_APPLICABLE，本轮不迁真实group ledger；consumer名单穷尽此合成部署，不代表生产全租户发现；`fence_reference`仅是标签，本机旧Host退出与旧epoch拒绝不是外部provider fence；无生产账户、私有数据库或真实provider操作。本包不替代MIG04发行安装矩阵，不单独作正式MIG03 PASS裁定；MIG03仍部分、正式PASS计数不变。已有静态review仅检查2316-pin的早期候选，未覆盖R8最终integration pin `80c923e3…`；最终R8字节的独立静态复核待Root安排。

封存candidate source pins和三文件提交scope见 `manifest.json`。Source evidence仅为Root固定来源合成测试与静态输入绑定，不证明真实部署、交易所行为或实际账户迁移。