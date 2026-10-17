# R278｜AFI06 正式验收裁定材料

日期：2026-10-17  
条款：正式迭代计划 AFI06（第 798 行）  
状态：Root 已同意完整条款映射并授权整理本包；本文件建议将 AFI06 记为 PASS，计数由 51/68 增至 52/68。全局计划与统计文档未在本轮修改，由 Root 按其正在进行的 R277 更新统一写回。

## 建议裁定

AFI06 的列明条件由五轮互补证据覆盖，并由 R269 的新增固定源码 SQLite 节点补上“预算目录与各账户实际版本/占用核对”缺口。建议 AFI06 正式 PASS，净增一例。该结论仅覆盖本条款；不表示跨账户全局原子准入，也不表示真实账户、真实 provider 或实时 FX 部署验收。

R269 的新节点为：

- 选择器：tests/test_afi06_budget_directory_owner_occupancy.py::test_reopened_budget_generation_reconciles_with_active_and_unknown_account_ledgers
- AFI 测试源：16328 字节，SHA-256 452c7a72f631e7bb5cb35da7bad4cca4f3628f62403b604b896d453e087e19c0
- 测试源提交：AFI dev b637c2eb5e325a3352a9c4828b2dbef4bd46d64a，父 811ae17522568653a73c2f66df54c58f17fa29c3。这个提交包含测试和本轮记录；测试文件在提交中的 Git blob 为 71a9e8db3a3ce6d9bad2671c7138466957a81d86。
- 测试运行目录：D:\temp\afi06-budget-occupancy-fixed-source-20261017-r1
- 执行器：AFI 后端虚拟环境 Python 3.12；复用 .codex-tmp/afi05-06-static-budget-20261016/root-qa/runner.py（3204 字节，SHA-256 8b7f470be77270adeb9d72a83776b827c4653004b39835eeca0f43bf63cf24ed），只运行上述一个选择器，--noconftest、禁用 pytest 插件自动加载和缓存。
- 固定运行时来源：父仓 bt_api_py 提交 8c8131ccdcb0df817fdaaaaf583e756de0ec52c2；Base 09d3ea8b07f8d3f724f2124271a2ac53a0be863e；Risk ad5c693e69b66b0982b7f889e6f237ca3629e587。固定导出清单 source-export-manifest.json 为 39299 字节，SHA-256 61a2c45b1a5f4916247ec71a8287bea80c295c108b526c6b8445cfef48856a77；139 个包源码文件逐个对应已提交 Git blob。测试源码是单独钉住的 AFI overlay，不从混合 AFI 工作树导入。
- 结果：1 passed，pytest exit 0，143 个源 pin 前后不变。JUnit 为 416 字节，SHA-256 ab138d7f69d3166d718c248aa2b57d79caf075c62c2948f7b75260b67bca2412；stdout SHA-256 cfe5f639ab1958b8f5184e3690d597579da0db6c3b8ab69ab1fa487d5cfa574d；stderr 空。执行者按 R269 记录为本地作者 fixed-source 运行；原始结果 JSON 中的 evidence_level 文本保留于归档，本裁定不把它改述成 Root 执行的 QA。

R269 建立并关闭后重开真实 SQLite PolicyCatalogStore、ConfirmedPolicyStore 与两个账户各自的 DurableRiskGate 数据库，再对照账户 RiskReadService 快照。两名成员预算为 25 与 15，未分配额 10，合计 50；容量 100 的池已用 50、可用 50。两个已确认 policy 的版本和 fingerprint 均与目录匹配。账户 A 有 7 的持久 ACTIVE reservation；账户 B 有 4 的未决 reservation，Risk dispatch 状态为 UNRESOLVED_ACTIVE。生成状态仍为 authority_ready、consumer_effective=false；目录 authority 状态与每笔 dispatch 的 UNKNOWN 锁存状态被明确区分。

## 正式条款到证据的映射

