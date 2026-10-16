# 第170轮根裁定：ARB04 组提交失联与晚到成交（提交 `52127cba`）

- 裁定：GO（增量，条款部分覆盖）。ARB01–ARB06 组仍部分，不新增正式 PASS（28/68）。依据：r1 两轮 2 passed + 两回归全绿、staging 提交树复跑全绿、独立复核 GO-with-disclosures。
- 范围（2 新文件，+1214，提交 `52127cba`）：`tests/integration/test_arb04_group_reply_loss_service.py`（sha `40138c30…`，538 行）+ `tests/integration/_arb04_group_reply_loss_support.py`（sha `b508086d…`，678 行）。故障注入：coordinator 子进程 transport_factory 包装器转发真实 `orders.submit_group_leg` 请求（account host 完整执行并应答）后丢弃真实回复，抛出真实传输类 `RequestOutcomeUnknownError(message_id, response_timeout)`；两个真实 account host + 一个 coordinator host（spawn 进程）、CURVE over loopback、真实 SQLite（group store/execution/outbox/risk journal/authority/gate），唯一合成件为外层 provider leaf。
- 验收证据：
  - r1 `D:/temp/it10arb04-20261016-r1`：run1 2 passed（17.8s）/ run2 2 passed（18.2s），junit 各 2/0/0/0，replay 确定性（junit 去时间戳后逐字节一致）；回归 ARB03 2 passed、R121 1 passed；四 run 全 exit 0；before==after pins（root `d85ec636`、base `b4aabd1e`、execution `dc8bd7a3`、risk `c7fc809b`、transport `a397fdb4`）。
  - staging（F8 要求，`D:/temp/it10arb04-staging-20261016-r1`）：提交树 root `dcad4003`（risk `e2bedb3`）下 run1 2 passed（18.42s）/ run2 2 passed（19.19s）/ ARB03 回归 2 passed / R121 回归 1 passed，全 exit 0；pins before==after。
- 独立复核 GO-with-disclosures（`.codex-tmp/arb04-review-status.md`；归档 review/independent-review.md）：F1–F6 全 GO（故障注入真实——请求真实执行后才弃回复；wire 计数/durable 计数/provider 写数三维印证不回发；断言非空洞；晚到成交冻结有每条轴上的 durable 证明；自包含不依赖他线在途文件）；F7 披露：弃回复注入不进入 socket 真实超时状态（无 `_last_uncertain_message_id`/socket retire，坐标与 cause code 与真实一致但链路保真有限）、r1 未记运行期测试文件哈希（复核重算值即提交基线）、cluster close 无 kill 兜底、basetemp 残留、时序余量宽；F8 触发 staging 复跑（已完成）；F9 条款未证部分见下。
- 条款映射（正式迭代计划.md:770）：证明——任一腿提交回复丢失/UNKNOWN 不重发（wire submit 恰 1）、不提前释放（预算 HELD、全程无 `reservations.release`）、按原业务 ID 恢复恰一次（PAIRED、residual 0、对冲恰一次）、晚到成交不采纳/不补偿/无盲目反向（拒绝半）。未证——撤单超时（design-only，无实现）、真实网络断开/恢复（重叠 TRN01）、重复成交回报；晚到成交采纳路径按设计未实现。
- 提交：`52127cba` = 上述 2 文件 + 本证据目录（qa r1 20 件 + staging 16 件 + 独立复核 + live patch 与作者状态）+ 进度/汇总文档。
- 证据等级：FIXED_SOURCE_SPAWNED_CURVE_CHAIN_SYNTHETIC_PROVIDER_ARB04_REPLY_LOSS_AND_LATE_FILL_REFUSAL_SCENARIO_ONLY。
