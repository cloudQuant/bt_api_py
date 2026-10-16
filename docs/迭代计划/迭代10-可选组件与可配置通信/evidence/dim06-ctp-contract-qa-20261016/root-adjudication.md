# 第163轮根裁定：DIM06 CTP adapter 合同固定源 QA 增量归档

- 裁定：GO（增量归档级）。范围仅限 evidence/dim06-ctp-contract-qa-20261016/ 的归档与文档；无源码变更、无 PASS 状态变更。
- 运行事实：r1 全子集 13 模块 519 节点 513 passed / 3 failed / 3 skipped（exit 1）；3 个失败 = test_execution_session.py::test_session_uses_real_gateway_command_roundtrip_with_native_mt5_intent[result0-True/result1-False/result2-False]，归因于运行当时未提交的 MIG01-R1 legacy-forwarding 退役（该退役现已提交 46697739；3 节点去留待 gateway-wire 轮处置）。r2 同子集 −3 deselect：513 passed / 3 skipped / 3 deselected，exit 0。两轮 sources_unchanged=true、heads_unchanged=true（HEAD 4e46ff01、base b4aabd1e、ctp a497bab6 前后不变），stderr 空；junit/exit/result/spec/source-before/after/runner/stdout 已入档。
- 条款→合同映射（正式迭代计划.md:778）：1 交易日、2 结算读回、3 重连代次、4 OrderRef 恢复、7 账户管理动作隔离、8 UNKNOWN 阻断恢复、E 未实现能力明确拒绝 = COVERED（合同级）；5 各 venue 持仓模式 COVERED（其他 venue 显式不支持；保证金池维度属 DIM03 线）；6 订单类型不兼容拒绝 COVERED（CTP limit-only 前置拒绝）、venue×TIF 不兼容矩阵仍缺（需新合同）。
- vehicle 照实：条款右列 vehicle = adapter 合同 + 本地故障验证，provider 行为另按 VENUE02 记录；本归档为固定源/合成、无真实账户、无 Host 服务栈，不宣称服务栈 vehicle。
- 裁剪与完整性：未入档 pytest --basetemp 瞬态 scratch（r1+r2 共 1872 文件 / 3154691 B，完整树保留于 D:/temp/it10dim06-20261016-r1/、-r2/）；入档文件字节原样；600 KiB 上限内排除 0 个。
- 来源一致性（信息性）：r2 spec 30 pins 中 30/30 与当前 dev 树逐字节一致。
- 剩余缺口：venue×TIF 不兼容合同（#6）；margin-pool 归属裁定（#5，DIM03 线）；3 个 execution_session 节点处置；DIM06 正式 PASS 归后续轮次。
