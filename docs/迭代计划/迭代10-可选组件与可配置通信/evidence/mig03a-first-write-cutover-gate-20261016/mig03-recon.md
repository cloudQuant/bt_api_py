# MIG03 只读侦察报告（迭代10，branch dev，2026-10-16 状态基线下）

范围限定：本轮只侦察，不改任何仓库文件、不提交、不跑测试。本文件是唯一写入。

---

## 0 结论摘要

- MIG03 原文（`D:\bt_api_py\docs\迭代计划\迭代10-可选组件与可配置通信\正式迭代计划.md:784`）："按第 11.3 节冻结旧客户端、确认旧 writer 失效、迁移订单/归属/预留/幂等/游标、建立新 epoch 并重登记；导入失败/切换前后回退明确 | 受控迁移和调用计数；无双 owner、无删除 UNKNOWN，首次新写之后无已验收反向迁移则拒绝回旧版"。
- 其大部分构件已有**组件级/相邻用例的已验收证据**：REG03（第98轮，新 epoch+显式 recover+原 UNKNOWN 可读）、GW04（第40轮，持久 owner/epoch、旧代次拒绝、provider 零调用）、EXE04（第59轮，旧 writer 代次零 provider 调用）、EXE02（第47轮，UNKNOWN 同 ID 不重派）、GW03（第50+08轮，服务重启后流 epoch/ACK 保留、过期游标从 snapshot+tail 重建）、MIG02（第145轮，旧 wire/旧配置拒绝）。
- **真正缺失的是 MIG03 形态的"一次受控迁移演练"本身**：drain 旧客户端→确认旧 writer 失效（需可验证的进程/路由/会话事实，不能只用租约过期推定，见 11.3 步骤3）→导入订单/归属/预留/幂等/游标→新 epoch 重登记→调用计数→首写后拒绝回退。现无该整合证据，也无"首写后拒绝回旧版"的机器闸门。
- 第 11.3 节明确允许 MIG03 用"脱敏合成状态与实际进程"完成成功/故障边界（`正式迭代计划.md:867`），真实 provider 切换另计。**本地范围原则上有解**：一次中等规模的受控两 epoch 演练即可，不必真实多机或真实账户。
- 但**不能靠引用现有用例直接关闭 MIG03**；建议按 MIG02 的先例（既有已验收证据 + 一个新增固定进程节点 + Root 裁定）补一个专门演练节点。

---

## 1 第 11.3 节合同摘要（原文关键规则 + 行号）

主计划 `D:\bt_api_py\docs\迭代计划\迭代10-可选组件与可配置通信\正式迭代计划.md`：

