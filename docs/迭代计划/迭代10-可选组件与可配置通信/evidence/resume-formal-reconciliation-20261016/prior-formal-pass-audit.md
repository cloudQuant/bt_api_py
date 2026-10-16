# Iteration 10 formal PASS 独立复核：REG04、RISK02、CFG06

日期：2026-10-16  
复核基准：`D:\bt_api_py` `dev` HEAD `7c135ccd8b86adc4921df67f168b2693dc5499c2`（父提交 `405883b40fcdc415cfd67661c63f3749b8c0180f`）。

## 结论

| 条款 | 独立结论 | 证据等级与限制 |
|---|---|---|
| REG04 | **GO-with-disclosures** | 本机真实 spawn/ServiceHost/CURVE/SQLite 与 AFI 双 worker 服务链；合成 provider。round188 的改名 scope 拒绝和 round192 的持久冲突映射测试补上了第184轮留下的直接证据。没有 map-store→订单/额度账本的单条生产集成链，且人工 book 额度是间接证明。 |
| RISK02 | **GO-with-disclosures** | Risk 包级规则 + 本机真实 ServiceHost/CURVE/SDK/journal/SQLite 完整观察链；provider 叶为合成，无真实账户结论。已接受固定源结果和依赖等价检查支持当前提交，不要求重复已接受 QA。 |
| CFG06 | **GO-with-disclosures** | 8 个固定源节点、65 passed；round193 原始记录显示 147 项来源 pin 在该次执行前后相同。审计期间观察到并发 WIP 使当前工作树一项偏离 pin；对应 `dev` HEAD blob 仍与该 pin 一致。包含真实 ServiceHost/CURVE 注册、分析角色控制面和无 analytics/ML extra 子进程边界；分析角色在同一服务进程内，不是 AFI04 独立分析进程证明。 |

三条结论依赖实际测试、冻结运行输出和来源绑定；我没有把独立文档裁定本身当作行为证明。未发现必须 HOLD 的计划条款缺口。DIST01/02 与 ARB05 真双机按指示暂缓；这些要求不适用于本次三条计划行，本复核未把双机或真实账户作为额外门槛。

## 条款与证据

### REG04 — GO-with-disclosures

正式条款在[正式迭代计划.md:747](D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/正式迭代计划.md:747)，要求实际代理/服务上的动作与 scope 正反例，以及策略、人工 book、旧 ID 映射不产生双份额度。第180轮的 SDK 测试在真实 spawn 的 ServiceHost/CURVE 链中证明应用 worker 与策略单活 claim 可共存、同一策略 ref 的第二 run 得到 `DUPLICATE_ACTIVE_RUN`、account-only/错误 manual book 拒绝、保留策略/人工归属。AFI 测试以两个真实 uvicorn app 进程和策略 session 运行，4 个 application session/2 个主体仍归到一个 manual book，8 个 intent 恰好 8 次派发/预留，replay 不重复预留。原始独立审计的代码级检查见[acceptance-audit-r1/audit.md](D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/evidence/reg04-worker-isolation-and-afi-dual-worker-20261016/acceptance-audit-r1/audit.md)，原始测试与提交锚点为 SDK `6448381f` 和 AFI `e9c7ca1d`。

我另外核了 PASS 之后新增的两组证据，而非仅依赖第184轮 scope memo：

- 第188轮[test_service_migration_drill.py:1638-1688](D:/bt_api_py/tests/unit/test_service_migration_drill.py:1638) 通过真实 ZMQ/CURVE 连接把已绑定 strategy scope 改成不同 `strategy_ref`。服务返回 `SCOPE_DENIED`；之后 `provider_dispatch_counts` 仍为 `{intent_1: 1, intent_2: 0}`，Risk 记录与 business projection 未变，执行分区仍只有一条。测试于提交 `e137193d17ced6e065567e8ab1f3ae12edf6b020`，当前文件 SHA-256 与轮次记录一致（`f5cb6610…d8f`）。这直接验证改名 wire scope 不能另开订单分区或多占额度。
- 第192轮[service_migration_map.py](D:/bt_api_py/bt_api_py/service_migration_map.py) 与[test_service_migration_map.py:155-235](D:/bt_api_py/tests/unit/test_service_migration_map.py:155) 在持久 SQLite 上拒绝同一旧键映射到另一个 ref、拒绝两个 exclusive source 绑定同一目标，并保持旧映射/行数不变；[同文件:270-310](D:/bt_api_py/tests/unit/test_service_migration_map.py:270) 证明未知 ID 重开后仍为 `UNALLOCATED/待对账`。提交 `405883b40fcdc415cfd67661c63f3749b8c0180f`，7 个测试节点；模块和测试明确披露 map 尚未接生产调用方，故这不是端到端 quota-ledger 证据。

