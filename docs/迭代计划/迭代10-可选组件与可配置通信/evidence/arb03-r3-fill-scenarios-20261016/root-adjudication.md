# ARB03 首腿零/全成交 + 第二腿部分成交 节点 — 根裁定（第190轮，2026-10-16）

- 结论：**GO-with-disclosures**。接受为开发增量（测试侧，生产零改动）；ARB03 仍**部分**；正式 30/68 不变。
- 任务背景：ARB03 剩余场景（R2 后）：首腿零成交、首腿全成交、第二腿（对冲）部分成交/撤销。

## 交付
- `tests/integration/test_arb03_dim05_group_partial_fill_service.py` **+497/−0**（`2f06291dbed35bc56f54d6e502c001fd3316f9408cf4a0fab615acaff03a033e`，2357 行；唯一被改文件）。三节点：
  1. `test_arb03_first_leg_zero_fill_closes_group_without_hedge`（59 断言）：REJECTED 零成交首腿→CLOSED/`GROUP_CLOSED_NO_EXPOSURE`、无对冲派发（0 provider 写入、无 intent/claim）、双预留 RELEASED、残差 0。
  2. `test_arb03_first_leg_full_fill_sizes_hedge_exactly_once`（78 断言）：全成交 2/2→对冲重定尺寸 6→`4.000`、恰一次派发（writes/queries 1/1、dispatch_attempts 1）→PAIRED、残差 0、`GROUP_PAIRED` 载荷 2/`4.000`/0。
  3. `test_arb03_hedge_leg_partial_cancel_requires_recovery`（69 断言）：首腿 1.5/2、对冲 3、对冲 cancelled:2→RECOVERY_REQUIRED/`hedge_residual_unresolved`、残差 1、`CANCELLED_PARTIAL` 双观测链、无超卖。

## 运行事实
- 生产方 `D:\temp\it10arb03-r3-20261016-r1`（spec `focused-qa.json` sha `5cef0e41…`）：exit 0、`5passed/0F/0E/0S/47.48s`、stderr 空、sources/heads 不变（353 pins 全量复算一致；root e137193d）。
- 独立复核 `D:\temp\it10arb03-r3-review-20261016-r1`：独立重跑 `5passed/exit0/49.93s`、stdout sha `a669cdc6…`、无孤儿进程；**零证伪**；三节点语义逐条核验：状态 CLOSED/PAIRED/RECOVERY_REQUIRED、残差 0/0/1、事件种类与载荷、恰一次派发（dispatch_attempts==1 + 精确 4/5 事件 outbox 元组 + provider 写入/查询 1/1）、无对冲（None-intent/status/claim/receipt + 0 provider 写入 + risk 向量 False/False）；计数器均为生产持久值并经 provider 台账 DB 行与 risk gate 向量交叉验证。

## 披露（随提交）
1. 零成交 CANCELLED 不可表达：生产 gate `bt_api_py/service_execution_group_proofs.py:2291-2298` 将零成交 CANCELLED 记为 `GROUP_ORDER_OUTCOME_UNKNOWN`；REJECTED 为可表达形式。
2. 节点③ recovery 事件为 `any(GROUP_RECOVERY_REQUIRED)`（非恰一次），状态/错误码/残差仍精确钉住。
3. 生产方报告 2358 行系笔误（实际 2357，sha/字节一致）。
4. 残差：合成 provider；全成交+对冲拒绝/部分成交跨组合、零成交 CANCELLED 表达、真实账户与完整矩阵仍缺（ARB03 仍部分）。

## 版本管理
- 与 ARB02 增量合并为第190轮提交（见 commit e-sha）；后续：ARB03 跨组合与真实账户矩阵。
