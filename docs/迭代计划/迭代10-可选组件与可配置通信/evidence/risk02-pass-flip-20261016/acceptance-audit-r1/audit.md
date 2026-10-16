# RISK02 独立对抗审计报告（r1）

- 审计日期：2026-10-16
- 审计对象：`docs/迭代计划/迭代10-可选组件与可配置通信/迭代10验收状态汇总.md:70` RISK02 行（现状态：部分 / 包级合成及既有服务链）
- 条款原文（`正式迭代计划.md:760`）：TTL、UNKNOWN、部分成交、撤单及重启的敞口转换；汇率/乘数/数据过期
- 证据单元要求：规则及完整订单观察链；不凭模拟余额宣称账户通过
- 审计方式：只读（文件读取、`git show/log/diff`、sha256sum）；未运行被测系统；未修改任何仓库文件；除本文件与状态文件外未写任何路径
- **结论：GO-with-disclosures**（可翻转 正式 PASS，须附带第 5 节披露文本）

## 1. 条款分解

| # | 片段 | 含义 | 审计口径 |
|---|------|------|----------|
| a | TTL | permit TTL 到期后不得派发，敞口不泄漏/不虚增，且有可观察转变 |
| b | UNKNOWN | 不确定写入（提交后超时等）的敞口保留、查询与重启语义 |
| c | 部分成交 | PARTIALLY_FILLED/FILLED 下敞口按成交×乘数正确累积；超限拒绝零增量 |
| d | 撤单 | 撤单后敞口释放、重估与幂等 |
| e | 重启 | 进程/会话重启后敞口行、合计、计数器保持与逻辑等价 |
| f | 汇率 | EUR/USD 事实绑定与可信汇率准入（元数据有效期∩汇率有效期） |
| g | 乘数 | 合约乘数在敞口换算中的参与 |
| h | 数据过期 | 汇率/数据过期 fail-closed（拒绝新单、UNKNOWN 查询） |
| cell-1 | 规则 | 风险规则包级链证据（非空跑） |
| cell-2 | 完整订单观察链 | 真实宿主组件 + journal/回复/计数观察断言 |
| cell-3 | 不凭模拟余额宣称账户通过 | 证据明示非真实账户，不得过度宣称 |

## 2. 最强证据定位与核验（含哈希与提交）

### 2.1 r150：`evidence/risk-order-ttl-restart-20261016-r150/`
- root-adjudication.md：接受轮 r5；spec `ccdf572e…`（2094 pins）；pytest exit 0；1 passed 20.26s；三场景（S1 TTL、S2 intent deadline、S3 UNKNOWN+重启）；含 journal `RESERVED→ADMISSION_DENIED` 转移修复；r1–r4 失败保留于 `D:/temp/it10riskttl-root-20261016-r{1..4}`；证据级别含 `…PERMIT_TTL_INTENT_DEADLINE_UNKNOWN_RESTART_NOT_REAL_ACCOUNT`。
- qa/spec.json = qa/authored-spec-r5.json = `ccdf572e…`（2094 pins）；qa/result.json pins=2094 未变，已记录 stdout 哈希 `f677cd5a…`；qa/stdout.log "1 passed in 20.26s"；source-before=source-after=`f94f3076…`（2094 键逐键相等）；junit 1/0/0/0，19.989s；command.json cwd `D:\temp\it10riskttl-candidate-r6`。
- source/：entry `89b2eade…`、journal `f10c2e89…`、测试 `466e42e8…`，三者经独立 sha256sum 复核与提交 `b712a45e` 字节一致。
- 提交核验：`b712a45e`（父 `833b101c`）diff 恰为一行——`"RESERVED"` 转移集合加入 `"ADMISSION_DENIED"`；父提交缺该转移（真实修复）；现 HEAD journal 第 3204 行含该转移。
- 现 HEAD 的 entry（`67c98d5b`，接受版 `89b2eade`）中 deadline/canonical_payload 检查、`_release_unclaimed_and_record_denial`（reason=DEADLINE_EXPIRED）、释放容忍 `PERMIT_NOT_ACTIVE`（否则 mark_unknown）逻辑原样存在。
- 测试要点（`466e42e8`，827 行）：S1 `_PermitTTLClock(expire_on_call=9)` → ADMISSION_DENIED/PERMIT_NOT_ACTIVE、expiry_sweeps==1、provider_writes==()、计数器 0,0、RESERVED 计数 1、无 CLAIMED/DISPATCHING；S2 intent deadline 仅写 canonical_payload、不在 provider order_request（`deadline_ns` 缺失）；S3 提交后 TimeoutError → UNKNOWN、敞口冻结于 USD150、同 logical_run_id 新会话重启、claim CLAIMED、provider 写入数保持 1、查询 incomplete→UNKNOWN。

