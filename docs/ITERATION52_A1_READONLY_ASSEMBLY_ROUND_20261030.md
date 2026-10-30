# I52-A：只读 paper/live 装配源码小轮

日期：2026-10-30。SDK 父仓库 `bt_api_py` 在 `dev`，本记录对应提交之前的父提交为 `6ee75583`。本轮先提交子仓 `bt_api_runtime` `dev`：`a8e224b9fb8b268b3b4b0eadba75f1ffe386b6d3`，父提交 `48057b1aebb964421d74aa3bc86fdc34ec6c7528`；父仓本轮仅更新该 gitlink、README 和本记录。父仓提交哈希由包含本文件的 Git 历史确定。

## 范围和验证

- 新 `runtime/` build root 将同一注册策略和可信代码绑定接到真实 Backtrader Cerebro、BtApiStore、BtApiFeed、BtApiBroker 与 ServiceReadProxy；paper/live 选择符号 route，适配器只提供只读 bar 和账户观察，不授权订单。factory 属于部署信任边界；现无真实 SDK facade 工厂。
- Backtrader `dev` `0f88508d3` 提供连接前 `supports_bar_history` 查询；旧同版本分发缺少该 API 时在工厂调用前失败。tick、盘口、错误 bar 周期不会触发连接或订阅。模式和符号引用不证明 endpoint、账户、凭据身份或许可。
- Python 3.11.5 源码定向检查 25 passed，1 个上游 Quandl 弃用警告；black、ruff 通过，独立源码审阅通过。fake 仅在外层 account/market facade；没有真实 provider、账户或交易操作。
- wheel SHA-256 `f06c318e65efd756d8ae38b3fd573fd94f1e13220c0bf5d2c147829fe9c248a8`，安装到独立 target 后以本地 Backtrader、合同包和 SDK 基础源码为依赖，同一测试 25 passed、1 warning；两个 Python payload 的源码、wheel 和安装字节一致。该检查不是隔离 venv、完整依赖解析、发行或部署验收。
- Strategy 启动异常的策略自有资源清理、可信实际资源身份、完整生命周期、真实 paper 账户与 live 写权限仍待独立实现/验收；本轮不代表 A0/A1 完成。replay、shadow 和旧本地撮合未运行。

本轮生成的 build/egg-info 缓存未纳入提交；父仓既有 gateway 子仓和证据记录工作区修改保持原样。
