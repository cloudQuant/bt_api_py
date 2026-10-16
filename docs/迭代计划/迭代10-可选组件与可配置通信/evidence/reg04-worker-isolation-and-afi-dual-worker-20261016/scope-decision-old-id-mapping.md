# REG04 旧 ID 映射 scope 决定（INCR-3 path-2，2026-10-16）

决定：REG04 验收采用本归档 recon（`.codex-tmp/reg04-recon.md` §3 INCR-3）路径 2——
「显式旧 strategy_id 迁移映射」的**构建**不属 REG04 交付，正式归属 MIG03 线
（正式迭代计划.md:426、§11.3 表行 :854、MIG03 条款 :784）；REG04 对该半项的**证据责任**
仅为其负向性质「不产生双份额度」，由下列已验收闸门与负例承担；边界在此显式记录。
REG04 不得在未记录该决定的情况下宣称 PASS——本文件即该记录（recon §3 原文：
`REG04 cannot be declared PASS with the mapping clause untested unless so recorded.`）。

## 依据（逐条可复算）

1. **映射当前不存在**：SDK 目录无「旧 strategy_id → 新 strategy_ref/book/账户键」显式迁移映射
   实现（recon §1(e) Gap：`bt_api_py/` 中无 service 相关 alias/迁移映射模块）。
   无法对不存在的映射做正向用例。
2. **计划归属**：正式迭代计划.md:426「SDK 目录负责旧 strategy_id 到新 strategy_ref/book/账户键的
   显式迁移映射，不能改名后生成第二份订单分区」；§11.3 表 :854 行「旧 strategy_id、裸
   symbol/order_id、进程内 run 与账户配置 → 建立有冲突检查的持久映射及历史关联；无法判定归属
   保留未分配/待对账，不改名后重新授信或重新下单」；MIG03 条款 :784「按第 11.3 节冻结旧客户端、
   确认旧 writer 失效、迁移订单/归属/预留/幂等/游标、建立新 epoch 并重登记；导入失败/切换前后回退明确｜受控迁移和调用计数；无双 owner、无删除 UNKNOWN，首次新写之后
   无已验收反向迁移则拒绝回旧版」（全引文，｜ 为表格单元界）。三处均将映射构建与历史关联列为迁移工作线职责。
3. **负向承接（均已在 dev 提交且有已接受证据）**：
   - **MIG03 增量A**（`f0731ec2`，归档 `evidence/mig03a-first-write-cutover-gate-20261016/`）：
     首写后反向迁移/回退被 cutover gate 拒绝（fail-closed；`service_migration_gate.py` +443、
     `service_host.py` +24）——关闭「回旧版重新下单/重新授信」这一双份额度路径。
   - **REG04 增量1**（父仓 `6448381f`，4 节点）/ **增量2**（AFI `e9c7ca1d`，2 节点，
     真实 HTTP→SDK→Execution 双 app 进程）：claim 与归属键为精确 scope——第二策略 run →
     DUPLICATE_ACTIVE_RUN；未知/越权 book、account-only 写 → SCOPE_DENIED；跨会话/多 worker
     同一 principal 收敛到**同一稳定人工 book attribution**（不能以临时会话/worker ID 铸造
     新 book/额度，durable 断言）；Monitor 转交保留原 actor/scope 不扩权。
   - **EXE03**（轮 76 正式接受；轮 67 为组件轮、明确未 PASS）：E/R 残差投影 UNALLOCATED
     与策略/人工 owner 分离。
4. **边界（诚实披露）**：本仓无「旧 strategy_id 改名后订单分区/额度」的直接节点；
   该问题恰是 MIG03 映射（含冲突检查）落地时必须解决的。本决定将 REG04 的映射半项
   建立在上述负例与闸门之上，并把映射本身的构建/验证义务显式转记 MIG03 线（下节）。

## MIG03 后续义务（随本决定记录）

当 MIG03 落地持久映射（或其验收轮）时，其测试必须包含负向断言：
旧 ID→新 ref/book/账户键 的映射/未映射行为均不产生第二份订单分区或双份额度；
无法判定归属时保留未分配/待对账（§11.3 :854）。该义务不影响 MIG03 当前「部分」状态。

## REG04 判定（本决定生效后）

| 片段 | 覆盖证据 |
|---|---|
| (a) AFI 多 worker 不占策略单活名额 | 增量1 节点1（宿主级）+ 增量2（真实双 AFI app 进程 + 策略 run 同 owner） |
| (b) 应用/service actor 与策略/人工 book 业务归属分离 | 增量1 节点1 scope 正反例 + 增量2 归属 durable 断言（跨会话/用户单人工 book） |
| (c) Monitor 转交不扩权 | 增量1 节点4（错 scope/route/actor 全拒绝、合法冻结恰一次） |
| (d) 服务间越权预留拒绝 | 增量1 节点2/3（无 TrustedGroupReservationPolicy → 拒绝且零 handler 调用） |
| (e) 策略/人工 book/旧 ID 映射不产生双份额度 | 策略/人工 book：增量1/2 + EXE03；旧 ID 映射：按本决定处理（同 ref 第二 run 与首写后回退路径由第 3 节闸门承担；
改名不同 ref 路径无直接测试，见 §4 边界与 MIG03 义务；构建归属 MIG03） |

四项行为片段 + (e) 的策略/人工 book 半项均已由作者轮 + 两轮独立复核 GO（见本归档
root-adjudication.md、review-incr1-status.md、review-incr2-status.md）覆盖；
(e) 的旧 ID 映射半项依本 scope 决定处理 → **REG04 满足 PASS 前置**。
