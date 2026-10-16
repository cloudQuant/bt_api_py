# AFI12 正式验收：来源保留、受控旧Paper账本迁移与回退

Root按正式计划第804行核对，裁定 **AFI12正式PASS**，累计47/68。固定脱敏样本通过正常AFI HTTP→ServiceReadWorker→SDK session→本机真实CURVE/ZMQ ServiceHost→Execution SQLite进口路径；这是合成数据的本机迁移验收，不是真实用户或交易所账户迁移。

AFI dev `44ad881b729bfd1fa489b73d522c82debecc744d`（parent `8e20e764045dc09002f3cc972e7e55973f78cdb2`）精确14路径包含两个迁移服务、API/router、ServiceRead绑定及worker/session/deployment闭包、三项测试和记录。独立reviewer resume_bt_acceptance核对提交闭包和§804为GO，Root复核实际测试、来源与提交。SDK入口在 `632877b32e6c43cc75b1e693b8cfcc8aae8c117f` 已提交；Execution残差/P&L修复为 `77a97a1d5a4a2af2b4351c08baeeb550c79d2159`，wire Mapping修复依赖 `5b83e7e584ff70f851e4b655ace781ab6b68fbf7`。

| 条款 | 证据与结果 |
|---|---|
| 原始记录及来源、账户/持仓归属 | R81 AFI aead91066655effe42e2dd1098da28243f6b56bf 保留原JSON/schema/hash/owner隔离来源；本轮正常Host非空迁移保留1笔来源order/position/trade，来源digest前后相同，固定受信账户及策略绑定。 |
| 现金流、费用、持仓及未分配残差 | 正向样本目标现金89.9、累计费用0.1、持仓1@10，源原始记录不变；不一致P&L、未决订单及不可表示的0.5 equity residual明确拒绝。残差保留在来源PREPARED记录，不静默分给策略或激活目标owner。 |
| 旧规则、审批、告警及游标 | 原RiskRule、Alert/AlertRule/Notification、approval/events/policy版本及epoch、三个PaperEquitySnapshot保存到preserved_context；源模型metadata字段按SQLAlchemy映射读取。旧base64url observed_at/id来源游标在迁移后继续分页得到相同记录与next_cursor，不把它冒充新目标游标。 |
| 受控回退与幂等 | 残差拒绝后来源PREPARED、目标无receipt/owner。错误digest回退403且不改状态；确切digest回退ABORTED、同receipt/generation重放幂等，来源数据和controls不变，原writer lease可再次取得。此节点回退的是来源准备态，不声称已激活目标被回退；已ACTIVE的导入禁止回退。 |
| 导入事实与交易所事实分离，GET不改账 | 目标明确origin=imported_legacy，execution receipt_count=0、cursor=0；导入无交易所成交伪造，不激活旧规则/审批/告警。初始HTTP GET前后Execution SQLite total_changes相同。 |

两项新Host selectors位于 `src/backend/tests/test_legacy_paper_import_http_host_integration.py`，已提交文件36136字节/SHA-256 `4af0e1236f44a8214bb1b8b6c0ab5e4ab4d24d407714ba5861c6e81aee8d1bd7`。两个节点各作者1/1通过，后者有已知Windows ZMQ Proactor警告；只有原工具控制台结果，没有持久JUnit/stdout，不补造运行文件或改称Root QA。其HTTP get_current_user采用固定TokenPayload fixture，Host仍执行持久owner及grant绑定；JWT/login依据既有AFI01证据，不在此新增认证声明。

既有backend六selectors（同目录 `test_legacy_paper_import_migration.py`）作者6 passed/6.45s覆盖非空守恒、残差冻结/回退、未决订单拒绝、P&L不一致、HTTP只读和digest回退，也只有原工具记录。R81的API6项/UI51项记录见开发进度第81轮；它单独不证明完整旧账迁移。

本目录原字节归档的Execution Root R2是另一组4个包级selectors：pytest exit0、stdout 0.91s、JUnit4/4、144输入及E/base HEAD不变。运行时E HEAD为5c531c68，使用当时工作树候选；它早于最终零现金百分比反例/兼容修正，不把该4/4称为最终字节复跑。最终窄补另有作者2项通过和独立静态GO，R1失败保留。其他名为paper-host的AFI13 JUnit不用于本裁定。多轮互补证据不是单一全绿运行。

本SDK轮仅本目录五份证据/裁定与状态、进度两文件，未重复QA。源码接受范围不延伸到AFI13受管撮合完整组合、AFI10退役、MIG03切换或真实provider。实际提交与scope见Git历史及 `.codex-tmp/iteration10-afi12-r273-postcommit.json`。
