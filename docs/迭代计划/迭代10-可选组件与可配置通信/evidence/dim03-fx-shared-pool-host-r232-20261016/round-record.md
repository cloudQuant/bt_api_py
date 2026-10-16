# R232：DIM03 FX 估值接入共享抵押品 Host 链

本轮只归档一项新的 Root 固定来源验收：`tests/integration/test_dim03_fx_shared_pool_host.py` 的三个节点在本地合成 provider 下通过。固定来源目录、绝对 pytest 节点、cwd、rootdir、PYTHONPATH、执行 runner、原始 11 个输出文件和 376 个来源 pin 均随包保留；来源与五仓 HEAD 前后相同。当前工作区 HEAD 仅作漂移检查，候选执行从固定 R2 导出解析。

Root 结果为 pytest exit 0，3 passed / 0 failed / 0 skipped，用时 3.77 秒，stdout 中保留两条既有 PyZMQ/Windows Proactor selector-thread warning，stderr 为空。首个节点在真实共享抵押品 ServiceHost、SDK、Risk mapper、SQLite admission 与 RiskOrderJournal 路径中使用可信 FX；第二节点断言 FX 过期时零 provider write；第三节点断言 FX 缺失时 Host 在 registry 创建前拒绝启动。

货币单位按合同保持一致：合约计价单位为 EUR，可信 FX 为 1.5 USD/EUR，订单名义额为 20 EUR，因此估值/共享池占用为 **20 EUR × 1.5 USD/EUR = 30 USD**。不能记录成 30 EUR。测试的 provider、账户与输入均为本机合成数据。

本轮固定执行源码由 R223 已接受的 SDK `98648af2ea3a24394537c9fbd414fb897fb38f11` 与 Risk `647840a75b2071fcb5b4395f554190afe2fae082` 组合及 R2 manifest 指定；R223 的共同 owner/共享抵押池 Host 证据通过引用已有包与 postcommit 收据复用，未重跑先前八节点。包中固定 source manifest、spec、runner 与 raw output 提供执行来源追溯。

正式计划 `正式迭代计划.md:775` 的 DIM03 还要求策略/合约集中度、Greeks 缺失或过期、权限过滤快照不能误作全账户，以及共享抵押品无单一准入 owner 时拒绝写入；该轮已接受 R223 共享 owner 组件证据和本轮 FX 三节点仍不足以覆盖全部条款。本轮未改正式 tracker、未增加 68 项正式通过数，也不证明外部 provider、真实账户或真实交易所行为。

Raw archive：`raw-evidence.zip`，125162 bytes，SHA-256 `212e8afaa4ad780ff4bab9b61d20ec3ec0b7efef7dc0246a8456fc0a94d32c7e`。  
Manifest：`manifest.json`，13216 bytes，SHA-256 `723f8c386cc0e3a257fb2dea2f59317599f7f1ddba1b0eebcf7ff6101cc33494`。
