# 第274轮：DIM08 正式验收（2026-10-17）

Root 裁定 DIM08 正式 PASS，累计 **48/68**。正式计划第10节要求的本机
producer/outbox/消费恢复已覆盖；合成行情/账户叶子不计真实交易所验收。

| 条款 | 接受的证据 |
|---|---|
| 换月订阅、旧仓归属 | R11 固定导出的 spawned ServiceHost/CURVE 链1 PASS；旧新完整合约键重叠订阅后释放旧流，账户数量2仍归旧合约；provider启停2/2，正常关闭。 |
| producer/outbox、重复事件、消费恢复 | R11实际Gateway relay→Monitor ingress→SQLite outbox；同事件复用原ID/sequence。R266持久consumer节点1 PASS/12.91s，在ACK失败后跨进程重启重放，不重复告警。 |
| 阈值、窗口、权限 | R11固定窗口及事件类型/scope过滤；R266实际ServiceHost资源与consumer隔离两个scope、拒绝跨scope注册。窗口/rearm补充证据按原执行版本保留。 |
| 多维标签、容量 | R266分别检验instrument+group与instrument+resource-pool；超额维度拒绝、不前移checkpoint、不产生超额告警；order_id不作为标签。 |
| 告警不是硬风控 | 验收链没有Risk准入或订单路由，告警结果不授予交易或解冻权限。 |

SDK `2140c0d87f2e06bfd6a1094f56de3dc5fde52ed4` 保存R266测试与原始包；
Monitor `e5343ba8e1f2dd9f3be725cd3b929c3b4d4ba3c3` 对应实际重启节点。
其后Monitor `02ee0c42a1e63a9cecbb666376e70c6f72b0d6e8` 与SDK
`f8c544338fdb8c2d1823f72f482637550c1d6003` 增加Execution selector，
静态复核确认未配置selector的市场事件匹配及持久窗口/cursor路径保持。

逐项完整路径、SHA和提交见 [evidence-map.json](evidence-map.json)。Root读取
R266原始记录、结果、精确测试差异及当前已提交源增量，接受独立审查者
`venue_acceptance_readiness` 的 `GO_FORMAL_DIM08`。审查者未执行测试，
Root亦未为归档或身份重复已接受QA。

证据边界：R11测试是 `aaa5103...`、Monitor overlay是 `617722...`；当前
测试 `2fed522...` 只增加bounded空poll与四个lineage断言，仍标为
STATIC_TEST_COMPATIBILITY_ONLY，不把旧PASS赋给新hash。R266节点
`d720a896...` 绑定 `a761740...` 源码，是有原始输出的作者本机pytest，
不是Root固定导出运行。13-unit仅有绑定cdf67d0/e372的作者摘要；selector
直跑有stdout/JUnit/exit而没有独立source-pins。两者仅作补充，不作为本次
闭合核心，也不用于冒充当前版本的全量复跑。

R246/R266历史结论和原件保持不变，包括当时40/68及OPEN_PARTIAL。
本轮只更新裁定与跟踪文档，没有生产代码修改或provider/账户调用。
DIST01/DIST02和ARB05双机子场景仍延期；VENUE01/02及百进程负载未因此通过。
