# CFG06 翻 PASS 独立裁定（r2 正式固定来源轮对抗性核验）

- 裁定者：独立裁定代理（只读；未运行 pytest/未起服务/未做任何 git 写操作/未复制证据件；本文件为唯一写入）。
- 日期：2026-10-16。被核对象：`D:\temp\it10cfghost-formal-20261016-r2\`；live 规格 `.codex-tmp/iteration10-cfg06-qa-spec-r2.json`；冻结 runner `.codex-tmp/iteration10-focused-qa.py`。
- 机读结论：35 项核点全部相符；无阻断发现。

## (a) VERDICT

**GO** — 可将 CFG06 翻为正式 PASS（30→31/68），采用下方 (d) 的含 analytics 措辞之六点披露块，并建议在账本注记 (c-附) 的 round-190 提交事实。

- 前置条件（缺口审计 §7：正式固定来源轮 r2 在最终字节上执行通过）已满足：rspec 由 builder 在最终字节重建（新 id `iteration10-cfg06-qa-20261016-r2`、新目录，输出目录事先不存在），runner 一次性执行（无重试），exit 0，pins 147/147 before==after，运行期 heads 定格不变。
- 审计 §7 其余开放项亦无残留：第 2 项由 root 采纳 2(a) 并已实际执行（analytics 控制面为 8 节点之第 7 节点，6 项全过）；第 3 项随本披露块关闭；第 4 项由披露 (6) 承载。
- 唯一须写入叙事的事实更新（非阻断）：运行结束后父仓 dev 于 14:19:59 +0800 追加提交 `e12a2880`（round 190）；与 147 pins 的交集仅 `tests/integration/test_arb03_dim05_group_partial_fill_service.py`，其磁盘字节仍等于 r2 所钉 bytes（独立重算 147/147 全匹配），故 pins 效力不受影响。

## (b) 七步核验（命令 + 原始证据）

### 1. exit.txt / result.json（PASS）
- `cat exit.txt` → `0`（内容 "0"；3B 系 runner 于 Windows `write_text` 的 \r\n，非异常）。
- result.json：`pytest_exit=0`；`source_count=147`；`sources_unchanged=true`；`heads_unchanged=true`；`evidence_level="FIXED_SOURCE_CFG06_FORMAL_COMBO3_HOST_MATRIX_WITH_POLICY_UNITS_AND_REGRESSION"`。
- 运行期 heads（before==after，逐字）：bt_api_py `ee3b05c2ed81ea27598dab1c60bb6f6d3339324d`（=MIG01 R3 提交，2026-10-16 14:07:46 +0800）；risk `adb6ec58308149faa93c02920b72f98ac3796056`；base `b4aabd1e17b9c7252fa7b4d266932087d99b5517`；execution `f5c030644dea06ce91afbaacf94e11e353538186`；transport_zmq `a397fdb460d2deb22e67ceb4963992ef63dbbb0e`。与宣称逐字一致。

### 2. junit.xml（PASS）
- `testsuite` 属性逐字：`errors="0" failures="0" skipped="0" tests="65" time="40.864" timestamp="2026-10-16T14:10:06.854537"`；`grep -o '<testcase '`=65；`<skipped`=0；`<failure|<error`=0。
- 按 classname 分组（`grep -o 'classname="..."' | sort | uniq -c`）：

| # | 模块 | 通过 | 宣称 | 符合 |
|---|---|---|---|---|
| 1 | unit.test_service_group_policy_admission_gate | 13 | 13 | ✓ |
| 2 | unit.test_service_group_policy_control_plane | 18 | 18 | ✓ |
| 3 | unit.test_service_policy_catalog_control_plane | 5 | 5 | ✓ |
| 4 | unit.test_service_components_group_runtime_bundle | 1 | 1 | ✓ |
| 5 | unit.test_service_execution_group | 11 | 11 | ✓ |
| 6 | unit.test_service_execution_group_wire_reply | 6 | 6 | ✓ |
| 7 | unit.test_service_portfolio_analytics_control_plane | 6 | 6 | ✓ |
| 8 | integration.test_group_policy_host_combo3 | 5 | 5 | ✓ |
| — | 合计 | **65** | 65 | ✓ |

### 3. spec / runner（PASS）
- 输出目录 `spec.json` 与 live `.codex-tmp/iteration10-cfg06-qa-spec-r2.json` sha256 均为 `e680476d238ac57f38f40ab18c407e05fefa1dfac75a20052ac1c28fea73a7f0`（逐字节一致）。
- NODES 恰 8 条，顺序：闸门 / 控制面 / policy_catalog / bundle / execution_group / wire_reply / **portfolio_analytics（第 7 条，位于 wire_reply 与 combo3 之间）** / host combo3；与 command.json argv 末端 8 文件一致。
- spec `repositories` 块 == result.json 运行期 heads；spec `source_sha256` 与输出目录 `source-before.json` 内容全同（`jq -S` diff 相同），147 条。
- `runner.py`（副本 sha256 `8b7f470be…` == 冻结 runner 及 pin 值 `8b7f470be77270adeb9d72a83776b827c4653004b39835eeca0f43bf63cf24ed`）为冻结一次性 runner：断言输出目录不存在（拒绝复用）、断言各仓库在 `dev` 分支且 heads==spec、单次 `Popen` 无重试/无续跑分支、运行后重算 pins 并断言 `files==after and heads_before==heads_after`、落盘 stdout/stderr/junit/exit/result。

### 4. 磁盘重算 + heads/round-190 交叠（PASS，0 失配）
- 从 `source-before.json` 取全 147 条 path+sha256，用 `sha256sum -c -` 对当前磁盘逐一重算：**147 OK / 0 FAILED / 0 失配**。
  （透明注记：首次管道因 jq 在 Windows 输出 CRLF 致 147 条文件名带 \r 而全部 open 失败——系裁定方管道缺陷，加 `tr -d '\r'` 后全绿；与仓库无关，不构成发现。）
- 当前 heads：父仓已从 `ee3b05c2` 前进至 `e12a288037cf9df0c0339e3d494fa1c60e919563`（round 190，2026-10-16 14:19:59 +0800，晚于本轮完成 14:10:48 +0800）；其余 4 仓与运行期 recorded 值完全一致。`ee3b05c2` 为当前 HEAD 祖先，`ee3b05c2..HEAD` 仅 e12a2880 一笔。
- 交叠检查（`comm -12`，两侧统一 LF 后）：round-190 变更文件集 = 2 个 docs evidence 目录（arb02-crashprefirstleg-20261016、arb03-r3-fill-scenarios-20261016，含 junit/review/root-adjudication）+ 2 个 docs `*.md` + `tests/integration/test_arb02_group_budget_service.py`（新增 488 行）+ `tests/integration/test_arb03_dim05_group_partial_fill_service.py`。与 147 pins 的交集 = **恰 1 个**：`D:/bt_api_py/tests/integration/test_arb03_dim05_group_partial_fill_service.py`。
- 该文件 pin sha `2f06291dbed35bc56f54d6e502c001fd3316f9408cf4a0fab615acaff03a033e`；磁盘重算 **OK**（即提交所记录的字节 == 运行所钉字节，提交本身未改变工作区字节，`git status --porcelain` 该文件干净）。据此：提交不改变 worktree 字节的预期成立，pins 效力不被 round 190 破坏。

### 5. stdout / stderr / combo3 历史易飘节点（PASS）
- stdout.log 逐字关键行：`......(65 dots) [100%]` … `65 passed, 2 warnings in 40.87s`；两条 warning 均为 `zmq/_future.py:718 RuntimeWarning: Proactor event loop ... add_reader`（历史同类，无害）。无 abort/retry 标记（单次 pytest 会话、单 summary）。
- stderr.log = **0 字节**（sha `e3b0c442…b855`=空串）——比宣称「stderr empty 除良性警告外」更干净：两条 warning 实际只出现在 pytest stdout 汇总中。
- 时间线自洽：command.started_utc `06:10:06.544554+00:00`（14:10:06.54 +0800）→ junit timestamp `14:10:06.854537` + time 40.864s → result.finished `06:10:48.196901+00:00`（14:10:48.19 +0800）；running.json 单一 pid 77392；`pytest-tmp\` 54 个条目（basetemp 实被使用）。
- combo3 历史易飘节点 `test_combo3_admitted_group_reaches_paired_and_gate_is_not_public`：junit `time="7.955"`（≈8.0s）自闭合 testcase = 真实通过（全轮 skipped=0、无 failure/error 元素）。其余：4.905 / 4.420 / 4.806 / 0.008s。

### 6. 缺口审计与披露块（见 (d)）
- 已全文读取 `D:/temp/it10cfg06-gap-audit-20261016-r1/audit.md`。§7「强制披露文本」中与节点数相关的待替换处 = 第 (1) 条内的两处：「r2（7 节点：group_policy 闸门/控制面、policy_catalog 控制面、bundle、execution_group、wire_reply、宿主组合 3）」枚举，及方括号占位「以 r2〔及按裁定并入的 analytics 控制面节点〕为准」。
- 计划备忘 `cfg06-formal-r2-plan.md` 第 9 行要求「方括号处替换为含 analytics 节点的措辞」→ 已按 root 采纳的 2(a)（analytics 已实际并入 8 节点）适配，见 (d)。

### 7. 前置条件确认（PASS）
- 缺口审计翻 PASS 前置 = r2 正式固定来源轮在最终字节上、一次性、无重试执行通过 → 全部满足（见第 1/3/4 步）。
- 计划门序：MIG01-R3 先提交（`ee3b05c2` = MIG01 R3，14:07:46 +0800）→ 后运行 r2（14:10:06 起），时序正确；时限门 15:05 前完成（14:10:49）。
- 无其它未决前置：§7 第 2 项已以 2(a) 落地关闭；第 3 项随本披露发放关闭；第 4 项仅文书性质（不改冻结文件），由披露 (6) 承载。

## (c) 宣称核验 / 被证伪项

- **无被证伪的实质宣称。** 逐条相符：exit 0、65/0/0/0、40.87s、8 模块计数、NODES=8 及 analytics 位置、147 pins、before==after 文件 sha `7d7e373c…`、heads 五仓值、stderr 空、combo3 易飘节点 8.0s 真过、spec 两副本 sha 一致。
- 措辞级修正（不构成 overclaim）：
  1. 「stderr empty modulo benign warnings」——实际 stderr 为 0 字节；良性 zmq 警告在 stdout 汇总区。
  2. 「heads unchanged」仅指运行窗口内（runner 断言严格成立）；运行结束后 +0800 14:19:59 父仓出现 round 190 提交 `e12a2880`。运行期记录 `ee3b05c2` 属实；因该提交未改变任何 pinned 字节（第 4 步全量重算证明），不影响证据效力，但账本叙事应注记。
  3. 「builder exit 0」在输出目录内无独立退出码工件（builder 自身 stdout 未归档至此目录），系通过 spec 存在性/哈希/runner 独立断言（dev 分支、heads==spec）与 `cfg06-r2-status.md` 互证；低风险文书项，归档时可顺带收 status/report。
  4. exit.txt 为 "0\r\n"（Windows 文本模式），语义即 0。

## (d) 适配后的六点披露块（逐字，可直贴账本）

> CFG06 正式 PASS 披露：(1) 最终字节覆盖——本轮固定来源正式轮为 r2（8 节点/65 项：group_policy 闸门 13、group_policy 控制面 18、policy_catalog 控制面 5、bundle 1、execution_group 11、wire_reply 6、portfolio_analytics 控制面 6、宿主组合 3 5；一次性执行、pytest exit 0、147 pins before==after、运行期 heads 定格 ee3b05c2），r154（170 pins）与 r156（172 pins）回执仅对其固定字节有效；截至定稿日 live 字节已分别有 20/170 与 12/172 pin 失配（含 service_registry.py、service_runtime.py、service_host.py、service_components.py、admission.py 及 execution 组链），相关片段在最终字节上的判定以 r2（已含按裁定并入的 portfolio_analytics 控制面节点）为准。(2) 「请求 publish 绑定 → SCOPE_DENIED」在冻结测试中为注释描述而非断言；r154 实际断言的是 service 出站 client 本地 publish → AMBIGUOUS_OR_MISSING_ROUTE。(3) 跨进程真实独立分析进程属 AFI04 条款（计划 :796），不在本 PASS 认证范围；本 PASS 的 analytics 证据为同进程真实角色切片。(4) 「无任意模块加载」由配置白名单/负例（CFG03、第 96 轮、第 165 轮）支撑，未含运行期任意加载探针。(5) combo-3 staging 复跑 run-a 并发争抢 1 failed 如实保留，正式轮结果为准。(6) INVALID_POLICY_VERSION / POLICY_VERSION_CONFLICT 为 owner 侧 store 产码，以冻结测试断言为准；r165 冻结测试 docstring 漂移（:629-637）保留不改。

（第 1 条中「7 节点」枚举与方括号占位均已按含 analytics 的 8 节点措辞替换；第 2–6 条与审计原文逐字一致。）

### (c-附) 建议随账本注记的裁定级事实（非六点之列）

> 运行后（同日 14:19:59 +0800）根仓 dev 追加提交 e12a2880（round 190：arb02 崩溃先腿与 arb03 组部分成交场景节点及两个 evidence 目录）；与 r2 的 147 pins 交集仅 tests/integration/test_arb03_dim05_group_partial_fill_service.py，独立重算其磁盘字节仍等于 r2 pin 且工作区干净，r2 pins/heads 记录（定格 ee3b05c2）效力不受影响。

## (e) 翻账本提交输入清单

**归档源 → 目标**：自 `D:\temp\it10cfghost-formal-20261016-r2\` 复制（由 root 执行，本裁定未复制）至 `docs/迭代计划/迭代10-可选组件与可配置通信/evidence/cfg06-formal-r2-20261016/`：

| 文件 | 关键 sha256 | 说明 |
|---|---|---|
| spec.json | e680476d238ac57f38f40ab18c407e05fefa1dfac75a20052ac1c28fea73a7f0 | 与 live spec 逐字节同 |
| runner.py | 8b7f470be77270adeb9d72a83776b827c4653004b39835eeca0f43bf63cf24ed | 冻结 runner 自拷 |
| source-before.json / source-after.json | 均 7d7e373c91c1d5fca215583df0f00419cefee015899e5436b11ee1532a23fd11 | byte-identical |
| stdout.log | 710200c89b6c9fa7a187948eb1326498a5aa5652aa57ae80e50d1915562f3600 | 65 passed, 2 warnings in 40.87s |
| stderr.log | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | 空 |
| junit.xml | ceb9f1afa2f789bbc738482b328698ab2258076a8159ee2a68d5a31c8a30e4bf | 65/0/0/0 |
| result.json | —（随包） | exit/heads/pins/evidence_level |
| exit.txt、command.json、running.json | exit.txt 13bf7b30…（内容 "0"） | 过程件 |
| pytest-tmp/（54 项） | — | 可选；如归档策略保留 basetemp 则收，否则注明略 |
| （可选）review 子目录：.codex-tmp/cfg06-r2-report.md、cfg06-r2-status.md | — | 过程报告；建议随包 `review/` |
| （可选）本裁定 .codex-tmp/cfg06-flip-adjudication.md | — | 建议随包复印件或作为 root-adjudication 引用 |

**需更新的 docs 行（root 写作）**：
1. `docs/迭代计划/迭代10-可选组件与可配置通信/迭代10验收状态汇总.md` CFG06 行：状态「部分/本机进程（CFG06b 增量）」→ **PASS（31/68）**，并入 (d) 六点披露块与 (c-附) 注记；行末增 r2 回执指认（artifact 目录、spec sha e680476d、junit 65/65、147 pins before==after、运行期 heads ee3b05c2、裁定文件指针）。
2. `docs/迭代计划/迭代10-可选组件与可配置通信/开发与验收进度.md`：新增「第 N 轮」entry（轮号由 root 排定；注意 190 已被 arb 场景提交占用），建议要点文本：「r2 CFG06 正式固定来源轮在最终字节一次性执行：8 节点/65 项全通过、exit 0、147 pins before==after、heads 定格 ee3b05c2；独立裁定 GO；CFG06 正式 PASS（31/68）。运行后父仓另有 round 190 提交 e12a2880，与 pins 交集 1 文件且字节未变，效力不受影响。」
3. `正式迭代计划.md` CFG06 条款行：无需改动（验收列措辞不变）。

**限制声明**：本裁定未独立重跑 pytest（按任务约束，机器上有 live 演练进程）；运行之真实性由工件内部时间线自洽、pytest 产物（junit/tmp）、五仓哈希与 147/147 磁盘字节级复核共同支撑。

*裁定人：独立裁定代理；2026-10-16；不翻动任何状态，仅供 root 裁定。*
