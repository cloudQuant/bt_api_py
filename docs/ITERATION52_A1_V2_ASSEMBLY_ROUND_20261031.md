# I52-A：版本二只读装配源码小轮

日期：2026-10-31。子仓 `bt_api_runtime` `dev` 提交 `02aeb67231cdf8cfc34243addd4c3ebbf01edaea`，父提交 `5e3a90d273577622e31ca6bb1cd3d3a5dab364d2`；父仓 `bt_api_py` `dev` 本轮父提交为 `546f9e15d29269ceaec1bd9a7aa8e33e54d91018`。父仓本轮只更新 runtime gitlink 和本记录，父仓提交哈希由包含本文件的 Git 历史确定。

`assemble_run` 支持 v1 及 v2 只读装配。v2 将已校验的 environment 与九项引用整体绑定到代码提供的策略类、Feed 和 `ServiceReadProxy` 工厂，同一 strategy ref 在 paper/live 使用同一策略类。参数深拷贝为普通 Python 值再传策略；缺少 `market.read` 或 `account.read`、错配 route/mode/参数、申请写入或启用未实现插件均在工厂前拒绝。静态绑定不是实际 provider/account/endpoint/credential 身份或许可，策略参数内部业务约束仍由真实策略描述符决定。

验证与限制：

- Python 3.11.5 源码测试 38 passed；独立只读源码审阅通过。两种模式使用真实 Cerebro、BtApiStore、BtApiFeed、BtApiBroker 和 ServiceReadProxy，fake 只用于外层 account/market facade。首次未指定源码路径的测试误导入旧 Backtrader，在收集阶段失败；重新指定来源后通过，不将首次失败视为代码测试通过。
- 合同 0.1.4 wheel SHA-256 `bf02fec88e160c05e528b9c1fee6ef939e37ae3ed10dce3186bc716f18bf0ab9`；分别安装到现有 Python 3.8/3.11 的目标目录后，各 56 项合同 unittest 通过。
- runtime 0.1.3 固定依赖合同 0.1.4，wheel SHA-256 `fb9aade281c3442e8c9c343f320b42314cf9c3f632090864940a8ee861262e31`。两个 wheel 同目标安装，并通过本地 Backtrader/SDK base 源码运行装配 suite：38 passed，1 条 Quandl 弃用警告。wheel、安装目标和源码 Python payload 字节一致；固定依赖元数据经核对。
- 安装目标检查使用现有解释器环境，不是隔离 venv、完整依赖解析、发行或部署验收。真实 SDK facade 工厂、真实资源身份、授权、订单写入与真实 paper/live 账户、部分 Strategy 启动清理和完整 A1 仍未完成。

清理本轮生成的测试缓存时自动审批策略拒绝删除；缓存保留、未纳入提交。既有 gateway 子仓和证据记录修改保留；本轮没有运行 replay、shadow、旧本地撮合或账户交易。