- L847：P0 登记发行矩阵；"握手不兼容在注册前拒绝，存储格式不兼容在写入前拒绝"。
- L852：旧 forwarding/GatewayConfig/旧 wire → 离线转换或**具体拒绝**，不保留兼容监听器/导入壳。
- L853：managed_live_direct/managed_live_gateway 等封闭旧装配 → "显式迁移原身份、必需能力、准入/恢复及授权到新服务合同；无法保持约束就拒绝在新版本启动或保留完整旧发行部署"。
- L854：旧 strategy_id、裸 symbol/order_id、进程内 run 与账户配置 → "建立有冲突检查的持久映射及历史关联；无法判定归属保留未分配/待对账，不改名后重新授信或重新下单"。
- L858：每次切换以真实账户及共用会话/资源池为边界，附带关联组协调者和全部消费者清单。（MIG03 本轮用脱敏合成状态，见 L867。）
- L860 步骤1：盘点 G 会话/快照、E 订单/成交/成本归属/人工 book/UNKNOWN、R 预留/预算/冻结/政策、组腿/期限、各 outbox/checkpoint、AFI 操作键/审批关联；不可安全转换的状态保持原部署且标记未迁移。
- L861 步骤2：停止该范围新增意图和配置变更，暂停旧策略/AFI/协调写客户端，保留授权查询和已接受订单的处置；"未决责任必须有明确接管方案"。
- L862 步骤3：核实旧 writer 外部写入已失效、旧会话交接/关闭，记录可验证的进程/路由/会话事实；"**租约过期、心跳消失、修改目录指向都不能独自证明 fencing**。无法确认则不启动新 writer，不提供自动接管"。
- L863 步骤4：离线、未派发状态导入/核对账本与引用；源 cursor/epoch 只在原流仍有效时沿用，否则从有来源的新快照重建并保留事件/业务去重关联；更名不重置订单号/已用额度/原始意图/UNKNOWN；无法重建的成本/历史标不完整、**不填零**。
- L864 步骤5：新 owner 取得新 writer epoch，恢复并与 provider 事实核对；客户端用同一已验证发行组合重新登记，旧会话/旧代次拒绝；先核实新读投影/政策/控制再放开新增写入；按账户汇报切换进度，不假装全系统原子升级。
- L865 步骤6："新 owner 尚未产生新的业务写入时，只有确认它停用、源状态未变且原组合完整可恢复才可回退。**首次新派发、预算/归属等状态写入后，必须有已验收的反向状态转换、重新对账及 writer 交接，才可恢复旧版本；没有该方案则止增并向前修复，不能直接运行旧二进制读取新库**。已发生的交易不可通过部署回退撤销。"
- L867：**"MIG03 使用脱敏合成状态与实际进程完成上述成功/故障边界；真实 provider 切换另需对应已授权范围的证据。"**（本文件不执行 drain/fencing/账户/retained-host 操作。）
- L869：已有 AFI paper 账户按同一单 writer 切换原则处理（旧模拟账本为源、守恒核对、未决模拟单不重复成交或显式取消、不可等价转换则拒绝、旧账本只读归档）。
- L821：迁移期间每账户/每组行情只运行一个已选 owner，**不为对比同时登录旧、新写会话**；"未解决的 UNKNOWN 不通过删库重建消除"；无法安全迁移的部署停留旧版本标记未迁移；回退是完整版本受控切换。
- L242：managed 旧合同的历史语义在旧发行版本保留；目标发行版按 11.3 明确迁移或拒绝旧受管 profile。

MIG03 验收证据类型（L784 右列）：受控迁移 + 调用计数；无双 owner；无删除 UNKNOWN；首写之后无已验收反向迁移则拒绝回旧版。

---

## 2 子需求覆盖表

状态：**已覆盖（组件/相邻用例级）｜部分｜缺失**。"正式 PASS"指已在 `迭代10验收状态汇总.md` 关闭的用例。

