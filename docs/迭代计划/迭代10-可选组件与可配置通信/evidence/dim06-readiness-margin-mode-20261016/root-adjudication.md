# 第212轮 Root 正式裁定：DIM06（2026-10-16）

**Root 裁定：DIM06 正式 PASS，正式通过数从 38/68 增至 39/68。** 本轮将既有 R163、R189、R191、R209 证据与本轮 readiness margin-mode 定点运行合并审阅。证据级别为本地 adapter 合同与合成叶端验证；不构成 provider、真实账户或真实 Host 验收。

## 条款映射

| 正式计划 §10 DIM06 条款（`正式迭代计划.md:778`） | 可审证据 | 本提案裁定与边界 |
|---|---|---|
| CTP 交易日、结算、重连与 OrderRef 恢复 | R163 根裁定记录 CTP 合同及本地故障验证；R163 排除的 3 个 session-path 节点由 R189 T3 增量 3/3、combined 17/17 补齐。 | 合同/路径级证据覆盖。R189 的 MIG01 wire 结果只支撑这些 session 路径，不声称真实 gateway router 或 provider 行为。 |
| 各 venue 持仓/保证金模式 | R163 对支持与明确不支持的 venue 能力作出合同覆盖；本轮 9 节点中 5 个既有 readiness 节点和 4 个新增 margin-mode 节点共同通过。 | Binance isolated 读取 symbol 配置后 ready；OKX isolated 与对应 leverage rows 匹配后 ready；cross 请求遇 isolated rows 时 definite failure；缺失 position/leverage 保持 unproven。共享抵押品/资源池归属属于 DIM03，本提案不声称 DIM03 完成。 |
| 订单类型/TIF 不兼容拒绝 | R191 矩阵 43/43；R209 两 venue 新 guard 21/21，分别覆盖 Binance endpoint-scoped LIMIT TIF 拒绝及合法邻例、OKX market + post-only 拒绝及合法邻例。 | 本地 adapter/mapping 前置拒绝证据覆盖。R191 自身曾将 Binance TIF guard 和 OKX market/post-only 标为未断言；该窄缺口由 R209 补齐。 |
| 已支持账户管理动作与其他策略隔离 | R163 根裁定及其固定源 QA。 | 合同/本地故障验证覆盖，不代表任何真实账户模式变更或 provider 执行。 |
| UNKNOWN 阻断恢复 | R163 根裁定及其固定源 QA。 | 合同/本地故障验证覆盖；未调用真实 provider。 |
| 未实现的账户管理能力明确拒绝 | R163 根裁定及其固定源 QA。 | 能力拒绝合同覆盖；未执行真实账户管理动作。 |

## 本轮 Root 运行事实

Root 在 `D:\temp\iteration10-dim06-readiness-margin-mode-root-20261016-r1` 对固定 spec 中 9 个节点运行一次：JUnit 9/9 passed、0 failed、0 errors、0 skipped，`exit.txt=0`；stdout 记录 `9 passed, 1 warning in 1.06s`，stderr 为空。唯一 warning 是已知 pytest 配置警告 `Unknown config option: asyncio_default_fixture_loop_scope`。独立静态复核结果为 `GO_TO_ROOT_FOCUSED_QA_ONLY`；它不是生产验收，也不替代 Root 的正式裁定。

测试使用 `BtApi.get_order_readiness` 和真实 Binance/OKX normalizer，只有末端 feed 方法是合成对象；四个新增节点均断言未调用 `make_order`。本轮没有调用 provider、网络、账户或 Host，也没有修改产品源码。17 个 source pins 的 before/after 文件字节相同；Root additive reconciliation 确认当前来源字节仍匹配 persisted-after pins。静态 spec 绑定的 7 个 repo HEAD 是运行开始时的 pins；runner 未持久化 repo-after snapshot，故本记录不声称运行前后 HEAD 不变。Root 明确记录 QA 已先于其后续 Risk 子仓提交完成。

## 结果封装故障披露

pytest 完成并写入 `exit.txt`、JUnit、stdout/stderr 和 source-after 后，runner 在构造最终 report 时因读取缺失的 `evidence_level` 键而抛 `KeyError`；静态 spec 只有 `planned_evidence_level`。原 spec 与运行目录未改写，`result.json` 未生成。本归档将 11 个原始 Root 产物（10 个原始 run 文件加 additive `root-report-reconciliation.json`）原字节保存在 ZIP 中；不补造 `result.json`，也不重跑已通过的 9 节点。此 runner 封装错误不改变 pytest 的 9/9 事实，但限制了 runner 层的完成状态与 HEAD-after 断言。

## 正式边界

Root 核对正式条款及上述独立审查/实际结果，确认 adapter 合同与本地故障验证证据闭合，接受 DIM06。DIM03 的共享抵押品归属、真实 venue/provider 行为（计划指向 VENUE02）、真实账户/Host 行为均不由本提案裁定。本轮同步更新正式 PASS 计数和 tracker/progress；历史结果及其当时的部分完成结论保持原样。

## 本轮交付

- 新增测试：`tests\bt_api_contract\test_dim06_readiness_margin_modes.py`，7834 bytes，SHA-256 `e23093ca5247ceb7be313aa56221aeeac726f107553b158b09bcd524cb115b0d`。
- 原始证据包：`docs\迭代计划\迭代10-可选组件与可配置通信\evidence\dim06-readiness-margin-mode-20261016\raw-evidence.zip`，11503 bytes，SHA-256 `16e6fb73eb8c8a03ff80ead7e0a1db613a41fb6eacd16d84ebd8c740761ae5e1`。
- 逐文件 ZIP entry pins 与既有轮次裁定文件 pins 见同目录 `manifest.json`。
- QA 时 SDK HEAD 为 `937350b138ea952a14e1ab08539dc295efb1d0a4`；交付 parent 为 `ebd6c1feb64dbabecfa401f128696349de7c7541`。新增测试、三份证据及两份进度文档共6路径提交至现有 dev；实际提交哈希见 Git 历史及 `.codex-tmp/iteration10-r212-postcommit.json`。不包含无关 WIP。
