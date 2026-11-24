# AFI05 account-policy admission startup binding — SDK source round

日期：2026-10-17
仓库：`D:\bt_api_py`，`dev`；candidate base HEAD `1dfc99601b027e1cce3e112ffffa6d96a161f212`。

精确 scope：`bt_api_py/service_components.py` 的已审查 account-owner policy binding overlay，及新测试 `tests/unit/test_service_risk_policy_admission_resolution.py`。selected patch：`.codex-tmp/afi05-account-policy-head-overlay-r1/selected.patch`，9,616 B / `da99ab91ef43c1016f9fe6f36267c89b74d85827843e192271d94238526ff19c`；由固定 HEAD 导出的完整候选组件为 132,013 B / `43b0ae1be87daef77d246250a0787738937121dbb753c99b0660d1d927027152`。测试为 9,162 B / `b0c2fd9ce7f981411175eab76d6bfed4ffe07d2e9d12a2792d2fbd2e043dcc49`。

变更将受信 `PolicyCatalogAccountAdmissionResolver`/精确 account `WireScope` 接入 Risk `account_owner` 组件构造：只从 `ConfirmedPolicyStore` 读取确切文档/回执，验证 account target、Risk policy、gate 及 durable binding 一致后才开放 `service.order_entry`。不替代静态 portfolio budget resolver，也不放宽共同 budget generation 的全成员确认约束。

候选的两个 focused unit tests 已在隔离导出下运行并通过（2 passed，1.70s）；本轮没有重跑。独立 `review_bt_v3_cpf` 对上述精确 source/test pins 返回 `GO_STATIC`。证据仅为 local unit + static source review：没有 ServiceHost、AFI HTTP、账户或正式 AFI05 验收。SDK live checkout 的 `service_components.py` 含其他 lane WIP；本轮只能应用/提交精确 overlay，保留 AFI08 的 `dispatch_quota_admission` 资源键和 constructor hunk。

提交状态：尚未 stage/commit；等待 AFI08→DIM09→本轮 SDK index slot。待 slot 到达后，按当前父仓库状态核验 HEAD blob/selected hunk 并做 exact-scope commit。
