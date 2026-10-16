# MIG01-R2 根裁定（root adjudication，2026-10-16）

裁定对象：MIG01-R2「破断矩阵 4 模块 re-home」的作者交付与独立对抗复核。
**裁定：两模块 re-home GO 并提交；另 2 模块按设计保持红（精确 wire 缺口报告，fail-closed）；
MIG01 保持部分（28/68 不变）。**

## 背景与范围

MIG01-R1（`46697739`/`83passed` GO）已将旧转发路径全部 fail-closed 退役，破断矩阵中 4 个
模块直接调用退役工厂 → 需 re-home（否则测试面与生产退役状态脱节）。R2 逐模块处置：

- T1 `tests/bt_api_contract/test_operation_dispatch.py` → **re-home 成功**（`_direct_api()` 真实
  `BtApi(debug=False)` + RecordingBackend double；12 参数路由节点断言逐字节保留；1 个 pin-env
  asyncio skip 与 HEAD 相同）。sha256 `e9b670bbf6aa6a436c95e258fc11557479d71769da7e30d2c5a184054fd4c03a`；
  终态 17 passed + 1 skipped。
- T2 `tests/bt_api_contract/test_environment_guard.py` → **re-home 成功**（3 节点落到文件既有
  `direct_factory`；zmq 残差引用全部移除）。sha256 `3beb5c45ba1e2506eec88f07acc56cf910247181518b1f03631231ca1c6b938c`；
  终态 47 passed。
- T3/T4 `test_execution_session.py`（pin `64961008…`）/`test_gateway_runtime_unknown_execution.py`
  （pin `27f30fd7…`）→ **保持红（不动文件）**：需要新 wire 的客户端写路径（intent/ack 模型），
  当前 direct backend / wire_mapping 无法表达；精确缺口报告见 author-status.md，
  其转绿归属计划中的 wire round。

## 验收（作者轮，全部 exit 0；失败不适用-红为设计内）

- `rehomed-r1/r2`：exit 0，65 tests = 64 passed / 1 skip（determinism pair True）；`rehomed-collect`
  exit 0。
- `targets-and-regression`：exit 1 = **恰为预期 17 红节点**（3 session + 14 gateway，id 全列于
  result.json adjudication.gap_counts，unexpected_failures []），其余 653 passed / 20 skipped
  （async-plugin 类，与 HEAD 同）；回归 spot-run 组全绿。
- 残差扫描（同 r1-scan.py + 同 patterns）：168 hits/36 files → **166 hits/35 files**；仅
  `zmq_transport_mode` 档位变化（49h/11f → 47h/10f）；文件级 delta 恰为：guard 文件消失、
  dispatch 文件 1→2 hits（故意保留的显式退役引用）；scan-artifacts/scan-behavior 哈希与 R1 相同。
- sources_unchanged / heads_unchanged 成立；序列化 4/4 首次检查即清（仅驻留守护进程）。

## 独立对抗复核 GO-with-disclosures（.codex-tmp/mig01-r2-review-status.md）

- 哈希复核起点/终点一致（两文件与作者 pin 逐字节相同）；红目标两文件 pin 未动 → 无越权改动。
- diff 审计：路由/旧撤单节点断言逐字节等同（仅 harness `_zmq_api`→`_direct_api`）；
  RecordingBackend 为 HEAD 既有（非新加遮掩），返回 `{"operation": <method>}` + 调用参数断言，
  错路由/错参数必失败；无新增 skip/xfail/mask；测试定义数 old==new（dispatch 7、guard 24）。
- 4 项 wire 缺口声明独立探针全部证实、无一被证伪：DirectBackend 无 client 写钩子；wire_mapping
  为不透明通用信封无 OrderCommand/CommandAck 投影；router 异常→UNKNOWN+raise / executor return→
  RETURNED_UNVERIFIED 与旧 GatewayRuntime 语义确为反转;无 GatewayDispatchResult→normalized-write
  消费者。红节点转绿依赖计划 wire round。
- 新鲜复现：pair exit 0 `64 passed, 1 skipped`（3.34s）；红目标 exit 1 恰 `3 failed, 14 errors`
  = 全部 17 预期节点、零意外；per-file 17P+1S / 47P；残差 delta 从保存的 scan JSON 独立重算一致。
- 序列化合规（等待外来 runner ~100s 记录，0 kill）。

## 披露（不阻塞提交）

1. 2 个节点改为显式退役断言（get_trades / async surface：退役构造抛
   `LegacyForwardingRetiredError("transport_mode=ZMQ")` 于 bt_api.py:988 先于 init_exchange；
   create_feed booby-trap 于真实调用点）——R1 已 GO 的同类模式，无真实失败可藏。
2. `_retired_zmq_api()` docstring 写 "verbatim" 但新增 `{"SIM___SPOT": {}}`——防御性强化
   （使退役顺序回归时无 feed 构造陷阱仍有效），无行为损失；docstring 措辞为 minor nit。
3. 未复跑：作者聚合 `653 passed` 未由复核重跑（lean 4 模块覆盖）；R1-r3 基线段读自作者保存
   artifact（未重执行）。红 17 节点保持红为设计内，不阻塞本增量。
4. 本提交不含 4 模块中 T3/T4 两文件；其 pin 不变。MIG01 条款（正式迭代计划.md:782）完整满足
   仍缺 wire 增量的客户端写路径 → **MIG01 保持部分，不新增正式 PASS（28/68）**。

## 证据

- 归档：author-r1/（spec/runner/result/source-before/after/8 组 run 子目录）、review-r1/
  （复核 run 目录与 patch/HEAD 对照）、author-status.md、review-status.md；照实排除 pytest-tmp
  basetemp 与运行期 `*.lock`。
- 本归档与 2 个 re-home 测试文件同父仓提交；轮次条目见 `开发与验收进度.md` 第181轮。
