# REG04 验收裁定补充——INCR-3 scope 决定与正式 PASS（第184轮，2026-10-16）

本文件补充 root-adjudication.md。第180轮裁定 REG04 为「部分」（增量1+2 已交付复核，
旧 ID 映射 INCR-3 未决）。本轮按 `.codex-tmp/reg04-recon.md` §3 INCR-3 路径 2 采用
显式记录的 scope 决定（`scope-decision-old-id-mapping.md`，同目录），并经独立对抗审计
（`acceptance-audit-r1/audit.md`，独立审计员、只读、未执行 python）复核。

## 对象与锚点

| 对象 | 锚点 |
|---|---|
| scope 决定 | `scope-decision-old-id-mapping.md`，sha256 `ee1c673fecd4e89533a047bd8287bca4bfb98d545bd682cb0dd11492cbdc7445` |
| 独立审计 | `acceptance-audit-r1/audit.md`，sha256 `e1eb30bd9429467fa99f05e1db15003e1175640fb1d1c8aad3a9535b00d6c881`（结论 GO-with-disclosures） |
| 支撑证据（已提交，审计复算哈希相符） | SDK `6448381f`（4 节点）、AFI `e9c7ca1d`（2 节点）、MIG03 incr-A `f0731ec2`、EXE03 轮 76 正式接受 |
| 入库 | scope 决定 + 审计 + 本补充随第184轮 docs 提交 |

## 裁定

- 审计结论 GO-with-disclosures → **REG04 正式 PASS**（第184轮起计入，累计 29/68）。
- 依据：除旧 ID 映射外，全部行为片段均由已提交、哈希相符的测试直接覆盖（两轮独立
  复核 GO + Root 裁定）；映射的**构建**归属 MIG03 线（正式迭代计划.md:426、:854、:784）
  为计划本身的分工；REG04 的映射**证据责任**经显式 scope 决定 + 边界披露收口，满足
  recon §3 路径 2 的原文条件（`REG04 cannot be declared PASS with the mapping clause
  untested unless so recorded.`）。
- 该 PASS 以本文件披露全部随案为前提；披露任何一条失效则本 PASS 需重审。

## 强制披露（审计原文，随本 PASS 记入裁定与证据汇编）

> REG04 PASS 认证范围披露（随本条 PASS 记入裁定与证据汇编）：
> 1. 本条 PASS 不认证「旧 strategy_id → 新 strategy_ref/book/账户键」显式迁移映射本身，也不直接证明「改名不产生第二订单分区/双份额度」：映射的构建与冲突检查负向验证归属 MIG03 线（计划 :426/:854/:784；scope-decision-old-id-mapping.md）。在 MIG03 落地前，改名不同 ref 的路径无直接测试；现有保障仅为精确 scope 键（同 ref 第二 run → DUPLICATE_ACTIVE_RUN）与 MIG03 incr-A 的首写后拒绝回退闸门（f0731ec2，fail-closed）。
> 2. 人工 book「额度」无双计的最强可得证据为：每单恰一次派发/预留、跨 4 会话/2 主体收敛单一人 book、E/R 侧以 EXE03 已接受的 UNALLOCATED 残差分离佐证；owner fixture 未挂载 E/R 预算投影（root-adjudication 披露 2、review-incr2 §7 判 adequate）。
> 3. 本 scope 决定文件须随验收轮提交（当前 untracked），且其 MIG03 后续义务（映射测试必须包含不产生第二份订单分区/双份额度的负向断言，无法判定归属保留未分配/待对账）应同步记入 MIG03 线记录；MIG03 本身仍为 部分。
> 4. 附记：引用细节——REG04 片段(b)省略「业务」；:784 引文以「……」跨两表格单元；「轮 67/76 已接受」中正式接受为轮 76（轮 67 为组件轮、明确未 PASS）；P0 组合「AFI 双 worker + 人工 book」的真实行为已由增量2 验证，其 P0 登记簿记应在验收轮一并核对。

## 验收轮核对与修正（提交前完成）

1. **引用卫生修正（提交前、同一提交）**：片段(b) 补回「业务」；:784 引文改为全引文并
   以 ｜ 标注表格单元界（消除 …… 省略）；「EXE03（轮 67/76 已接受）」改为「轮 76 正式
   接受；轮 67 为组件轮、明确未 PASS」；(e) 行改为「同 ref 第二 run 与首写后回退路径由
   第 3 节闸门承担；改名不同 ref 路径无直接测试」并把闸门作用域与边界披露对齐。
   修正后文件哈希即上表 `ee1c673f…`（披露 4 所列引用细节均已按此收口）。
2. **MIG03 义务转记**：已记入 `迭代10验收状态汇总.md` MIG03 行（第184轮段）；MIG03 仍部分。
3. **P0 组合核对**：「AFI 双 worker + 人工 book」真实行为由增量2（`e9c7ca1d`）验证；
   P0 样例集登记簿记——本轮检索迭代10文档与 evidence，未发现登记文件/条目
   （正式迭代计划.md:538 的登记清单尚无簿记载体），列为待办（P0/CFG 职责线），不阻塞本条。
4. **范围补记（审计 Q4 判非阻碍，随案记录）**：无 rename 专项负例（=披露 1 已覆盖）；
   人工 book「额度」无双计为间接最强证据（=披露 2 已覆盖）；monitor 转交正向保留原
   issuer 仅隐式覆盖（负向不扩权为条款所指）。

## 结论

REG04：**正式 PASS**（第180轮「部分（增量1+2已交付并复核）」状态终结）。裁定链：
本补充 + `scope-decision-old-id-mapping.md` + `acceptance-audit-r1/audit.md` +
既有 `root-adjudication.md`（增量1/2）。
