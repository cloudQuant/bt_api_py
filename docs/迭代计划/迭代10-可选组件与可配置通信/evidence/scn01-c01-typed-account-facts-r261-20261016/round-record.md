# 第261轮 SCN01 typed provider account facts — 子包提交完成，SDK 精确部分提交待 Root 核对

本轮为正常服务运行时补充 C01 所需的只读事实合同：`AccountConfigurationObservation` 与 `ExchangeServerTimeObservation`。Gateway 仅在账户只读 journal ACL 通过、会话处于当前有效 generation 时调用固定 provider fact 方法；配置只投影 `canTrade`、`canDeposit`、`canWithdraw`、`dualSidePosition`，缺失字段保持 `None`。交易所 `serverTime` 必须来自 provider 返回的 literal integer；Gateway UTC 接收时间和 provider 时间分开记录。SDK 客户端显式调用新操作，校验账户身份、generation 与注册 binding，不重试或回退到快照。

作者本机四组测试 **140/140** 通过：Base 6、Gateway 81、SDK facts client 5、SDK runtime 回归 48；三条 scoped Ruff 检查通过。pytest 有三条既有 `asyncio_default_fixture_loop_scope` 配置 warning。完整命令和 stdout/stderr 在 `raw-evidence.zip`；四个命令结果与每个成员的长度、SHA-256 已在 manifest 固定。Root 未重复这批作者 QA。

独立静态审查结果为 `GO_STATIC_TYPED_ACCOUNT_FACTS_COMPONENT_ONLY`。此结果仅认可合同/collector/client 源组件。venue adapter 尚未实现两个 provider fact 方法，缺方法明确返回 `PROVIDER_FACT_UNAVAILABLE`；后续必须完成 C01 正常 `ServiceClientRuntime`/`ServiceHost` 路径和实际 venue facts adapter。任何本机合成 Host QA 仍只算 synthetic，不是 provider/account 或正式 case PASS。

子包按本轮范围已分别提交到现有 `dev`：Base `4812cad42e67d2aad3ad5306a08f1aeef6bbf6f0`，parent `cc6036c89c534a17d93e6b793f930c576b4aa960`，仅两个 Base protocol 文件；Gateway `6d26789d9a03ad486c07153af1590be855b090a2`，parent `59b164ab7f89c6428ae6546103aad6058dab4c16`，仅本轮五个 Gateway 文件。两子包提交后 index 均为空，MIG01 市场 WIP 及其他文件保留未提交。

当前 SDK `dev` parent 为 `c61d6bcfc34e1781c6930f523195c9cbec5bc1e3`。Root 正在核对的精确 partial scope 为两个新 SDK 文件、`service_runtime.py` 和 `service_components.py` 的 R261 两个 account-facts hunk、上述两个 gitlink 和本 evidence packet。必须排除同文件 Monitor/Risk/group WIP 及 `service_registry.py` 的 AFI08/MIG03 WIP。Evidence packet 的 21 个精确文件路径以 `.gitattributes` 的逐文件 `-text` 规则保护原始哈希。本 round packet 固定原始 source baseline 与当前 delivery parent 的区别；Root postcommit receipt 负责记录最终 SDK commit SHA。

正式 SCN01 计数从 **40/68** 保持为 **40/68**，新增正式 case PASS 为 0。本轮没有启动 Host/C01Strategy、没有 provider/account/credential 操作，也没有双机验收；SCN02 九目录实际入口验收仍待后续完成。
