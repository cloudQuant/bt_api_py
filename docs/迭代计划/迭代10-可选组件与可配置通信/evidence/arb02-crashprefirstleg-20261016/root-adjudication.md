# ARB02 coordinator-crash-before-first-leg 节点 — 根裁定（第190轮，2026-10-16）

- 结论：**GO-with-disclosures**。接受为开发增量（测试侧，生产零改动）；ARB02 仍**部分**；正式 30/68 不变。
- 任务背景：ARB02 剩余缺口（gapmap2 第 2 项）——"全部账户预留已提交、首腿派发前协调器崩溃（无 TTL）"不可超卖节点。

## 交付
- `tests/integration/test_arb02_group_budget_service.py` **+488/−0**（`639d4d847f3d8f61e2e2c4c46d0d1058d1c38ab4314bc4ed354ba93754af5391`，3124 行；唯一被改文件）。
- 节点 `test_all_reservations_submitted_crash_before_first_leg_no_oversell`：两账户 Risk 向量提交；account-b 扣留预留应答；协调器进程于 deadline 严格之前被真实 `process.terminate()` 杀死（exitcode≠0 断言；无 TTL 等待）；崩溃后释放应答；重开为 PREPARING/RESERVED、双向量未被 claim、provider 零副作用；精确 replay 续跑至 PAIRED：每账户恰一次 RESERVED→HELD、每腿一次派发尝试、每账户一次 provider 写入、`pending_holds()==()`、无 reject/recovery/deadline 事件、恰 2 次 reserve RPC（队列空守卫）。

## 运行事实
- 生产方 `D:\temp\it10arb02-crashprefirstleg-20261016-r1`：runner exit 0 / pytest_exit 0；junit 5 tests 0F/0E/0S（89.122s；新节点 34.833s）；sources_unchanged=true（248 pins）、heads_unchanged=true（parent e137193d、base b4aabd1e、gateway 59b164ab、zmq a397fdb4）。预检失败一次（spec 转义工件；无产物）保留于记录。
- 独立复核 `D:\temp\it10arb02-crashprefirstleg-review-20261016-r1`：整文件独立重跑 `5passed/exit0`（83.359s；新节点 30.359s）；248 pins 前后复算一致；**零证伪**；断言反平凡性逐条核验（事件日志恰一次各终态、计数器、provider 写入、恰 2 次 reserve RPC）。

## 披露（随提交）
1. 复核期间 parent HEAD 因 MIG01-R3 提交（ee3b05c2）移动——提交为 git 态变更，248 pins 在提交后仍逐字节一致，且 ee3b05c2 不含本文件；本轮提交仅含本测试文件（ARB03 同轮）。
2. runner 仅门控 4 个仓库 HEAD；execution（f5c03064）/risk（adb6ec58）HEAD 未门控，但其 26/22 个源文件在 hash pin 闭包内且逐字节稳定。
3. "one hold" = 组合级单一 RESERVED→HELD（单 $200 预算）的适配表述；每账户证据由 claim/resolution + dispatch_attempts + provider 写入行承载（与生产语义一致）。
4. 残差：合成 provider、无真实账户；账户级拒绝→零派发负例与同瞬 SQLite 竞争节点仍未建（ARB02 仍部分）。

## 版本管理
- 与 ARB03 增量合并为第190轮提交（见 commit e-sha）；后续：ARB02 账户级拒绝负例（可选）、ARB 组完整矩阵。
