# 第204轮：BT01/BT02 正式验收与行情对齐修复

Backtrader/dev `edfb4ddb64ee42e0d1f90d7a111def63447ccd67` 已提交四个明确路径：生产 BtApiFeed、定向 unit、新 Store/Feed 集成节点及本轮记录。tick 产生可交付 bar 后停止排空，保留对应 quote，避免第二条 tick 抢先替换第一根 bar 的订单上下文。提交后四路径匹配已接受字节，暂存区空，无关 tracked 改动保留。

Root R1 一项失败定位生产行情错位；R2 unit 通过、集成因把一条 intent 错当一条 lifecycle receipt 而失败；R3 只运行修正后的集成节点，1 passed / 13.76s / exit 0、一条既有 Quandl warning、stderr 空、819 pins 与五仓 HEAD 前后一致。三轮原始结果完整保留，没有把 R2 改写为全过，也未重跑已通过 unit。

R3 通过真实 BtApiStore.getdata() → BtApiFeed → Cerebro/Broker → ServiceExecutionSession → V2 Host；两条 tick cursor 已接收，持久 fill 与 quote source/generation/cursor/instrument/as-of、data/bar 对齐，成交 1.25 @ 10.02，一次 notify_trade/Host 派发。四条 lifecycle receipts 属同一 intent，仅最终 FILLED 投影产生一次回调并 ACK。Session/runtime/runner/Host 正常退出，exit 0、无强制清理、两对 Pipe 关闭。

Root 结合独立逐条裁定接受 BT01、BT02 正式 PASS，累计 37/68：R82/R107 覆盖策略绑定和共享 Broker 读语义、预算不冒充现金；R120 双策略正常账簿/资金/持仓重开；R122 同 revision 重复投影不重复成交/费用；R184 真实 fresh-process 在 Trade checkpoint 后、callback ACK 前崩溃，重启恢复原 Trade 并重放/ACK callback，submit_calls=0、receipt 不增、Host dispatch 仍一次。本轮补齐原先唯一未经过内置 Store/Feed 的链路缺口；这些已接受旧节点不重复运行。

边界：本机合成末端 provider，覆盖被测 MARKET/FOK 开仓和恢复窗口，不是交易所账户验收。回调用持久待交付/ACK 状态追踪，允许失败重放，不承诺任意用户副作用 exactly-once。正式计划 BT01 允许替换末端 provider，BT02 明确该回调边界。独立审计含各原始证据定位。SDK 仅此证据/状态五路径；并行 LOAD 遥测和迁移候选不纳入。本轮 SDK 提交 SHA 见 Git 历史。