因此，第184轮的 scope 决定是背景和范围记录，不是缺失行为的替代证明。合并看，旧 ID 冲突映射有持久组件级测试，改名 wire scope 有实际服务负例且直接保持订单分区、Risk 行和额度计数不变；同 ref 策略 claim 与 manual book 的真实链也已覆盖，足以支持条款现有行为。披露应保留“尚无 map-store 到订单/Risk quota ledger 的组合测试”及“manual book 额度以唯一 reservation/book 与 EXE03 UNALLOCATED 分离间接证明”的限制。无须为 REG04 增加真实账户或双机门槛。

当前 tracker [迭代10验收状态汇总.md:57](D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/迭代10验收状态汇总.md:57) 仍写“改名不同 ref 无直接测试”，这在第188轮之后已过时；对应第184轮 addendum 也保留了翻转当时的历史表述。建议后续仅将披露改为：已有第188轮改名 scope 拒绝/无第二分区证据，以及第192轮冲突检查 map 组件；生产 map-store 到 quota ledger 的组合仍未证明。该记录不构成行为 HOLD，但不宜把旧披露继续作为 HEAD 状态。

### RISK02 — GO-with-disclosures

条款在[正式迭代计划.md:760](D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/正式迭代计划.md:760)：TTL、UNKNOWN、部分成交、撤单和重启的敞口转换，以及汇率/乘数/数据过期；证据需规则和完整订单观察链，且不得凭模拟余额宣称账户通过。

第134轮证明真实本机 ServiceHost/CURVE/SDK Risk 路由、SQLite journal 与合成 provider 上的填单、ACK、部分成交、UNKNOWN 保留、取消释放和查询幂等。第146轮 fixed-source host 节点验证 EUR→USD、乘数与 partial-fill 敞口、同 logical run 重启保留、陈旧 FX 拒绝新单/查询 UNKNOWN、新鲜 FX 撤单重估归零。第150轮 fixed-source host 节点验证 permit TTL 到期零 provider 写、canonical payload deadline 与 provider 请求分离，以及 dispatched UNKNOWN 跨 TTL/重启保留 USD150 并不重派；其接受修复把 journal `RESERVED → ADMISSION_DENIED` 转移写入当前根仓 [`service_risk_order_journal.py:3204`](D:/bt_api_py/bt_api_py/service_risk_order_journal.py:3204)。对应原始证据目录分别为[r134](D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/evidence/risk-order-exposure-service-20261016)、[r146](D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/evidence/risk-fx-applied-exposure-restart-20261016-r146)、[r150](D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/evidence/risk-order-ttl-restart-20261016-r150)。

第186轮独立对抗审计[原文](D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/evidence/risk02-pass-flip-20261016/acceptance-audit-r1/audit.md)逐项拆为 8 个行为片段与 3 个证据单元，核了实际测试断言、固定源码、journal 修复提交和后续路径差异。对照正式条款，合成 provider 是允许的证据边界；所有结果均明确不是账户接受，因此没有把模拟余额包装成账户通过。保留的限制包括非零 quote-currency fee 未验、三条 host 节点未在之后每个提交上重复执行、transport/execution gitlink 的无关漂移。独立审计已逐项核查相关风险路径等价，且现有受接受 QA 不应为本次复核重跑。故为 GO-with-disclosures，无真实账户/服务外 claim。

### CFG06 — GO-with-disclosures

正式条款在[正式迭代计划.md:743](D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/正式迭代计划.md:743)：strategy/application/service 角色、出站 clients、执行/分析角色和 policy_catalog；错误类型/版本拒绝、目录失联不影响已确认政策；正反配置与真实注册、无 analytics/ML extra 时账户 admission/分析基础能力及缺失 extra 的显式失败、拒绝配置驱动任意模块加载。

第193轮 r2 在固定源码上一次运行 8 个节点/65 项，全通过、pytest exit 0，JUnit 65/0/0/0，warnings 为 2，stderr 0 字节。原始执行记录显示运行前后 147 个源码 pin 相同；运行时五仓 HEAD 均记录且未变。原始输出 `D:\temp\it10cfghost-formal-20261016-r2` 的 spec、runner、result、JUnit、source-before/after、stdout、stderr 与提交到仓库的归档逐项 SHA-256 一致。核心工件为[spec.json](D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/evidence/cfg06-formal-r2-20261016/spec.json)、[result.json](D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/evidence/cfg06-formal-r2-20261016/result.json)、[junit.xml](D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/evidence/cfg06-formal-r2-20261016/junit.xml)、[冻结 runner](D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/evidence/cfg06-formal-r2-20261016/runner.py)；归档提交为当前 HEAD `7c135ccd`，仅提交验收材料和 tracker/进度，没有改产品源码。

