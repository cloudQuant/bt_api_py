# R223：DIM03 trusted composer shared-collateral host slice

本轮保留并组合两次 Root focused QA 的原始证据：R6 在 SDK R221 固定源码下通过六个拒绝/启动负例，另两项正例因测试错误地要求 provider client order id 等于原始 intent id 而失败；R7 只修正并重跑这两个正例，在 SDK R222 固定源码下两项通过。八个唯一节点均有一次成功执行记录。R6 的失败结果原样保留，不伪装为全绿的一次运行。

R7 对共享域和隔离域的成功路径增加持久绑定核验：从真实 `RiskOrderJournal.get_command/get_event` 读取已保存请求和事件，将 caller intent、owner 派生 `risk-<digest>` client id、请求摘要、合成 provider 写入/回执、`PROOF_READY` exposure reference、`ORDER_OBSERVATION` 与 `ACKED_TRACKED` 状态连接起来。没有更改 production source，也没有放宽 Risk proof gate。ZIP 中的静态 AST 报告证明六个 R6 通过的负例函数与当前源一致、两个 R7 正例与当前源一致；完整 QA 输出和原始候选测试源均保留。

本轮源码范围：Risk `src/bt_api_risk/core/instrument.py` 已单路径提交 `647840a75b2071fcb5b4395f554190afe2fae082`（parent `ae9075ebd6f2bda2802edcb41a2dc66c859478fc`）；SDK `bt_api_py/service_components.py`、`bt_api_py/service_host.py`、`bt_api_py/service_risk_order_entry.py` 和新的 `tests/integration/test_dim03_shared_collateral_composition.py` 仍由 Root 按此精确 pin 提交，并更新 Risk gitlink、进度记录及此证据三件套。SDK commit 标识待 Root 完成；不包含其他开发工作区改动。

证据仅覆盖冻结源码导出中的本地合成 provider/Host 组合，不声称正式 DIM03 PASS、外部 provider 或账户验收。R6/R7 每轮均记录 368 个源码 pin 和五仓 HEAD 前后不变；R7 两条既有 Proactor warnings 原样保留在输出。没有重复 R6 六个已通过负例，也没有进行其他 QA/Host 或真实账户操作。

- Raw archive：`raw-evidence.zip`，151282 bytes，SHA-256 `12d784acf0fdf0c826e4f2f5d14857668fdfc8415da25ed2d19421a6ea934d97`。
- Manifest：`manifest.json`，14748 bytes，SHA-256 `aef21495664635b537cd4d854752d3b3fc9fb182dc2783e495a787f6a65a9e20`。