### 2.2 r146：`evidence/risk-fx-applied-exposure-restart-20261016-r146/`
- 提交 `2f9d6a99`（父 `c1d9d692`）；source-assessment.json：entry `8ad906d5…`/151781B、journal `7edc4281…`/231705B、测试 `814d37fe…`/47242B，与提交字节一致。
- source-postcommit.json：host-v8 spec `b167c746…`（1962 pins、repositories {}）、result `54ea7005…`、junit `c48a7376…`（1/0/0/0，21.154s）、stdout `30ebe352…`、stderr 空；direct-v7 result `4dadd268…`、junit `ade2f2e9…`；归档文件哈希独立复核一致。
- history/：v5 失败 1、v6 失败 1、v7-host 失败 1（失败历史保留）。
- 测试要点（`814d37fe`，1122 行）：0.5@20EUR×3×1.25 → 37.5/112.5/150；超限拒绝零增量；重启后陈旧汇率保留行/合计/计数器；陈旧汇率新提交 ADMISSION_DENIED、陈旧查询→UNKNOWN、EXPOSURE_APPLIED 计数 1；新鲜 1.30 → CANCELLED filled 39、leaves 0、重复撤单幂等；fee=0（已披露缺口）。

### 2.3 r134：`evidence/risk-order-exposure-service-20261016/`
- 提交 `b3b2c6d6`（父 `a404512f`）；result.json pins=368、exit 0；junit 1 passed 9.853s；candidate-overlay-provenance.json：entry `dea50da0…`、journal `68389c09…`、exposure test `56a7e228…` 均与提交一致；nohost-r19 2 passed。

### 2.4 r138：`evidence/risk-trusted-fx-admission-20261016/`
- risk-postcommit.json 6 路径含 admission.py `8733c5e9…`；独立复核 `8733c5e9` = blob `c6fcacda`（LF）→ CRLF 转换结果，即 r150 运行于 risk `c6fcacda`。r2 25 用例 23p/2f、r3 3 用例 2p/1f、r4 1/1p/0f（无 25/25 单轮，已披露）。

### 2.5 规则层与完整订单观察链
- risk 包级链 r43→r138 及后续子模块提交 `80fa67fb..adb6ec58` 均存在；risk HEAD=`adb6ec58`。
- r134/r146/r150 宿主链 = 真实 ServiceHost/CURVE/ServiceClientRuntime/ServiceExecutionClient/ZMQ + 真实 Risk 网关（LIVE_GROSS）/journal（SQLite）+ 合成 provider 叶节点；证据级别均含 `NOT_REAL_ACCOUNT`。

## 3. 对抗性证伪结果

| 证伪假设 | 检查 | 结果 |
|---|---|---|
| 死代码/永真断言 | 三个测试文件断言均为 fail-loud（assert，无 skip/xfail） | 未发现 |
| fixture 即 oracle（自证） | 观察链断言 journal 转移、计数器、回复码、provider 写入元组 | 通过（非自证） |
| UNKNOWN 被删除 | r150 S3 与 r134 均断言 UNKNOWN 保留 150、写入数不变 | 通过 |
| 重启后重派发 | provider 写入元组不变量（保持 1）成立 | 通过 |
| 过期汇率未 fail-closed | r146 陈旧汇率：新提交 ADMISSION_DENIED、查询 UNKNOWN、EXPOSURE_APPLIED 计数 1 | 通过 |
| 撤单非幂等/泄漏 | r146 重复撤单幂等、leaves 0 | 通过 |
| journal 转移被"修饰" | `b712a45e` 恰一行；父提交缺该转移（真实修复） | 通过 |
| 调度绑定探针有副作用 | dispatch_claim_binding 只读 | 通过 |
| 过度宣称（账户通过） | 全部裁决明示 NOT_REAL_ACCOUNT，无"账户通过"字样 | 通过 |
| 费用非零换算 | 测试 fee=0，宿主链未验非零费用 | 残差（披露） |

## 4. 新鲜度（相对现 HEAD）

- 审计期间 HEAD 移动：`4adce9b5`（任务陈述，risk gitlink `f56af2b`）→ `b06815a1`（risk gitlink→`adb6ec58`）→ `a3044673`（round 185 记录）；entry/journal/test 三个被依赖文件哈希在移动前后不变（`67c98d5b`/`37d89c33`/`466e42e8`）。
- 后置提交对依赖文件的修改经核等价：`bb762465`（DIM01，身份冲突闩码/`trade_id_domain` 增补、average_price 回包，附加型）、`ed09b3ca`（r166，"同 run 释放重绑路径与 HEAD 等价"）、`336203d2`、`e2bedb3`（r167，"resource=None 时全路径与 HEAD 字节等价"）、`adb6ec5`（legacy REDUCE 原样保留，round-185 记录）。
- 子模块 gitlink：base `b4aabd1e` 锁定无漂移、gateway `59b164ab`、monitor `536a4f04` 无漂移；transport `a397fdb4`（对锁定 `29ab21e7` 漂移 1 提交）、execution `6e89d306` 漂移。风险准入路径不导入 execution/gateway，漂移对本条款证据无实质影响（披露）。
- 三个宿主节点（r134/r146/r150）未在 r160/r166/r185 之后重跑 → 建议翻转轮于现 HEAD 重跑闭口（披露）。