本次只读复核期间，工作树出现并发 WIP：`bt_api_py/service_migration_gate.py` 当前内容与 r2 的该项 pin 不同（期望 `70700cd648da85d9dcca4ff0e55022cd44f2f5e12090a0734a852b5f3f0cc39d`，当前工作树 `3f89a0ea4cf20442057560e393ae955a1b56b3e9e475b3cdb77a51febf970385`）；该文件在 `dev` HEAD 的 blob 仍为期望值。故这里不再声称当前可变工作树的 147 项全部吻合，也不触碰此 WIP。该漂移不改写 r2 当时冻结来源与原始执行结果；交付前应由该 WIP 的所有者按其验收流程处理。

最终来源字节下的实质覆盖是：

- [policy_catalog 测试](D:/bt_api_py/tests/unit/test_service_policy_catalog_control_plane.py:428) 运行真实 `ServiceHost` 并启动 ZMQ/CURVE；application manager draft/approve/publish/read/receipt、错误类型/版本、service 出站只读 client、错误 scope 与目录关闭后的已确认版本读取均有断言（`:463-623`）。R2 固定源轮包含此文件。
- [portfolio analytics 测试](D:/bt_api_py/tests/unit/test_service_portfolio_analytics_control_plane.py:846) 真实注册分析角色，含 route/scope 正反例与任务状态/容量/失效路径；其子进程边界节点（`:1555-1738`）阻断 numpy/scipy/sklearn/pandas，实际运行基本 portfolio analytics，并断言 `RiskManager`、`AnomalyDetector`、`RiskEnsembleModel` 在缺 extra 时返回带 analytics/ml 标记的 `ImportError`。另一个受阻 import 子进程见 [policy_catalog 测试](D:/bt_api_py/tests/unit/test_service_policy_catalog_control_plane.py:783-842)：在同一 blocker 下成功导入 `bt_api_risk.order_admission` 与 `bt_api_risk.core.risk_assessor` 并完成 policy_catalog 流程；这证明 account-admission 代码的无 extra import closure，实际准入行为由同一来源树的其他 Risk/service 测试覆盖，但没有在该 blocker 子进程内提交一笔账户订单。该轮 source pin 覆盖了 analytics 测试和所需源码，因此不依赖 r156 的漂移字节。
- [combo-3 Host/CURVE 节点](D:/bt_api_py/tests/integration/test_group_policy_host_combo3.py:149) 以真实 spawned Host/账号 owner 证明 group_policy 配置、内部 gate 接线、正向 PAIRED 和 scope/public 拒绝。R2 的 `spec.json` 明确包含该路径及两个 CFG06b group-policy 文件。
- Risk `service_component.py` 的 fixed role-port table 和封闭分支（`_RISK_ROLE_PORTS`/`validate_service_options`；当前文件约 :57-150）不从配置导入任意模块；冻结测试的 unknown role/额外选项拒绝作为负例。独立披露准确指出没有通用的运行时任意 import 探针，故结论限于当前固定内置角色/配置面，不宣称一般 Python sandbox。

R154/r156 的旧 pin 集合已漂移，本裁定不把它们单独当最终字节覆盖；r2 新增 analytics 节点并固定了当前相关源码。AFI04 要求的独立跨进程 analytics worker（计划 :796）不在 CFG06 条款内，r2 将其明确限为同一真实服务进程内角色切片，不能把本 PASS 写成 AFI04 通过。由此没有因缺少跨进程、真实账户或任意 import 注入探针额外阻断 CFG06。

## 操作边界

本复核只读查看了 AGENTS、正式计划、tracker、已提交审计、原始 QA/源码/提交，并复算了 CFG06 原始输出工件 SHA-256 与发生漂移文件的 HEAD/工作树哈希。未运行测试、未启动/停止 Host、未访问真实账户、未修改源码或受限 Host 状态。复核期间观察到 `service_migration_gate.py` 与 `tests/unit/test_service_migration_gate_atomicity.py` 的并发 WIP，均未触碰；本报告是本审计唯一新增文件。