| # | 子需求 | 状态 | 已有证据（轮次/文件:行） | 精确缺口 |
|---|---|---|---|---|
| A | 冻结旧客户端 | 部分 | 机制：registry 重启 epoch 后删除旧 epoch 全部会话并停用 strategy write claim（`bt_api_py\service_registry.py:747,958-969`）；旧 epoch 绑定在 handler 前拒绝 STALE_EPOCH（同文件 `:2013,2086`）；SDK `recover()` 显式重登记（`bt_api_py\service_runtime.py:828-849`）。证据：REG03 第98轮（evidence/reg03-durable-registration-20261015/root-adjudication.md，STALE_EPOCH+显式 recover）；TRN03 第99轮（旧 host 端点 ZAP 撤权、旧 A SUB 重连拒绝、新登记 REQUEST_NOT_SENT）；MIG02 第145轮（旧 wire/旧配置拒绝） | 无"计划性 drain"证据：在服务不停机/有计划边界下暂停旧策略/AFI/协调写客户端（11.3 步骤2），并保留已接受订单处置；现只有"重启换代"这一种冻结方式 |
| B | 确认旧 writer 失效 | 部分 | 机制：gateway writer authority 单调 epoch，上一 epoch 有未决 action 则拒绝接管（WRITER_LEASE_HELD / WRITER_ACTIONS_UNRESOLVED，`bt_api\bt_api_gateway\src\bt_api_gateway\writer_authority.py:100,143-176`，revoke `:252-282`，mark_unknown `:501`）；execution store 单调 fencing token 租约（`bt_api\bt_api_execution\src\bt_api_execution\store.py:17942-18023`：活跃他人→WriterLeaseUnavailable，过期→token+1）；risk journal writer_fence（`bt_api_py\service_risk_order_journal.py:258`）。证据：GW04 第40轮（持久 owner/epoch、旧代次拒绝、provider 零调用）；EXE04 第59轮（旧 writer 代次零 provider 调用）；REG03（注销/租约过期不清业务事实）；ARB02 第152轮（派发中租约失效→UNKNOWN(GROUP_OWNER_FENCE_UNAVAILABLE)） | 11.3 步骤3 要求**可验证的进程/路由/会话事实**，且明确"租约过期/心跳消失不能独自证明 fencing"；现无任何工具/证据记录这类事实，也无"无法确认→不启动新 writer"的启动闸门；无"停旧 writer→收购新 epoch"的顺序化演练 |
| C | 迁移订单 | 部分 | 机制：直接执行会话 journal 原子切迁 `migrate_execution_journal`/`migrate_legacy_execution_journal`（`bt_api_py\_execution_session.py:1970-2104`：源锁+freezing epoch、claims/quarantine、远端 reconcile、staging→publish no-replace、PREPARED/COMMITTED 事务、未提交回滚 `:1931-1967`）；契约测试 `tests\bt_api_contract\test_execution_session.py:1265-1773`（迭代07，161 passed）。CTP 旧订单号映射导入 `CtpOrderRefSeedProof.legacy_mappings`（store.py `:961,10286`；`bt_api\bt_api_execution\MIGRATIONS.md` 0.2.0 合同）。服务侧：EXE01/EXE02（同 ID 重放/崩溃不重派） | 该 journal 切迁面向**直接会话 JSONL**；没有把旧 managed 部署订单/UNKNOWN 导入四包服务 store 的已验收证据；MIG03 无演练节点 |
| D | 迁移归属 | 部分/偏缺 | 已有：account-books 策略/人工归属与 UNALLOCATED 残差（EXE03 第67/76轮）、AFI 持久操作键→intent 映射（第129/149/151轮）。DIM01 身份碰撞 WIP 在 checkout（RISK02 第150轮注明排除） | 无旧部署策略/人工 book 归属迁移；11.3 L854 的"有冲突检查的持久映射（旧 strategy_id/裸 symbol）"无证据；无"无法判定归属保留未分配/待对账"反例 |
| E | 迁移预留 | 部分 | REG03：注销、租约过期、重启后风险占用/claim/freeze 全程保持、原 intent 可查、provider 只派发 1 次；RISK02 第146/150轮：派发后 UNKNOWN 跨 TTL/重启保持 USD150 冻结不重派 | 无旧预留/预算/冻结状态导入新 risk store 的工具与守恒核对（11.3 步骤1/4） |
| F | 迁移幂等 | 部分 | registry 幂等登记（SQLite）；EXE01 幂等/异内容冲突；AFI 操作键持久映射（第129轮跨进程只派发一次） | 无旧部署幂等键/记录的迁移；11.3 步骤4 "保留事件/业务去重关联"无演练 |
| G | 迁移游标 | 部分 | GW03 第50+08轮：消费者进度按 principal/consumer/view 隔离，含 stream_epoch+offered/acknowledged cursor；服务重启 service_epoch 变化而流 epoch/ACK 保留、同 actor 新 run_id 续读；过期游标从 snapshot+tail 重建且超 head ACK 拒绝（`bt_api\bt_api_gateway\src\bt_api_gateway\event_journal.py:2127` stream epoch 校验） | 无旧 forwarding 消费基线/checkpoint 导入新 gateway 的演练；11.3 L863 "源 cursor/epoch 只在原流仍有效时沿用，否则重建"未作为迁移动作被验证 |
| H | 新 epoch + 重登记 | 已覆盖（组件级） | REG03 第98轮：新 host epoch≠旧，旧绑定 STALE_EPOCH，显式 recover() 重登记、原 UNKNOWN ID 可读、派发数 1、撤单 0；机制 `service_registry.py:718,2013,2086`+`service_runtime.py:828`。证据 evidence/reg03-durable-registration-20261015/ | 未与"旧 owner 被冻结/导入"串成迁移故事；作为 MIG03 证据可复用（MIG02 先例允许组合已接受证据） |
| I | 导入失败/切换回退明确 | 部分 | 机制：journal 切迁仅未提交可回滚、COMMITTED 后恢复只前进（`_execution_session.py:1970-1990` docstring + `:1612` 恢复测试）；freeze 记录 BLOCKED 语义；"destination_exists→拒绝" | **无实现 11.3 步骤6**：首写之后拒绝回旧版的闸门（如 cutover/epoch 标记 + "新库已有新写即拒绝旧二进制/旧库"检查）；无导入失败（半迁移）在服务 store 层的具体回退演练 |
| J | 受控迁移 + 调用计数 + 无双 owner + 不删 UNKNOWN | 部分 | 调用计数：合成 provider 计数在 REG03/GW04/EXE04/ARB02 广泛使用；无双 owner：GW04（WRITER_LEASE_HELD、旧代次零调用）、ARB02 双进程竞争；UNKNOWN 保留：REG03 原 ID 可读、EXE02 同 ID 不重派、gateway mark_unknown | 这三项**从未作为一次迁移演练的合并证据**出现；MIG03 验收的就是这条整合证据 |

