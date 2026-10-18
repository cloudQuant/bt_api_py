# 迭代10：CTP 终态查询与风控身份传递（2026-10-18）

正式进度仍为 **62/68**。本轮接受本机固定源码、真实 SQLite/回调处理与合成 native API 叶节点的组件证据；没有真实 SimNow 下单、撤单、成交或风控整链验收。

CTP 终态读取器只验证本轮托管订单的 CANCELLED/REJECTED、零成交状态。它绑定同一 owner、OrderRef、session、完整 QueryOrder 回调窗口以及查询前后账本快照；读回必须使用同一签发对象，并在校验前后检查有效期。已有 OPEN/PARTIAL 撤单目标检查未改变。费用字段仍为 None/INCOMPLETE，不伪造实际手续费。

Execution 的查询窗口重复读回已修复：每次仍重验持久记录，保留首次观察时间和原对象，不再因读取时间推进而误报来源变化；新增回调仍会被拒绝。此轮已在 dev 提交为 `c8f1a8d94d706dccbb22593cb618eeedf2369b91`。下一小轮增加可选的可信身份传递接口，BasicAdmission 成功后将原调用者上下文、writer lease 与端口交给 Risk 预留，缺少上下文时不调用预留；旧接口行为保持。该轮 dev 提交为 `a147e4c853c0daefc53eebdff2748d09b1b2aca9`。

验收累计覆盖 11 个不同检查：终态读取器 5 项、查询窗口回归 1 项、可信身份传递 5 项。R1 为 2 passed/2 failed，定位了窗口时间错误和“全成交后再撤销”的错误夹具；R2 为 10 passed/1 failed，唯一失败是缺少上下文的测试预期，接口实际会提前抛 AdmissionRequired。仅修正该断言后，R3 为 1 passed/5 deselected；未重复运行已接受节点。每轮来源后检完成，输入和八仓 HEAD 不变，无拒绝事件，stderr 为空，仅有预期的 CTP 原生扩展排除提示。

SDK 本轮只交付终态读取器、测试、Execution gitlink 和本记录；正在开发的运行时 Risk 桥接、命令钩子和风控终态 ACK 不包含在本轮验收中。旧 SimNow 会话状态、逐笔费用、首次托管结算确认、LOAD01 内存门槛及用户暂缓的双机项目继续保留为待验收。

[机器可读轮次记录](evidence/iteration10-ctp-terminal-query-20261018/round-record.json) · [原始 QA 与精确源码](evidence/iteration10-ctp-terminal-query-20261018/root-qa.zip)
