# R228：AFI05/06 Risk 预算 admission resolver 固定源验收归档

日期：2026-10-16。Risk `dev` 候选建立在已接受提交 `ede55dec5e8e7ad1c5376969eb54e9933ebf9e85` 上，精确候选范围为三条路径：`src/bt_api_risk/service_component.py`、`src/bt_api_risk/budget_admission.py`、`tests/test_policy_catalog_budget_resolver.py`。SDK parent 本轮验收 HEAD 为 `1e79ecfde28e13b95b60ec88b092783564b1ee24`；本轮只归档证据，不修改共享进度文件、不暂存或提交。

## Root 固定源验收

Root 唯一选择的两条新 resolver 节点通过：2 passed / 0 failed / 0 error / 0 skipped，pytest exit 0，用时 2.36s；stderr 为空。Root 结果记录 147 个 source pins unchanged，并且 SDK parent、Risk、Base 三个 checkout HEAD 在运行前后保持不变。固定实际执行的 spec 为 `.codex-tmp/iteration10-afi-budget-resolver-root-spec-r3-r227-20261016.json`（37172 bytes，SHA-256 `098e366d0f4b46aa1ffff67cae2d4d60ec9e03a83ff807d5e90051f7fb3d5e68`），实际 runner 是 `.codex-tmp/iteration10-focused-qa.py`（3204 bytes，SHA-256 `8b7f470be77270adeb9d72a83776b827c4653004b39835eeca0f43bf63cf24ed`）；spec 与 Root 输出内的副本逐字节相同。全部 raw11 原件、实际 spec/runner、结果、source 前后快照及 JUnit 均按原始字节保存在 `raw-evidence.zip`。

运行节点覆盖重新打开持久 Risk stores 后读取完整 authority 的 resolver 路径，以及绑定请求账户与确认 store 的拒绝边界。固定源层使用 Risk `ede55` 的受验收源码加本轮受限 production overlays，并使用 Base `b4aabd1e17b9c7252fa7b4d266932087d99b5517` 的111个已接受 Git blobs；完整固定来源/哈希在归档 `fixed-source/source-manifest.json`（41970 bytes，SHA-256 `d20aa7c7e07acfeb57ee90af4afb91c7e3b3494ef4b19ae5e791682ff51b6523`）。Risk `service_component.py` 的 ede55 原始 blob 和本轮覆盖版均分别封存。SDK parent、Risk、Base checkout HEAD 仅作 Root mutation guard，不等于把这些 live checkout 作为运行时来源。

## 本轮范围与产品边界

| Risk 路径 | 本轮内容 | SHA-256 | 字节 |
| --- | --- | --- | ---: |
| `src/bt_api_risk/service_component.py` | 既有 PolicyCatalog owner 上接入 authority-ready budget resolver | `88ed4255d56e1ed2d8e445d110c72a6c26464534e00ed52b14d9d4d8a63c5e04` | 112223 |
| `src/bt_api_risk/budget_admission.py` | 新增解析器：精确已确认 generation/member receipt 读取与 RiskPolicy 构造 | `f90f73deef863d853d3a80cd509229c452e38063094814d66d3cf09b4cc5e478` | 8575 |
| `tests/test_policy_catalog_budget_resolver.py` | 两个新 resolver 固定源测试节点 | `d33fecb285338a2802ae4bf7919d415066dbab94b4afabdbc457201a147dd130` | 10874 |

独立静态审查 `.codex-tmp/iteration10-afi-budget-resolver-static-review-r2-20261016.md`（5542 bytes，SHA-256 `5a7a118084a1374e415a0202d1fd9041894174a6b62b6366613b921e1be95bdb`）按原始字节保存在 ZIP，并核对真实 Risk store/receipt/`RiskPolicy` 构造 API。本轮结论仅为 `ROOT_FIXED_SOURCE_RISK_POLICY_CATALOG_BUDGET_ADMISSION_RESOLVER_ONLY`：本地合成 SQLite/store 与 resolver 组合路径获得固定 Root 验收；本轮没有把 resolver 接入正式 admission/order 判定，也没有接入普通 AFI HTTP/UI 的认证 principal/scope 控制面。

正式迭代计数仍为39/68，本轮正式通过增量为0。没有读取真实账户、凭据或调用 provider。后续产品闭环需由 Risk admission 实际消费此已确认 authority，并由普通 AFI 认证 HTTP 控制面接入 SDK `service.risk_policy` 的发布、审批和逐账户状态；只有真实执行消费者读取精确已确认版本后才可宣称 `consumer_effective`。本归档不代表 AFI05/06 正式验收或预算已对订单生效。
