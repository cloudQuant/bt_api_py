# DIM02 / ARB03 独立正式 PASS 审计

审计日期：2026-10-16。结论基于正式迭代计划第769、774行及其第42行 PASS 定义。当前 bt_api_py/dev HEAD 为 `7c135ccd8b86adc4921df67f168b2693dc5499c2`；Backtrader/dev HEAD 为 `be07a50ed3b4ddb5b419855827e7a965d5230b84`。本审计只读，未运行测试、Host、账户或服务，未改源码、tracker、migration gate WIP 或既有工作区状态。迁移 gate 的未提交 WIP 不作为 DIM02/ARB03 证据。

| 用例 | 独立裁定 | 原因 |
|---|---|---|
| DIM02 | HOLD | 主要缺口是 bar 修正冲突从真实 SDK 流进入实际 Backtrader 消费者的证据。现有测试中的 fail-closed consumer 是测试本地对象。 |
| ARB03 | GO | 固定源码集覆盖正式条款列出的首腿零/部分/完整，以及对冲拒绝/部分成交；不需要真实交易所账户或全笛卡尔组合。恢复事件的 `any()` 不是唯一证据，持久化组快照、精确残差、订单/outbox、provider 台账和 Risk 回执共同支持有界失败终态。 |

## DIM02 — 正式计划第774行

当前五个 DIM02 固定文件逐字节匹配已接纳摘要：`bt_api_py/service_market_legs.py` `1d6456c25406dd034cb098a71a3e4e412202b784b93e3f1083295bec6571edf2`；`tests/unit/test_service_market_legs.py` `d99776ab16a36b2adff8ca9ec992a3cc9f69ce90180a49a56a0f37c967a2c53b`；`tests/unit/test_service_market_dim02_integration.py` `938125294243acaeee6bb168d0c7f12c661c34ae91032ba599510327839e5e46`；`tests/unit/test_service_market_dim02_seam.py` `7c86904384a8423d42b2cccdc92984ef185a8e2e9e9df19c705b5612531a0bc6`；`tests/unit/test_service_market_dim02_legs.py` `a9de557c1321217304e5606e3b3b3622026801a15bf1f479c3d55cb07718a4b4`。这些是当前固定源码身份核验，不是本次重跑。

Round 176 原始根裁定 `D:\bt_api_py\docs\迭代计划\迭代10-可选组件与可配置通信\evidence\dim02-increments-20261016\root-adjudication.md` 明确只接受三个增量，未宣告正式 PASS。原始 QA 显示：多键/稀疏链节点作者最终两次均2 passed，独立复核两次2 passed；历史接缝和修正节点作者两次4 passed，独立复核三次4 passed；腿时间偏差节点9个单测及1个spawn节点通过，独立变异验证也通过。相应日志和报告在同目录 `incr1-author-status.md`、`incr2-runs\report.md`、`incr2-review-status.md`、`incr3-runs\report.md`。没有将历史增量 QA 重跑成此次审计证据。

同合约多频道/周期有真实 spawned ServiceHost、CURVE 和多个 SDK 客户端；hub 对每个 key 的真实 upstream/head/epoch/sequence，以及源时间和跨 key 隔离都由原测试断言。接缝节点真实运行历史查询与实时发布，历史更正重读返回 provider 的新值；腿偏差另由生产 `ServiceMarketLegGroup` 读取 `market.head` 源时间并跑真实链路。这些支持计划的订阅、投影、无重复上游和无伪造新鲜度要求。

稀疏期权链需精确描述：真实链路为每个 strike 建立独立订阅，测试只发布 strike 105；真实 hub head 对 110/115 保持0且 `latest_observed_at_utc=None`，无事件。随后 `_project_option_chain`（`tests/unit/test_service_market_dim02_integration.py:919`）在测试进程中把这些真实 head join 为 `live` / `no_data`，另一个节点固定其边界语义（约第1305行）。这不是生产期权链聚合器的证据；但正式条款没有要求新增该聚合器，故它足以作为测试 oracle 验证真实 per-key 投影没有替静默 strike 制造 freshness。不得把它表述为生产 chain aggregator 已验收。

bar 修正是 DIM02 的阻塞项。SDK 接缝测试 `tests/unit/test_service_market_dim02_seam.py:1061` 确认真实 SDK 流按序转发相同 open_time 的冲突 bar，但随后由测试本地 `_FailClosedBarProjection`（约第244行）执行冲突拒绝；这不能证明生产 consumer 拒绝冲突。被引用的 Backtrader 07B 生产消费证据范围较窄：当前 `D:\source_code\backtrader\tests\test_service_read_proxy_integration.py:884` 确实以真实 ServiceHost、ServiceReadProxy、Store/Broker/Feed/Cerebro 消费历史与实时流，并验证相同 bar 的重复项只投影一次；但它没有输入冲突修正。Backtrader `tests\test_service_read_proxy.py:565` 的冲突测试使用 fake `_MarketClient`，覆盖历史 bar 与 bootstrap snapshot 重叠冲突，不覆盖后续 live `poll_bar` 冲突。生产源码 `backtrader\stores\service_read_proxy.py:512,523,563` 有 HISTORY_BAR_CONFLICT/HISTORY_LIVE_CONFLICT 分支，却未找到对 live `poll_bar` 冲突分支的实际消费者测试；HISTORY_BAR_CONFLICT 也没有测试调用点。故“07B 已有 HISTORY_LIVE_CONFLICT / HISTORY_BAR_CONFLICT 的实际生产消费者证明”超出可见证据。