参考先例：MIG02 第145轮 = 已接受 CFG05 分段证据 + 1 个新增真实本机节点 + Root 组合裁定（`evidence\mig02-old-wire-20261016\root-adjudication.md`；进度 `开发与验收进度.md:3527`）。

---

## 3 最小可行增量评估

### 3.1 本地受控演练能否满足证据类型？

**能，在原则上。** 依据 `正式迭代计划.md:867`：MIG03 明文允许"脱敏合成状态与实际进程"覆盖成功/失败边界；真实 provider 切换是 MIG03 之外另计的验收。因此不需要真实多机、真实账户、真实 provider。证据类型（受控迁移+调用计数；无双 owner；无删除 UNKNOWN；首写后拒绝回退）都可在本机两个真实 spawn 进程 + SQLite + 合成 provider 上实现。

### 3.2 提议的演练内容（两 epoch 受控迁移）

场景骨架（复用现有机制，集中在服务 store 层）：

1. **epoch1（旧 owner）**：真实 spawn ServiceHost A + CURVE + SQLite（Execution/Risk 库）；旧客户端登记 → 提交 1 单（合成 provider 注入 UNKNOWN）→ 产生：UNKNOWN 订单 + 风险预留/freeze + 归属事实 + 幂等键 +消费者游标推进。记录 provider 派发计数=1。
2. **冻结旧客户端**：正常关闭 A（drain）；新 host B 以同库启动 → 旧绑定查询 STALE_EPOCH（REG03 机制）；旧 wire 帧被拒（复用 MIG02 端点反例，可选）。
3. **确认旧 writer 失效**：旧 writer lease 无法再派发（fencing token/epoch 拒绝，GW04/EXE04 机制）；显式核对"无第二位 owner"（WRITER_LEASE_HELD / WriterLeaseUnavailable）且 provider 计数相对基线零增长；记录进程/会话事实（本机 PID/退出码/端点）。
4. **迁移**（MIG03 当前的真实缺口）：
   - 订单/日志腿：对旧 JSONL journal 用已有 `migrate_execution_journal` 做原子切迁（源锁、claims、远端 reconcile、publish、回执），或对服务 store 以"同库新 epoch 重开 + 逐项按原 ID 核对"表示（服务世界无独立旧库格式时）。
   - 预留/幂等/归属/游标：按原 intent/order/consumer 身份逐项核对守恒（无删除、无填零、无改名重置）。
