# MIG03 翻转对抗审计 — 迭代10 正式验收（READ-ONLY, r1, 2026-10-16）

- 审计对象：case MIG03 是否可从「部分」翻转为「正式 PASS」。
- 仓库：`D:\bt_api_py`，branch `dev`，HEAD `e137193d`（=incr-B 提交，已核实）。
- 审计约束：除本文件与 `D:\bt_api_py\.codex-tmp\mig03-flip-audit-status.md` 外零写入；未运行 pytest；未执行 git 写命令；未杀进程。预算内共约 30 次工具调用。
- 判定：**NO-GO**（当前不足以翻转；阻塞项 F1，次级 F2/F3；收敛路径见第 6 节）。

---

## 0 输入完整性核验（先证实证据是它声称的东西）

1. 生产/测试文件哈希与 `evidence/mig03b-drill-20261016/pins-before.txt`（before==after）逐字节一致（本次实时复算）：
   - `tests/unit/test_service_migration_drill.py` `f5cb6610…91d8f` 87816 B；
   - `bt_api_py/service_migration_gate.py` `70700cd6…39d`；
   - `bt_api_py/service_host.py` `07e95f92…be1`；
   - `tests/unit/test_service_migration_gate.py` `afaca97a…3cb`。
   → incr-B 生产零改动属实；演练所跑测试文本 == 当前仓库文本。
2. `run-03-final/junit.xml`：`errors=0 failures=0 skipped=0 tests=2 time=15.003`，两节点名与声称一致（8.052s + 5.973s）；`stdout.txt` = `2 passed … 14.99s`；两个 observation JSON `status: PASS`，`forced_cleanup=[]`、`failures=[]`、`runtime_cleanup_failures=[]`；epoch `0d6c04fa…`/`11e4739f…`/restart `b62cc356…`、`ORDER_OWNER_ALREADY_ACTIVE / provider_calls=1 / registry_created=false` 均在 JSON 中实测存在。
3. 6 套回归 junit 实测属性：12/1/1/1/32/21 = **68 tests，全 0F/0E/0S**；`ruff.txt` = All checks passed / exit:0。与 00-INDEX.md、review 的引用一致。
4. 披露的 run-01 重大发现物证存在：`D:/temp/it10mig03b-20261016-r1/run-01-fix1/junit.xml` `failures="1"` 且失败断言文本 `assert 'REJECTED' == 'UNKNOWN'` 原样保留（同一冻结根下另有 run-00/02；仓库提交只收录 run-03-final + regressions，为已披露的打包口径）。
5. `e137193d` 提交内容与声称范围一致：28 文件 = 证据归档子集 + 进度/汇总文档 + 新测试 2162 行；无生产文件。
6. 演练测试通读 2162 行：无 `skip`/`xfail`/`monkeypatch`（grep=0）；恰两节点；`multiprocessing spawn` 真实子进程 + 真实 ZMQ-CURVE + 真实 SQLite 文件；双 owner 负例中竞争者跑在 pytest 父进程、锁由 spawn 出的 host-b 子进程持有 → **确为跨进程锁**，不是进程内 fake；`_RefusingDispatch`/`_assert_fixed_source_origins` 防止“被拒者偷派发/导入伪装”。真实进程主张成立。

## 1 义务片段 × 断言覆盖表（逐片段；行号为现仓库文件）

状态：COVERED=断言直接覆盖；PARTIAL=有断言但有已识别缺口；UNCOVERED=无实现/无断言。

