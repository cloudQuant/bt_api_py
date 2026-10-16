# CFG06 正式 PASS 翻转 — 根裁定（第193轮，2026-10-16）

- 翻转依据：CFG06 缺口审计（`D:/temp/it10cfg06-gap-audit-20261016-r1/audit.md`）判 GO-with-disclosures，前置条件 = 在最终字节执行 r2 正式固定来源轮；root 采纳审计建议 2(a)，将 `tests/unit/test_service_portfolio_analytics_control_plane.py`（6 节点）并入 r2 节点集（8 节点）。
- 执行事实（冻结一次性，无重试）：builder 重建 spec（新 id `iteration10-cfg06-qa-20261016-r2`；断言输出目录/Target 事先不存在）→ 冻结 runner（sha `8b7f470b…`）一次性执行输出至 `D:\temp\it10cfghost-formal-20261016-r2`：
  - pytest exit 0、`65 passed, 2 warnings in 40.87s`；junit 65/0/0/0；stderr 0 字节；
  - 8 模块：闸门 13 / 控制面 18 / policy_catalog 5 / bundle 1 / execution_group 11 / wire_reply 6 / portfolio_analytics 6 / 宿主组合 3 5；
  - 147 pins before==after（source-before == source-after，`7d7e373c…`）；五仓 heads 运行期定格（父 ee3b05c2 = MIG01-R3，14:07:46 +0800）；
  - combo3 历史易飘节点 `test_combo3_admitted_group_reaches_paired_and_gate_is_not_public` 7.955s 真过（skipped=0）。
- 独立翻 PASS 裁定（`review/cfg06-flip-adjudication.md`）：**GO**——35 核点全相符、零阻断；磁盘 147/147 复算 0 失配；spec 两副本（live/输出）sha `e680476d…` 逐字节一致；时间线自洽（command 14:10:06.54 → junit 14:10:06.85 + 40.864s → finished 14:10:48.19）。措辞级修正：stderr 实际 0 字节（比宣称更干净）；「heads unchanged」限运行窗口内（运行后 14:19:59 round-190 提交已注记）；「builder exit 0」以 spec 哈希/runner 断言/status 互证。
- 裁定：采纳 GO——**CFG06 翻正式 PASS，累计 31/68**；六点披露块（含 analytics 8 节点措辞）随账本；裁定级注记（round-190 提交与 pins 交集 1 文件、字节未变）随账本。
- 六点披露块（逐字，同账本）：

> CFG06 正式 PASS 披露：(1) 最终字节覆盖——本轮固定来源正式轮为 r2（8 节点/65 项：group_policy 闸门 13、group_policy 控制面 18、policy_catalog 控制面 5、bundle 1、execution_group 11、wire_reply 6、portfolio_analytics 控制面 6、宿主组合 3 5；一次性执行、pytest exit 0、147 pins before==after、运行期 heads 定格 ee3b05c2），r154（170 pins）与 r156（172 pins）回执仅对其固定字节有效；截至定稿日 live 字节已分别有 20/170 与 12/172 pin 失配（含 service_registry.py、service_runtime.py、service_host.py、service_components.py、admission.py 及 execution 组链），相关片段在最终字节上的判定以 r2（已含按裁定并入的 portfolio_analytics 控制面节点）为准。(2) 「请求 publish 绑定 → SCOPE_DENIED」在冻结测试中为注释描述而非断言；r154 实际断言的是 service 出站 client 本地 publish → AMBIGUOUS_OR_MISSING_ROUTE。(3) 跨进程真实独立分析进程属 AFI04 条款（计划 :796），不在本 PASS 认证范围；本 PASS 的 analytics 证据为同进程真实角色切片。(4) 「无任意模块加载」由配置白名单/负例（CFG03、第 96 轮、第 165 轮）支撑，未含运行期任意加载探针。(5) combo-3 staging 复跑 run-a 并发争抢 1 failed 如实保留，正式轮结果为准。(6) INVALID_POLICY_VERSION / POLICY_VERSION_CONFLICT 为 owner 侧 store 产码，以冻结测试断言为准；r165 冻结测试 docstring 漂移（:629-637）保留不改。

- 裁定级注记（随账本）：

> 运行后（同日 14:19:59 +0800）根仓 dev 追加提交 e12a2880（round 190：arb02 崩溃先腿与 arb03 组部分成交场景节点及两个 evidence 目录）；与 r2 的 147 pins 交集仅 tests/integration/test_arb03_dim05_group_partial_fill_service.py，独立重算其磁盘字节仍等于 r2 pin 且工作区干净，r2 pins/heads 记录（定格 ee3b05c2）效力不受影响。

- 提交范围：本证据目录 + 账本 CFG06 行翻 PASS/31 与披露块 + 进度 第193轮。
