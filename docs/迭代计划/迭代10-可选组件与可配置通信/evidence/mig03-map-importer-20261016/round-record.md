# MIG03 map-only offline importer component — delivery proposal

轮次编号由 Root 在排定 R209/R210 后统一分配；本目录名不预设轮次号。当前只准备交付文件，未暂存、未提交，也未改进度账本。

本轮只包含 `service_migration_import.py`、其 17 节点定向测试，以及两次 Root focused QA 原始结果归档。Root R1 的 17 项失败保留为 fixture 诊断：测试 seed 在调用真实 v23 `claim_for_dispatch` 前没有取得生产 API 要求的 writer lease。仅测试 fixture 随后按生产 facade 的 acquire/release 路径修复；生产 importer 未改。Root R2 在修复后的文件上 17 passed，耗时 `3.71s`，pytest exit 0、stderr 0 字节；90 个来源 pin 前后相同。QA 仍绑定 `d400f0f98724554c0aed3f41ebfaffcff4d9e774`。准备本交付时分支 `dev` 已由 R209 更新至 `0302917dd50184a74cfa8d66c5e597cec904984c`；Root确认该提交与本轮依赖无关。

独立静态 review 的元数据来自 Root 收到的协作消息，不存在独立原始 review 磁盘文件。review verdict 为 `GO_STATIC`，审阅的 importer pin 为 `d4ec88d69e65e7012e20077cf4a4e5123d3e96f70606c0709ddbb2861073bb71`，测试 pin 为 `1a59f1e14799004d230363a3f04805a0d14a3c1242dbc41db0b5d569f710c48c`；该测试 pin 是 R1 fixture 修复前版本，不能称 reviewer 审阅过最终 fixture。最终测试版本 `e2e85e1850aeb58c16d945225a080452609d94b5782a75bb7b1a6da3945e542f` 由 Root R2 17 项验收覆盖。未找到 R1 旧 fixture 的原始源码副本，所以 ZIP 只保留其 source hash metadata，不重构旧源码冒充原件。

证据级别为 `ROOT_FOCUSED_SOURCE_QA_MAP_ONLY_IMPORT_COMPONENT_NOT_FORMAL_MIG03_PASS`。实现针对 Execution schema v23 做有限、有界读取与字段核验，并把已验证的显式关系交给既有 identity-map batch API；测试使用临时合成 SQLite 库与真实 store/facade API，不触碰 Host、provider、私有账户或真实部署数据库。该 map-only 组件不搬迁/改写旧 journal，也不构成 runtime 授权。

Manifest 中的 `fence_receipt_ref` 仅作为调用方提供的外部引用保存；引用或其摘要本身不证明 fence 已建立、有效或被遵守。源 Execution DB 只读检查与目标 identity-map SQLite 写入跨数据库，不具备一个原子事务。此包不声称跨库原子性或完整迁移 drill，MIG03 仍为 partial，正式验收计数不变。

精确代码、测试和三个交付路径见 `manifest.json`。以下并行脏路径明确排除在本轮 scope 外：

- `bt_api/bt_api_monitor`
- `bt_api/bt_api_risk`
- `bt_api_py/gateway/__init__.py`
- `bt_api_py/gateway/client.py`
- `bt_api_py/service_execution_group.py`
- `bt_api_py/service_execution_group_authority.py`
- `bt_api_py/service_execution_group_proofs.py`
- `tests/test_gateway_client_and_certification.py`
- `tests/unit/test_load01_hundred_strategy_30min.py`
- `tests/unit/test_load01_rpc_telemetry_contract.py`
- `%SystemDrive%/`
- `.codex-tmp/`
- `0`
- `tests/integration/test_service_execution_group_owner_recovery.py`
- `tests/integration/test_service_portfolio_analytics_v2_process.py`
- `tests/test_mig01_gateway_entrypoints_retired.py`

原始证据 ZIP 含 R1/R2 各 11 个根文件与一份标明来源及版本边界的 `review/review-summary.json`。pytest 的 `pytest-tmp/` 临时目录未归档。