最少下一步：补一个固定源码、合成确定数据的跨 SDK/Backtrader 集成节点，让实际 ServiceHost/SDK client 产生同 open_time 的冲突修正，并由生产 `ServiceReadProxy`（最好经现有 Store/Feed 路径）消费；断言明确失败码、原投影未被替换/部分更新。将历史重读更正和 live 冲突的来源、固定 pins、原始 JUnit/stdout/stderr/exit 一并归档，再做一次独立审查。无需真实交易所账户，也无需为本条新增链聚合生产 API。通过前保持 DIM02 HOLD。

## ARB03 — 正式计划第769行

R164 原始根裁定和 QA 在 `D:\bt_api_py\docs\迭代计划\迭代10-可选组件与可配置通信\evidence\arb03-dim05-group-partial-fill-20261016\root-adjudication.md`、`qa\r2\result.json`；R190 三节点裁定、独立复核和固定源 QA 在 `...\evidence\arb03-r3-fill-scenarios-20261016\root-adjudication.md`、`review\arb03-r3-review.md`、`exit-producer.txt`、`exit-review.txt`。R164 两节点与 R190 五节点均有原始通过记录，R190 reviewer 独立复跑5 passed；本审计没有重跑。R190 QA 的 353 个固定来源与当前相关包 HEAD 对应（base `b4aabd1e`、execution `f5c03064`、risk `adb6ec58`、gateway `59b164ab`、transport `a397fdb4`）；当前 ARB03 测试文件仍为固定摘要 `2f06291dbed35bc56f54d6e502c001fd3316f9408cf4a0fab615acaff03a033e`。

语义覆盖是：R164 首腿1.5/2部分成交、第二腿拒绝，按 base 3 和步长发3.000，最终 RECOVERY_REQUIRED/残差3；R190 首腿拒绝零成交且不派发对冲、完整2/2后按base发4.000并配对，以及首腿部分成交后对冲发3.000、成交2并撤单，最终 RECOVERY_REQUIRED/残差1。零成交用 REJECTED，是因为生产证明把零成交 CANCELLED 判为 UNKNOWN；这与计划“零成交”一致，不应额外要求不可表达的零成交 CANCELLED。计划只列状态及合成 provider 边界，没有要求所有首/次腿结果的全笛卡尔组合或真实交易所账户。

“补偿耗尽后保留未解决状态”有超过事件 `any()` 的证据。生产 `bt_api/bt_api_execution/src/bt_api_execution/group_coordinator.py` 的 `advance_group` 对每轮最多 `2*len(legs)+2` 次迭代，并在状态转为 RECOVERY_REQUIRED 时返回；`_finish_hedge` 在两腿均为终态时按实际成交 base 计算差额，非零则持久化 RECOVERY_REQUIRED、`hedge_residual_unresolved` 与残差。R190 对冲部分节点断言最终状态/错误码/残差1、从 SQLite 读回的 `persisted == snapshot`、不存在 GROUP_PAIRED、单个 HEDGE_LEG_SIZED 与单个对冲 resolution 事件、每腿 dispatch_attempts=1 以及 provider ledger 各一次写入/查询。`any(GROUP_RECOVERY_REQUIRED)` 单独不能证明唯一事件，但唯一事件不是正式条款；组快照和其余计数已证明该路径有界并保留残差。

费用与风险不是只靠该 `any()`：R164 partial/rejected 节点断言首腿费用 -0.05 USD、第二腿零成交无费用；R190 full-fill 节点断言两腿费用；R190 partial-hedge 节点断言已成交的对冲腿费用 -0.05、订单/outbox 的 PARTIALLY_FILLED→CANCELLED、两腿 Risk 向量 claimed/resolved、无待决 execution authority holds，以及持久化的组残差。正成交 resolution 是生产 Risk 服务返回的 TERMINAL_EXPOSURE_RESOLVED 回执，合同要求其携带 exposure proof、reservation id/hash 等字段。范围说明：partial-hedge 节点没有单独断言首腿费用；`_risk_legs` helper 投影的是 claimed/resolved 布尔值而不直接比较 Risk state enum，组快照断言 account reservation 存在但没有直接比较其 phase。结合独立的 R164 首腿费用路径、R190 当前第二腿费用与生产 Risk terminal-exposure 回执，正式条款要求的费用、占用和残差类别均有同源真实组服务路径证据；这些是审阅时应保留的断言边界，不构成把条款扩成“同一节点逐字段比对两腿全部手续费”或“额外真实账户”的依据。

因此 ARB03 按第769行可 GO 正式 PASS。Root 需在其正式裁定/状态汇总中登记；本报告未改 tracker，也不把 GO 扩展到真实 venue 费率、滑点或未列出的完整组合矩阵。

## 保留状态

本仓库状态仍含既有 migration gate WIP：`bt_api_py/service_migration_gate.py` 与 `tests/unit/test_service_migration_gate_atomicity.py`；子包 `bt_api/bt_api_monitor`、`bt_api/bt_api_risk` 及其他未提交状态也原样保留。本次没有写入这些路径。Backtrader 仓库另有既有用户/其他轮次变更，也未触碰。正式 tracker 仍由 Root 决定是否按本审计更新；本文件是唯一新增输出。
