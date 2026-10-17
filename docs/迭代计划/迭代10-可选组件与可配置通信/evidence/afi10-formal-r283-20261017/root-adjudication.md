# R283：AFI10 正式验收裁定

**Root裁定：AFI10 正式 PASS（固定本机发行组合与普通离线研究路径）。** 独立条款审计及Root原始证据核对通过，状态表与进度同步更新，正式计数由56/68增至57/68。本轮仅提交这两份裁定及两份状态文档。

| 正式条款 | 核验依据与结论 |
|---|---|
| 退役 AFI 旧领域实现、活动导入/任务/直发单和 raw-ZMQ 分支；四包不反向依赖 AFI | AFI 提交 `00cf38fd1dbcd3bd860972bda7f2a4be6f83f138`、`4e35e6023568c41e7e4c76ece849f02ad4061a61`、`f888ead54b79cef704039e09135f896c80295ec7`、`2df1a9a163817b4bafabb987409f2adcc5246466` 均为 R3b 安装源 `94386432b862fa0bfdf858033d95724ef5399169` 与普通离线 R5 源 `40262739a1f506d5289281a931d85d1d35b3fe03` 的祖先。R241 固定源码退役证据和 R254 quote-consumer/最终提交归档补足 direct writer、旧 Gateway 与 raw-ZMQ 消费路径的删除/隔离。MIG04 已接受的固定发行组合记录了组件包安装来源及普通策略环境无四包/ZMQ；Root 另做的四包对 AFI/app 的源码反向导入扫描无匹配。 |
| 构建、隔离安装并从正常入口调用 | R3b 在 Python 3.12.10 隔离环境中加载 SDK 0.15.5、AFI backend 0.1.0、Backtrader 1.4.0 wheel；正常 ServiceHost/策略入口完成订单、查询和 graceful STOP，child 正常退出。普通离线 R5 通过已安装 AFI `BacktestService` 调用实际 Backtrader/Cerebro 和本地合成 CSV。 |
| 保留的适配器不实现第二套规则/账本 | R241 的 direct-order retirement 固定源码/测试显示旧 AFI live writer 已关闭，服务调用通过受管服务路径；R3b 的订单/查询链只作为服务协议调用证据，不被表述为交易所成交或 AFI 自有 live ledger。 |
| 无组件时普通离线研究仍可用 | R5 断言无可选 `bt_api` distribution/import、无网络尝试，任务正常完成并读取本地 CSV；36 根 bar 对应 36 个 equity 点，资源清理和持久结果行由原记录/SQLite readback 支持。 |

本裁定将分散且已接受的证据合并映射到 AFI10 单行条款，不要求新增统一矩阵。R3b 与 R5 是各自固定 wheel/source 组合：不将结果外推到当前全部 checkout bytes、所有 provider/模式或真实账户。没有真实 venue/account/provider 验收，也不据此裁定 MIG01 或其他 AFI 正式用例。

## 可复核原始证据

- R3b 安装/正常服务记录：`D:\source_code\ai-for-investor\docs\ITERATION10_AFI10_INSTALLED_SERVICE_STRATEGY_20261017.md`，5677 B，SHA-256 `839b6a82389a8cb4a59c66dcc94d2fc16f5740ae4f086ca4a3ace1e356182513`。
- R3b 原始目录：`D:\source_code\ai-for-investor\.codex-tmp\afi10-local-release-candidate-r1\service-host-r3b-sdk55c-shadow-actual-r1\`。冻结 spec：55617 B，`98800d8a16060d6eeb6c7f506aa24e90b2e5a07afc40820394a939ba6a2c9863`；result：610 B，`52e398d8bfc3948610b1f83c7d4a28ccfdd675f83404604e5d94703451f6e2d0`；JUnit：415 B，`af056160e5c6c7e01c55a300dbcf0cc1f80f90e8336d6a7edf6f75f185b19186`；cleanup receipt：489 B，`6931b203c594b9b32f13763c6e9a88b5656d91d0158dac10777f17879371c24d`；stderr：434 B，`2e1044e036ff7577dc31e3ec9063c9172c4cde9332624f7bba8fb3311af4fe79`。
- R3b wheel sources: SDK commit `55c05166709aa538f43fb78805085468724d023d`, wheel SHA `69348eaeff211bf023ac6bc75cbad1d2b00ecf59905f78878b204d13808fa57c`; AFI commit `94386432b862fa0bfdf858033d95724ef5399169`, wheel SHA `ae0ed4794ab775e00f9ac64c4b2a124ca7d40c1635714ed5a41d1c668a981fd0`; Backtrader commit `4f3e6a1ca3c41c1e5a726d11cce162faec25b140`, wheel SHA `f48328ebba1d1bd7c259c135d6477e638f0e920c66f1b1ee3ab18f3721745561`.
- 普通离线 R5 记录：`D:\source_code\ai-for-investor\src\backend\scripts\acceptance\AFI10_ORDINARY_BACKTEST_SERVICE_INSTALLED_R5.md`，3034 B，SHA-256 `bad55b66ac950e47a3598b82693d9b364bd38d31a4bb3b51c1fb477416fb1ed2`。它明确说明该记录副本没有重跑，Root 消费的是固定源节点。
- R5 原始输出目录（确切路径）：`D:\source_code\ai-for-investor\.codex-tmp\afi10-local-release-candidate-r1\ordinary-backtest-service-installed-r5-qa\`。执行源 test：`ordinary-backtest-service-installed-r5\test_installed_afi_backtest_service.py`，25740 B，SHA-256 `83518b59b87bcc06f3e457fbeee08da9881f45021597eaf2ade37cacdfa1d185`；spec：`spec.json`，SHA-256 `1bd913868fbc0fe31cce094a2e0c4c3a1458970e71d16f9899b32f4fc36bcd1a`；result：857 B，`4b9b077d2990ad321d8733f49703cac98ec0bed52b3472676580b6b40cec4252`；JUnit：391 B，`985a536451bfad77a9943ef248d7c05b691467ca8707e739126f13a58b07c503`；stdout SHA-256 `4b010b210230b5f3b91899af5afa1a1eaed6ce590e6d903b6ea74545971c879a`；stderr 0 B。R5 AFI/Backtrader source HEAD 为 `40262739a1f506d5289281a931d85d1d35b3fe03` / `4f3e6a1ca3c41c1e5a726d11cce162faec25b140`，77 个 source pins 前后不变。
- 先前已接受的来源记录：R241 `evidence/afi10-live-order-retirement-r241-20261016/round-record.md` 与 `raw-evidence.zip`；R254 `evidence/afi10-service-market-quote-consumer-r254-20261016/round-record.md` 与 `raw-evidence.zip`；MIG04 固定发行组合 `evidence/mig04-formal-acceptance-20261016/root-adjudication.md`。Root 的独立 readback 还确认四个 AFI10 retirement commit 与 R3b AFI wheel 的祖先关系及四包反向导入扫描无匹配（verification tools `a9f569`, `a647d2`, `44bc89`, `a3e0c8`）。

本包只记录上述条款映射及既有原始结果；不复制大体积证据，不重复 QA/Host，不宣称通过当前整套 checkout 或真实账户场景。