| AFI06 子条件 | 证据与裁定范围 |
| --- | --- |
| 同量纲静态组合预算分配守恒 | R219 16 个 Root fixed-source 节点覆盖精确金额、最小单位守恒、成员绑定、混合 metric/currency、重复与不精确金额拒绝；R269 再验证 25 + 15 + 10 = 50 的持久目录总和。R219 输出目录 D:\temp\iteration10-afi05-06-budget-focused-root-20261016-r1。 |
| 并发发布、成员重叠及容量核算 | R226 的同一 R3 Root run 中，五个 Risk authority 单元节点通过，其中包含跨 SQLite 连接串行容量/账户 claim 和预算维度/重叠成员/发布前撤回；第六个 SDK Host 节点失败。后续 R7 在最终 R223 + R221 + R226 fixed-source 组合上通过独立 Host 节点，不能抹去或并入 R3 的失败。 |
| ACTIVE/UNKNOWN 占用及不得重复释放/分配 | R226 单一 durable Risk owner 持久化容量 claim；发布后 partial reject、UNKNOWN、缺失/不匹配 confirmation 保留 hold，只有发布前可安全撤回。R269 在真实重开的两份账户 Risk ledger 上读到 ACTIVE=7 和 UNRESOLVED_ACTIVE=4，并与已确认成员版本/分配核对。 |
| 撤回未确认额度不能造成越权重分配 | R226 的 SQLite authority 单元覆盖未发布提案撤回以及已发布/未知状态保持 claim；撤回释放仅限发布前。原始结果归档保留所有通过与失败轮次。 |
| 陈旧 FX 与不同 metric 不可混加 | R219 对混合 metric/currency 与不兼容静态组合输入拒绝；Risk FX R268 以可信服务端 FX 源、新鲜度校验和精确 Fraction 守恒覆盖 proposal、approval、publish、confirm 与 resolve 阶段的重验。R268 两节点为作者 R5 fixed-source 执行，不是实时汇率或真实账户测试。 |
| 预算目录与各账户实际版本/占用核对 | R228 两个 Root fixed-source resolver 节点验证重开后精确绑定 generation、member、confirmed receipt 和版本；R269 再把重开的目录、两份 policy 和两个 reopened account ledger/RiskReadService snapshot 逐项对账。 |
| 订单侧的有效版本读取佐证 | R250 为作者执行的一条 authenticated AFI HTTP → SDK ServiceHost → Risk admission 合成单账户路径，覆盖预算内许可与超预算拒绝。R250 不证明多账户目录核对，也没有把 consumer_effective 宣称为 true。 |

## 必须保留的 R226 原始历史

R226 Risk dev 提交为 ede55dec5e8e7ad1c5376969eb54e9933ebf9e85，父 647840a75b2071fcb5b4395f554190afe2fae082。

- R3 输出目录 D:\temp\iteration10-afi-budget-authority-host-focused-root-20261016-r3：同一次固定源 Root run 共 6 个节点，5 个 unit pass，1 个 Host 注册失败，pytest exit 1，543 pins unchanged。Host 只报告注册失败；不把后续源码推断当作已恢复的运行时原因。
- R4 输出目录 D:\temp\iteration10-afi-budget-authority-host-focused-root-20261016-r4：单 Host 节点收到 immutable mapping reply 后，测试断言只接受 dict 而失败；原始失败保留。
- R5 输出目录 D:\temp\iteration10-afi-budget-authority-host-focused-root-20261016-r5：早期固定组合上的一条 Host pass，保留为早期成功轮。
- R7 输出目录 D:\temp\iteration10-afi-budget-authority-host-focused-root-20261016-r7：最终 R223 + R221 + R226 fixed-source 组合的一条 Host pass，569 pins unchanged，stdout SHA-256 649a12e0965dfad135be2898144608f65342deef7632d774ee8a373e7bda38a1，stderr 空，保留一次 Windows Proactor/ZMQ warning。R7 不是 R3 的续跑或覆盖；R3 的 5/6 + Host failure 事实仍保持原样。

## 其他轮次来源及边界

- R219 AFI commit 5be2e26c9cbb558187ed4ba7cce622989cb50c0e；Root fixed-source 离线编译/目录结果 16 passed，正式状态当时为 39/68。
- R228 固定组合结果目录 D:\temp\iteration10-afi-budget-resolver-focused-root-20261016-r2；2 passed、147 source pins unchanged。
- R268 Risk commit 6d51dc8ec28364cb79688caeb0073e0d3615beff。R5 的固定 source/run 目录 D:\temp\iteration10-afi-budget-fx-focused-author-20261017-r5；两个此前失败的 FX 节点通过。其 Risk/Base 来源与源码 overlay 均保存在 R268 原归档。
- R250 作者 R8 运行目录 D:\temp\iteration10-afi-budget-manual-host-root-20261016-r8；1 passed，2190 source pins unchanged，保留原 Proactor warning。其原始结果字段的 Root 标记不代表 Root 执行者；R250 记录据实标为作者本地 fixed-source HTTP/Host 路径。

凡没有固定运行源码/模块来源的历史作者单测 1-pass（包括此前未绑定 Base `09d3ea8b` / Risk `ad5c693e` 固定提交的同选择器运行），不作为本包的 fixed-source 依据；只引用上文固定导出组合上的 1-pass。独立条款复核来自协作消息（review-message-only），未另有审查文件。原 R269 fixed-source 验证、已有 R219/R226/R228/R268/R250 归档与其来源边界均保持原始字节。

## 限制

全证据均为合成数据、本地 SQLite 或合成 provider。没有真实账户、真实 provider、交易所调用或部署。总组合汇总是异步目录视图；本包没有证明多个账户 Risk admission 之间的全局原子性，也不应宣称这一点。R269 的 budget generation 状态是 authority_ready 且 consumer_effective=false；只有测试涉及的账户 policy receipt 与本地 Risk ledger 状态得到对账。R268 的 FX 输入为固定/可信测试源，不是生产行情接口。

本轮未重跑已接受节点，未编辑正式计划/全局进度统计，未改生产源码；仅准备 R278 三件证据包供 Root 做最后 adjudication 与统一写回。
