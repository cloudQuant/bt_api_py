# 第246轮：DIM08 到期换月与 Monitor 阈值本机服务链

本轮整理 R11 的固定来源运行证据和前序已接受的 Monitor threshold/outbox 证据。R11 的唯一 Root 节点通过：1 passed、0 failed/error/skipped，pytest exit 0；source export 有 3,929 个 pins，source 与七个仓库 HEAD 前后均未变化。此处只记为本机服务/合成 provider 证据，DIM08 保持部分，正式计数仍为 40/68，正式案例增量为 0。没有重跑 R11，也没有 stage/commit。

R11 路径使用 spawned `ServiceHost`、CURVE 授权客户端、正常解析的服务配置、GatewayMarketHub 与 relay、真实 Monitor ingress 和 SQLite DurableOutbox。行情/账户 provider 都是受控 synthetic leaf；运行不调用 Risk 或订单路由。旧期货 key 与新月份 key 同时订阅后，旧 key 被解除而新 key 保留；市场报价更新后，独立 AccountObservation refresh 仍报告 `_OLD_INSTRUMENT` 上的原数量仓位。相同的 source event 重放返回相同 outbox sequence；两条持久事件通过真实 outbox 被阈值消费者读取，错误 scope/event type 不计数，已 ack checkpoint 的消费者重启不重复告警。测试正常关闭两个 Host、客户端与 runtime，并检查退出码和 cleanup 状态。

| DIM08 条款 | 本轮判定 | 证据边界 |
| --- | --- | --- |
| 到期/换月订阅；行情换名不迁移持仓 | 本机合成 PASS | R11 证明精确 `MarketSubscriptionKey` 的重叠订阅、旧 key 释放、新 key 保留，以及 account position 的完整旧 `MarketInstrumentKey` 不变。 |
| 真实 producer/outbox/消费恢复 | 本机服务链 PASS | Host、CURVE、relay、SQLite outbox 与阈值消费路径真实运行；provider 仍为 synthetic，不能推导真实市场/账户接入。 |
| 阈值配置、窗口和维度隔离 | 部分 | R2 的 unit contract + R11 的 60 秒/2-event 规则证明有界窗口、scope/event_type 精确匹配；策略、账户、品种、产品、组等维度组合没有覆盖。 窗口按消费者处理时钟，不按源事件时间计算（consumer processing clock, not event time）。 |
| 重复事件/恢复语义 | 部分且为 at-least-once | 相同事件 ID 的入口重放及已提交 checkpoint 后重启不重复计数。R2 还明确记录 crossing 时 ACK 丢失可在重启后产生不同锚点的第二个 alert，sink 在 crossing 失败后重启则可能不重发；rule window 本身未持久化。不能写 exactly-once。 |
| 权限边界 | 部分 | account-scope 注册在 provider/outbox 动作前被拒绝；system-scope 获准读取/追加。完整 monitor read/ACK/control 角色矩阵未验收。 |
| 标签容量 | 未证明 | 本轮有 pending queue、event count/byte 限额；这些不等于 metric label/tag cardinality 上限。 |
| 告警不冒充硬风控 | 窄路径通过 | 此阈值路径只消费并投影告警，无 Risk/admission 调用或交易旁路；不证明其他生产路径的全局隔离。 |

**本轮精确候选源码**：先在 Monitor `dev` 单独提交 `src/bt_api_monitor/threshold_rules.py`（13,897 B，`617722e269dbcf634ab7370fb3e9e46162bf22c9c254dcb59e38a591ee4f92a8`）与 `tests/test_threshold_rules.py`（22,041 B，`6f7351cc46fce954bba0a5c3781246557d4bce4c44d30ae5759fffaed37f5887`）；排除既有修改 `src/bt_api_monitor/service_control.py` 与 `tests/test_service_control.py`。Monitor 变更验收并提交后，再在 SDK `dev` 仅提交 `tests/integration/test_dim08_market_expiry_monitor_service.py`（41,088 B，`aaa5103b565c223fda9feaa946edb66c0dd489c7c2044fcbcccd600c39ccce21`）、本目录三份证据文件和更新后的 Monitor gitlink。SDK 其他 MIG01/业务 WIP 及 `开发与验收进度.md` 均不在范围。本文件记录的是拟定范围，没有替 Root 执行 stage/commit。

打包时父 SDK `dev` 已从 R11 执行时的 `9a1495ad` 前进到 `a208ccd8`（R236）；固定 HEAD 差异仅为 AFI04 三份文档/证据与进度文档，没有运行时源码或 submodule gitlink 变化。R11 的运行仍严格绑定原固定导出和 `9a1495ad` HEAD，不因文档提交重建或重跑。正式计划与只读进度快照的原字节及 SHA 已加入 raw ZIP。

R11 原始 JUnit 是 1 test / 0 failures / 0 errors / 0 skipped（6.620 秒）；stdout 显示 1 passed in 7.00s。`result.json` SHA-256 `7d1c01051998ab67e6749323ff0a3d4dc6bc906f8d60c7f3ceeef30ae99e1df5`，JUnit SHA-256 `5ad9efaaf816a7f546fb76b4c52eb1e6c5a6931a38c5f41db0dc064054bfbfc0`，stdout SHA-256 `132c1c7990c6269368d7cd486973c6e7ea81b5a18da5019a9c05eab44bacd11e`，stderr 为空。固定 spec SHA-256 `e850eeef90b1db902a8d9f4b51cfef7a073c0a05530fa7c5116c1932b6ec87f1`，fixed-source manifest SHA-256 `d1e5c7ed939a4e170c8d15a29fefa3e3df4fb77d6fa7f698e15d41e027402d45`。前序 R7/R9/R10 失败输出作为历史保留在 `raw-evidence.zip`，不替代 R11 结果。R2 threshold-rule 和 MON03 Round 78 的已接受输出也按原始字节收录，成员 hashes 见 `manifest.json`。