| # | 义务片段（来源） | 覆盖证据（具体断言） | 状态 |
|---|---|---|---|
| 1 | 冻结旧客户端（:784；§11.3 步骤2 :861） | drill 1397-1408：`runtime1.close()` 后同一 session 查询 `NOT_REGISTERED`、两 instance `active_registration_ids=()`、业务投影不变且 host 仍在服务；1410-1411 正常关闭 exit 0 | COVERED（查询暂停窗口未保留单独 authorized 查询路径——小缺口） |
| 2 | 确认旧 writer 失效（:784；步骤3 :862） | drill 1461-1463 `STALE_EPOCH`；1480-1491 竞争者 `ORDER_OWNER_ALREADY_ACTIVE`+`registry_created=false`+`provider_calls=1` 且 `_RefusingDispatch` 保底；1410/1790 关闭事实（closed ack、exitcode 0）进 observation | COVERED（跨进程锁=execution sidecar lock 一路；GW writer authority/R risk fence 未在本演练复演，属组件既有证据） |
| 3 | 迁移订单（:784） | drill 1451-1458 新 epoch 接管后 exec rows/风险行/journal 与旧 epoch 逐项相等；1534-1538 新登记后查询同 UNKNOWN、同 permit、attempts=1；1857-1859 终局相等 | COVERED（表示法=同库新 epoch 接管+按原 ID 守恒；无独立旧库导入器——披露项 F4） |
| 4 | 迁移归属（:784） | scope 断言：1552-1554 `replay.scope_key == first_order.scope_key`；明细行含 scope_key 逐项相等；1412-1426 legacy 未映射 scope 事件原位保留；1638-1689 改名 scope `SCOPE_DENIED`。**成本归属/人工 book 未演练**（无成交/无人工 book） | PARTIAL |
| 5 | 迁移预留（:784） | drill 1369-1387 预留/claim/freeze 三元组精确断言；1457/1497/1863 跨 epoch 逐项相等；1719-1724 新 epoch 首写被拒后 `increase_count=1`、`increase_notional=100000`、冻结仍在、intent-2 零 risk 行 | COVERED |
| 6 | 迁移幂等（:784） | drill 1548-1572 同内容重放同 permit/attempts 且计数 {1,0}；1560-1564 异内容 `INTENT_CONFLICT` 且携原记录；2041-2063 二节点复演；1727-1736 seal 记录幂等（True→False）；gate 88-98/154-155 arm 幂等与重臂不解除 | COVERED |
| 7 | 迁移游标（:784；步骤4 :863） | drill 1333-1357 stream_epoch/offered=2/acked=2；1574-1636 已 ACK 不重投、新事件恰一次、progress 4/4；1499-1519 `STREAM_EPOCH_MISMATCH`/`STREAM_EPOCH_REQUIRED`；1828-1833 跨重启保持；2118 回退后 journal 投影不变 | PARTIAL（“源流失效→从新快照重建”分支未演练；无旧 forwarding 消费基线导入） |
| 8 | 建立新 epoch 并重登记（:784；步骤5 :864） | drill 1445-1450 epoch2≠epoch1；1528-1545 recover 后全部 epoch2 且 registration_id 全部更换；1540-1545 活跃登记集合一致；1791-1818 epoch3 重启+再次 recover；旧绑定 `STALE_EPOCH` | COVERED（组合 REG03 既有机制） |
| 9 | 导入失败（:784） | drill 2047-2063 异内容导入 `INTENT_CONFLICT` 携原记录、计数/投影不变（不覆盖、不填零） | PARTIAL（服务 store 层“半迁移回滚”未演练——表示法无 staging 导入；门面等价于 journal 切迁“未提交可回滚”由既有 `_execution_session` 证据承担） |
| 10 | 切换前后回退明确（:784；步骤6 :865） | 切换前：drill 2066-2074 旧 epoch 可启动+`rollback_migration_cutover` 移除 marker+2077-2110 完整旧部署重启、事实守恒；切换后：1742-1782 封存后旧部署子进程被拒 `REVERSE_MIGRATION_REFUSED`（state=failed、provider_calls=()、exit 0）、rollback/undeclared/foreign 全拒、marker 字节不变；incr-A gate 100-123/126-178 | COVERED（组合） |
| 11 | 受控迁移（验收栏） | incr-A gate 全生命周期（arm/冲突/损坏 fail-closed/跨解释器持久）+ drill 全流程 marker 状态推进；2028-2031 `CUTOVER_MARKER_CONFLICT` | COVERED（“首次写→seal”触发为人工程序，无生产调用者——披露项 F3） |
| 12 | 调用计数（验收栏） | drill 1367-1368/1557/1571/1704-1707/1864-1865 provider `{intent_1:1, intent_2:0}`、cancel=0 各相位恒等；1382-1383/1719-1724 risk increase 计数/额度守恒；登记 id 集合逐相位断言 | COVERED（真实、非空断言；计数源为持久 SQLite 表） |
| 13 | 无双 owner（验收栏） | drill 1480-1491 + `_RefusingDispatch` 保底 + 事后投影不变；1461-1463 旧代次 `STALE_EPOCH` | COVERED（限 execution owner 锁；见 F5） |
| 14 | 无删除 UNKNOWN（验收栏） | drill 1327-1331/1361-1367/1534-1538/1785-1786/1820-1827/1857-1859：state=UNKNOWN、同 permit、attempts=1 跨 drain/接管/首写/重启/回退窗口全部保留；计数恒等证明不重派 | COVERED（“不删除”以五相位保留断言证明，非仅注释） |
| 15 | 首写后无已验收反向迁移则拒绝回旧版（验收栏） | incr-A gate 126-178（seal 单向、旧/未声明/未知 epoch 全拒、rollback 拒）+ 278-300 跨解释器持久 + host 385-405 start 首动作拒绝零副作用；drill 1727-1782 真实流程端到端 | COVERED（机制+接线+演练=组合；触发靠人工 arm/seal，见 F3） |
| 16 | REG04 转记负向义务（:854；scope-decision §MIG03 后续义务） | drill 1638-1689 改名 strategy/account scope 双双 `SCOPE_DENIED`；事后 1684-1688：计数不变、风险行不变、`execution_records` 行数==1（无第二分区）；1714-1718 第二意图零额度（无双份额度）；1412-1426/1846-1856 未映射 legacy 事件原位保留、对映射消费者永不投递（断言事件列表精确相等） | COVERED（负向半项；保留=scope 级原位保留） |
| 17 | “无法判定归属保留未分配/**待对账**”（:854） | 同上：legacy 事件原位保留、不重归属、不投递是实测断言；但**无 UNALLOCATED/待对账状态表示**（现有 UNALLOCATED 残差投影在 `bt_api_execution/account_reconciliation.py` 等，未与此演练对象挂接） | PARTIAL（保留=有断言；“待对账”语义未实例化） |
| 18 | “不改名后重新授信或重新下单”（:854） | 同 #16：改名提交被拒→零新派发/零新额度；同 session/run 幂等重放保持原分区；seal 后旧部署不得再起 | COVERED |
| 19 | **持久映射及历史关联**（:854“建立有冲突检查的持久映射”；:426 SDK 目录职责） | **无实现、无测试、无断言**：`bt_api_py/` 全仓无 legacy_strategy/alias/mapping/迁移映射模块（本次 grep 实证）；两增量生产零改动；drill 遇改名直接拒绝（不映射）；REG04 scope 决定把“构建”明确转记 MIG03 线；REG04 正式 PASS 披露 (1) 明确“不认证映射本身”并指向 MIG03 | **UNCOVERED（F1）** |
| 20 | 租约过期/心跳消失不能独自证明 fencing（步骤3 :862） | drill 未以租约为失效证明（用进程退出+锁+epoch），方向正确；但未演练“lease 过期后接管”的反例 | PARTIAL（设计上满足；负例未演练） |
| 21 | 步骤1 盘点（G 会话/E 成交/成本归属/人工 book/R 政策/组腿/outbox/checkpoint/AFI 操作键） | drill 覆盖 registry/execution/risk/journal 四类；无成交、无人工 book、无组腿、无 outbox/checkpoint、无 AFI 操作键（超本地演练范围，:867 允许） | PARTIAL（范围披露 F5） |

## 2 对抗性检查记录（falsification attempts）

- **A1：找无断言覆盖的片段** → 命中 F1（#19，持久映射构建：零实现零断言）；次级命中 #17（待对账语义）、#7 重建分支、#4 成本归属、#9 半迁移回滚。任务提示的两个候选（“归属/预留/游标/幂等”作为独立片段）逐一核到具体断言行（#4/#5/#7/#6），**不是空泛覆盖**；“UNALLOCATED/待对账保留”= PARTIAL（保留有断言、待对账语义无实例化）。
- **A2：真实进程主张核验** → 成立：spawn 子进程（host-a/b/b2/refused 四进程带 pid/退出码断言）、真实 CURVE、真实 SQLite、跨进程 owner 锁（竞争者与持锁者分属父子进程）；`source_origins` 导入源防漂移断言在父子两侧执行。**未发现进程内 fake 替代**。
- **A3：注释 vs 断言** → ~1638 注释（REG04 改名义务）后实际断言存在且可强制：`payload["ok"] is False`+`code=="SCOPE_DENIED"`×2、事后计数/风险行/投影相等、`len(execution_records)==1`。UNKNOWN“保留”在五个相位均有行级断言（state/permit/attempts），非注释宣示。“调用计数”是真实持久表计数断言（provider_dispatches/risk 表），非空断言。
- **A4：弱点扫描** → 无 skip/xfail/monkeypatch；唯一运行期重绑定是子进程内 `ServiceRegistry` partial（锁 lease 时钟，已披露且合规）；`observation.status` 在 finally 计算 + `_assert_no_fallback_cleanup`，失败路径不可伪装 PASS；`assert` 全集未见恒真项（如 1688 行数==1、1487 registry_created false 均为可证伪强断言）。
- **A5：生产接线核查** → `enforce_migration_start` 唯一生产消费点（service_host.py L344-346 首动作、L782-792）；`record_migration_first_write`/`arm_migration_cutover`/`accept_reverse_migration` **全仓无生产调用者**（实证 grep，仅测试调用）→ F3。incr-A 裁定 F3 已披露该状态，incr-B 未改变（生产零改动）。

## 3 编号发现（findings）

- **F1（阻塞级）——§11.3 :854/:426 正向“持久映射及历史关联”未交付、未测试。** :854 要求对旧 strategy_id、裸 symbol/order_id、进程内 run 与账户配置“建立有冲突检查的持久映射及历史关联”，仅“无法判定归属”的子集才允许保留未分配/待对账。现仓库无任何该映射实现（grep 实证）；两增量生产零改动；drill 对改名路径一律拒绝（SCOPE_DENIED），只证明了负向半项。REG04 第184轮 scope 决定把“构建”正式转记 MIG03 线，且 REG04 的已接受 PASS 在披露(1)中明确不认证映射本身并指向 MIG03；MIG03 汇总行第188轮又记“持久映射构建审计待翻转轮”。翻转轮审计结论：**构建不存在** → 此刻翻转会使该义务无归属、无载体（REG04 已消费了对 MIG03 的依赖假设，recon §3.4 亦建议在导入/映射路径落地前保持 MIG03 部分）。**这不是可披露消化的问题，而是本 case 线内一个 §11.3 合同的零交付片段。**
- **F2（次级）——“未分配/待对账”只落地为 journal scope 级原位保留。** drill 断言未映射事件原位保留、永不重归属/投递（强断言），但没有 UNALLOCATED/待对账状态或对账队列的表示；与 EXE03 已验收的 E/R UNALLOCATED 残差投影无挂接断言。若验收口径为“保留=原位不投递”，需在裁定中**显式记录该读法**；若要求对账状态表示，则缺证据。
- **F3（次级）——“首写后拒绝回旧版”的封存触发无生产调用者。** 闸门 enforcement 已接宿主 start（唯一消费点），但 `record_migration_first_write`/`arm`/`accept` 仅测试调用：COMMITTED→SEALED 依赖人工在首写后调用 API；存在“首写已发生但未封存”的窗口，其间旧部署仍可通过 enforce。incr-A F3 已披露“闸门在 incr-B 前 inert”，incr-B 生产零改动使其**依旧 inert**。若受控迁移接受“操作员 arm/seal 程序”为控制方式，需显式记录；否则应把 recorder 接入真实首写路径（execution 订单写/风险预留/gateway 游标）加一个固定源节点。
- **F4（披露级）——迁移腿采用“同库新 epoch 接管+按原 ID 守恒”表示法，无独立旧库导入器。** 与 recon §3.2 option 2 一致，且 :867 允许脱敏合成状态；但必须携带：本演练不覆盖真实旧 managed 部署数据导入（recon §3.4 建议与 AFI12/AFI13 合并、期间保持部分）。
- **F5（披露级）——证据范围上限。** 合成 provider（durable 超时叶）；无真实账户/provider 切换（:867/附注另计）；无跨机；双 owner 仅覆盖 execution sidecar 锁一路；游标“失效重建”分支未演练；成本归属/人工 book/组腿/AFI 操作键盘点未演练；arm 竞态 F1（check-then-write，异对并发双成功 30/40）沿用 incr-A 披露，enforcement 只按 epoch、不受影响；不带闸门的旧二进制无法被协作拒绝（F2 沿用）。
- **F6（记录级）——证据打包与遗留。** run-00/01/02 原件仅存 `D:/temp/it10mig03b-20261016-r1`（已披露；仓库收录 run-03-final+regressions 子集，00-INDEX.md 全量列哈希）；run-03-final 无 result.json/stderr.txt（review 已注）；工作区存在非本轮脏文件（LOAD01 行尾、strays），提交未包含（已核实 `git status`）。

## 4 覆盖归属：组合 vs 仅第188轮

- **仅第188轮新增**：计划性 drain（服务不停机冻结旧客户端）、旧 writer 活会话 `STALE_EPOCH` 全流程、跨进程真锁双 owner 负例、订单/预留/幂等/游标跨 epoch 守恒与负例、新 epoch 重登记+epoch3 重启、封存后端到端拒绝与封存前回退窗口（含完整旧部署重启）、REG04 改名/未映射负向断言、全相位调用计数。
- **仅 incr-A**：闸门生命周期（arm 幂等/冲突/损坏 fail-closed）、seal 单向与首事实保留、宿主 start 首动作拒绝（零副作用+机读码保真）、跨新解释器持久、无标记 passthrough 参数化回归。
- **必须组合才成立**：片段 10/11/15（回退明确、受控迁移、首写后拒绝回旧版——机制在 incr-A、端到端与宿主接线在 incr-B/两轮共同）；片段 2/8 与既有已验收 REG03/GW04/EXE04 机制组合。

## 5 收敛条件（若要转为 GO-with-disclosures 必须逐字携带的披露）

本审计判 NO-GO。若根裁定坚持翻转，唯一可接受的路径是先补一条**与 REG04 INCR-3 同级的显式 scope 决定**，至少记录以下三点，并逐字写入根裁定（此即 GO-with-disclosures 的最低披露集）：

1. 「§11.3 :854/:426 的旧 strategy_id/裸 symbol/order_id/进程内 run 与账户配置→新 ref/book/账户键**正向持久映射及历史关联的构建**不在 MIG03 本次 PASS 认证范围内；MIG03 仅认证负向半项（改名/未映射不产生第二订单分区、双份额度；未映射原位保留）。该构建保留为 MIG03 线/后续命名轮次的在手义务，触发条件=真实旧 managed 部署身份迁移（P7R/P8）前必须落地；REG04 披露(1) 的依赖自此仅由负向半项承担。」
2. 「‘无法判定归属保留未分配/待对账’在本次认证中的落实口径=gateway journal 未映射 scope 的原位保留、永不重归属/投递（drill 1412-1426/1846-1856）；不含对账状态/UNALLOCATED 挂接的表示；E/R UNALLOCATED 残差以既有 EXE03 接受证据为准。」
3. 「首写封存为受控迁移操作程序（人工 arm→seal→accept），闸门接宿主 start 强制；`record_migration_first_write` 暂无生产调用者；在接入真实写路径前，‘首写已发生未封存’窗口由操作程序以人工核对封堵。」
另如不携带第 1 点，则维持 NO-GO，并以“实现+固定源节点”关闭：映射模块（冲突检查+历史关联）+ 改名解析到同分区/同历史的正向节点 + 无重复额度负例。

## 6 判定

**NO-GO（当前不满足“正式 PASS”翻转）**：验收栏四项与 :784 左栏除“持久映射”外各片段均有真实断言覆盖（多由 incr-A+incr-B 组合成立，第188轮单独覆盖了演练主链），REG04 转记的负向断言确实落地且非空；但 §11.3 :854/:426 的正向持久映射构建为零实现、零测试的未交付片段（F1），叠加 F2/F3 两个待记录的语义缺口。补齐 F1（或按第 5 节逐字携带 scope 决定）并处理 F2/F3 后，可再评翻转。