5. **新 epoch + 重登记**：B 取得新 epoch，客户端显式 recover() 重登记（REG03 现成）；旧会话/旧代次拒绝。
6. **回退闸门**：
   - 首写前：允许受控回退（旧版本完整组合可恢复）。
   - **首次新派发/归属写入后**：断言"拒绝回旧版"路径生效——这是需要新增的最小生产闸门，建议做成切迁标记文件（形似 journal 切迁的 COMMITTED transaction + `destination_exists` 拒绝语义）或 host 启动时的"目标库已有新 epoch 写入即拒绝源部署再启动"检查；并留"无已验收反向迁移"的反例测试。
7. **调用计数与结论断言**：全场景 provider 计数、无第二 owner、原 UNKNOWN ID 全程可读、回退拒绝码。

### 3.3 规模与改动面

- 规模：**中等**（约 2–3 轮；1 个集成测试节点为主 + 1 个小的生产闸门）。
- 需要新建/改动：
  - 新集成测试（建议 `tests/unit/` 下 MIG03 专用文件，复用 REG03 `executed-test.py` 的 spawn/关闭断言模式与 `tests/unit/test_service_execution_integration.py`、`test_service_group_registration_lifecycle.py` 的 host 装配 helper）。
  - 小生产增量（仅若采纳回退闸门）：切迁/epoch 标记与"新库已有新写→拒绝源部署续跑"的检查；可参考 `bt_api_py\_execution_session.py` 的 freeze/transaction 记录格式，而不新造框架。
- 参与的 store：SDK Registry（epoch/registration）、Gateway writer authority + event journal（writer epoch、消费游标）、Execution store（订单/归属/写租约）、Risk 预留/claim/freeze。
- 可复用：`migrate_execution_journal`（订单日志原子切迁）、REG03/GW04/EXE04/EXE02 的既有机制与断言模式；MIG02 的组合裁定先例。
- 不建议：真实多机、真实 provider、旧 forwarding 真实写会话对接——超出 MIG03 本地证据边界（L867），且属 DIST/VENUE 条目。

### 3.4 若坚持"迁移真实旧部署数据"

如果验收方要求把真实旧 managed 部署（旧 CTP journal、旧 forwarding 消费基线、AFI 操作键/审批）导入四包 store 并做守恒核对，则缺一个真正的导入器（order/ownership/reservation/idempotency/cursor 到服务 store 的映射与冲突检查），规模升级为**大**（多轮），应拆分为"MIG03 本地受控演练（先做）+ 旧部署数据导入证据（延后，可与 AFI12/AFI13 迁移合并）"，并把 MIG03 保持部分状态。

---

## 4 诚实裁定

- **MIG03 在本迭代本地范围原则上可关闭**（第 11.3 节 L867 明文允许脱敏合成状态+真实进程），但**不能仅凭现有已验收证据直接关闭**：MIG03 的证据类型要求的是一条整合的"受控迁移+调用计数+无双 owner+不删 UNKNOWN+首写后拒绝回退"证据，而当前这些要素分散在 GW04/EXE04/EXE02/REG03/GW03/MIG02 等相邻用例中，迁移叙事（drain→fence→导入→新 epoch→重登记→回退拒绝）从未实际执行。
- **缺失的机器**（按优先级）：
  1. 首写后拒绝回旧版的闸门（无实现；11.3 步骤6 只有文字合同）；
  2. 旧的订单/归属/预留/幂等/游标导入服务 store 的路径（现有 `migrate_execution_journal` 只覆盖直接会话 journal；CTP legacy mapping 只覆盖 CTP 订单号）；
  3. 旧 writer 失效的"可验证进程/路由/会话事实"记录与"无法确认则不启动"闸门。
- **建议**：把 MIG03 保持"未开始/部分"，排一个**中等规模、2–3 轮**的专用演练（§3.2/3.3），成功后按 MIG02 先例做 Root 组合裁定关闭；若组织上无法在本迭代给 2–3 轮预算，则显式记为延期，理由="整合迁移演练与回退闸门尚未实现，而非仅缺一次运行"。
