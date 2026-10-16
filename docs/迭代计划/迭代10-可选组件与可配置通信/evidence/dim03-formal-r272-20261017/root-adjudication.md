# DIM03 正式验收：多维预留、共享抵押品与完整持仓基线

Root按正式计划第775行逐项核对，裁定 **DIM03正式PASS**，累计46/68。证据是计划要求的本机Risk/SQLite及真实ServiceHost通信，provider数据为合成；不代表真实交易所账户验收。

| 条款 | 已接受证据 |
|---|---|
| 多品种/产品竞争同一保证金池；原子预留、不重复授信 | R167固定源码114项（含7个新节点）；R217七个唯一节点跨两次运行验证ACTIVE/SETTLED/UNKNOWN跨scope产品聚合与去重；R223六个启动拒绝和两个真实Host共享/独立账户正向节点。两个AccountKey共享抵押品而缺统一准入owner时拒绝写入。 |
| 策略及合约集中度、源绑定Greeks | R237六个SQLite节点：跨合约集中度、向量事务回滚、Greeks来源/profile不匹配、新鲜正确来源、UNKNOWN重开仅计一次及旧记录缺维度拒绝。 |
| 计价和资源范围、FX缺失/过期 | R232三个真实Host节点：20 EUR乘1.5 USD/EUR等于30 USD；过期FX拒绝且零provider写入，缺FX在registry建立前拒绝。 |
| 过滤快照不冒充全账户；持仓重估完整性 | 正常Gateway worker向Risk owner提供精确类型全量快照receipt，绑定完整账户/venue/instrument、分页权限版本及估值源。R4正常Host安装/维护与错误venue节点通过；R5只重跑修正fixture后的跨AccountKey节点并通过；filtered Host节点一项通过，持久基线/注册/派发均未新增。 |
| 基线持久性与冲突拒绝 | 新baseline-owner R2一项通过：首次安装USD30、单维度/单基线；新Gateway generation及Risk journal重开后同版本同内容无新增；同版本quantity改变报GROSS_BASELINE_REVISION_CONFLICT，原journal及Risk投影不变。 |

各旧轮的结果、源码提交、原始archive与pin在同目录 `evidence-map.json`；这是先前创建的裁定输入，其formal=false保留历史含义。最新R2原结果、JUnit和stdout以原字节归档，pytest exit0，372输入pin及六仓库HEAD不变；是作者运行，未改标Root QA。R1的10通过/1个SQLite contextmanager用法错误保留，R2仅重跑该失败节点。Filtered Host外层runner因无关HEAD提交返回1，但pytest自身exit0、371输入未变，原因和限制保留在映射中；宽泛异常断言不提供精确诊断代码证据。

最终源码为SDK dev `c62de0faafc7957a77eb61e829e3a3f225a12b6a`（parent `f8c544338fdb8c2d1823f72f482637550c1d6003`），恰八个源码/gitlink路径加round-record。Risk gitlink为 `d22c8abb4fadfc8b2216c88e9ce5ad1041173d43`，Gateway保留 `8d05c53690d25409afd9bfe38c34b82d5bc56a89`。Root与独立审阅venue_acceptance_readiness确认patch精确范围、依赖API和正常入口；`baseline_guard`恢复正确contextmanager装饰，匹配判断仍为普通布尔函数。Paper已提交代码和MIG03维护屏障保留，最终components全文件与R2运行导出不完全相同，差异为169行既有Paper代码，不把完整文件等同于测试快照。详见已提交 `evidence/dim03-position-owner-r3-20261017/round-record.md`。

R257旧15项自报完整映射不单独证明可信全量，已由后续typed Gateway receipt路径补足。当前Binance实际adapter缺权威全账户单调revision时仍拒绝启用该基线，不编造revision，不宣称真实Binance可用。不同轮次是互补证据，不是一轮针对全部当前WIP的全绿测试。此裁定不接受DIM04/DIM05、VENUE或双机场景。

本轮SDK只提交本目录五个证据/裁定文件和状态/进度两文件。实际提交及后验scope见Git历史及 `.codex-tmp/iteration10-dim03-r272-postcommit.json`；没有重复已接受QA。