## 5. 裁决

**GO-with-disclosures** —— 八个片段与三项证据单元要求均有已接受证据覆盖；对抗性证伪未发现矛盾；残差仅为披露级（合成范围、宿主节点新鲜度、费用非零、子模块漂移）。

### 披露文本（建议随翻转写入汇总/裁决，REG04 风格）

> RISK02 独立对抗审计（r1，2026-10-16）判定为 GO-with-disclosures，翻转 正式 PASS 时须附带以下披露：
> 1. 全部证据基于合成 provider 叶节点与真实 ServiceHost/风险网关(LIVE_GROSS)/journal/SQLite 链，属包级与既有服务链验证，不构成真实账户验收；本条款证据不凭模拟余额宣称账户通过。
> 2. r150 已闭合 r146 遗留缺口（TTL 到期 PERMIT_NOT_ACTIVE→ADMISSION_DENIED；intent deadline 仅写入 canonical_payload 且不进入 provider 请求；UNKNOWN 跨重启保留敞口且不重派发），并含 journal `RESERVED→ADMISSION_DENIED` 转移修复 `b712a45e`。
> 3. 被依赖逻辑在现 HEAD 经核等价（DIM01 增补 `bb762465`；r166 同 run 等价 `ed09b3ca`；r167 resource=None 字节等价 `e2bedb3`；`adb6ec5` legacy REDUCE 原样保留），但三个宿主节点（r134/r146/r150）未在其后重跑；建议翻转轮于现 HEAD 重跑该三节点闭口后落账。
> 4. 失败历史均保留（r1–r4、r138 r2–r4、r146 v5–v7）；费率非零换算在宿主链未验（测试 fee=0）；transport/execution 子模块 gitlink 存在漂移，风险准入路径不导入对应模块，不受影响。

## 6. 逐片段证据表

| 片段 | 最强证据（归档） | 提交/哈希 | 状态 | 实质性 |
|---|---|---|---|---|
| (a) TTL | r150 S1 宿主；r68 包级 | `b712a45e`；spec `ccdf572e`(2094 pins)/source `f94f3076` | 覆盖 | 高 |
| (b) UNKNOWN | r150 S3；r134 宿主；r68 | `b712a45e`/`b3b2c6d6` | 覆盖 | 高 |
| (c) 部分成交 | r146 V8；r134；r68/r83 | `2f9d6a99`/`b3b2c6d6`；test `814d37fe` | 覆盖 | 高 |
| (d) 撤单 | r134 全撤；r146 重估/重复撤单 | `b3b2c6d6`/`2f9d6a99` | 覆盖 | 高 |
| (e) 重启 | r146 V8；r150 S3；r68 | `2f9d6a99`/`b712a45e` | 覆盖 | 高 |
| (f) 汇率 | r146 V8；r138 包级 | `2f9d6a99`；`8733c5e9`(=blob `c6fcacda`) | 覆盖 | 高 |
| (g) 乘数 | r146 V8 ×3；r68/r83 | `2f9d6a99` | 覆盖 | 高 |
| (h) 数据过期 | r146 陈旧汇率 fail-closed；r150 TTL/deadline | `2f9d6a99`/`b712a45e` | 覆盖 | 高 |
| cell-1 规则 | risk 包级链 r43→r138；子模块 `80fa67fb..adb6ec58` | 均存在 | 覆盖 | 高 |
| cell-2 完整订单观察链 | r134/r146/r150 宿主链（真实组件+合成 provider 叶） | 见上 | 覆盖（合成叶限定） | 高 |
| cell-3 不凭模拟余额 | 所有裁决明示 NOT_REAL_ACCOUNT；无过宣称 | — | 覆盖 | 高 |

## 7. 审计轨迹（只读操作摘要）

- `git show/log/diff`（只读）：`b712a45e`、`2f9d6a99`、`b3b2c6d6`、`bb762465`、`ed09b3ca`、`e2bedb3`、`adb6ec5`、`a397fdb4`/`29ab21e7`、`6e89d306` 等；
- sha256sum 复核：r150 source/ 与 qa/、r146 source/ 与 source-postcommit、r134 provenance、r138 6 路径；
- 未运行被测系统；未做任何 git 写操作；仅写入本文件与 `D:/bt_api_py/.codex-tmp/risk02-audit-status.md`。
