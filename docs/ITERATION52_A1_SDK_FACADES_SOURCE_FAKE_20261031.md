# I52-A：真实 SDK facade 的合成传输纵切

日期：2026-10-31。`bt_api_runtime` `dev` 提交 `d4b55575f7eec6daf683ef2ee0c0ef4c0ccaf0fa`，父提交 `02aeb67231cdf8cfc34243addd4c3ebbf01edaea`；父仓 `bt_api_py` `dev` 本轮父提交为 `deb87ecad6f130b5340a41dfc24e31afff1a6394`。父仓本轮只更新 runtime gitlink 和本记录，父仓提交哈希由包含此文件的 Git 历史确定。

新增的两个 source-fake 测试使用真实 `ServiceClientRuntime`、`ServiceAccountClient`、`ServiceMarketClient`、`ServiceRegistry`、Backtrader `ServiceReadProxy`、Store、Feed、Broker 与 Cerebro；仅 request/reply transport、subscriber 与服务响应为本地合成实现。两个环境使用同一普通 Strategy，分别接收合成 bar 并尝试一次 buy；只读 Broker 返回 `Rejected/market_data_only`，合成 transport 和 registry handler 均未收到订单/执行写请求。I52 的 `strategy/...` 引用与 SDK 所接受的引用语法由测试代码显式映射；产品仅验证 `product/synthetic_spot` 到合成 SDK spot 的映射。

另一个测试在合成 subscriber 关闭处注入一次失败：测试 owner 成组保留未确认关闭的 proxy/facades、SDK runtime、transport 和 registry；重试成功后才依次关闭 runtime 与 registry。该 owner 是测试代码，不代表生产 provider owner 已实现。Python 3.11.5 定向测试 2 passed、1 条既有 Quandl 弃用警告；独立静态审阅通过上述限定范围。

测试仅检查 runtime 向 fake transport 传递了有界 timeout 参数；fake 没有模拟阻塞或实际超时，不能证明真实超时生效。也未连接真实 Gateway、provider、账户或交易，不能证明真实资源身份、授权、paper 账户或 live 写能力。SDK facade 的可用真实工厂/拥有者、时钟合同、插件及完整 A1 仍待实现和验收。

开发诊断中先后出现导入路径、synthetic route 跨环境引用和引用格式的装配前失败，以及一项被包装的 factory 失败。原因均按测试/接口问题处理；最后确认 I52 引用带 `/` 而 SDK 的 ref 语法不接受 `/`，通过显式映射修正。没有将这些失败归因于交易所。真实账户、replay、shadow 和旧本地撮合均未运行，既有 gateway 子仓与证据记录修改保留。
