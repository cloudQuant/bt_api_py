# I52-A1：符号路由匹配小轮记录

日期：2026-10-30。范围：`bt_api_runtime` 独立 `contracts/` 中的纯内存路由匹配与调用前重验，不含 provider、账户连接、插件加载或许可签发。

- 仓库/分支：`bt_api_runtime` / `dev`，源码父提交 `0e9ba0727d49ba0a23d6d4717f512ac79315d038`，路由合同提交 `0278b3b`；其后 `48057b1` 更新该仓 README，澄清符号匹配不证明真实身份。
- 交付：部署代码显式提供的符号路由表可区分 paper/live 并逐项匹配策略、provider、endpoint、账户、凭据与 scope 引用；同 provider/environment 不同账户可共存。重复绑定或跨环境复用环境敏感引用拒绝；当前启用插件明确报延期，不默默忽略。`validate_plan(plan, config, registry)` 重验原配置及插件限制并比对计划。对象的常规 repr、JSON、pickle/复制不输出引用，但 Python 反射不是安全边界。
- 源码/安装检查：Python 3.8.20 与 3.11.5 源码测试各 `33 passed`；独立审查复核了重复/错配、伪造计划、插件绕过及常规序列化边界。由源码构建 `bt_api_run_contract-0.1.1-py3-none-any.whl`，SHA-256 `b85a85bee9aaccd5b2710e3b56ce50a5a1ee2d41ff61787f6c34f0fa04317422`；两个隔离安装环境各运行 33 项测试、`pip check`，并核对四份源码/wheel/安装载荷和独立导入位置。此为本机 source/installed 小轮，不是发行或账户证据。
- 边界：注册表只是调用方给出的符号数据，不能证明 endpoint/账户/凭据的真实来源、provider 兼容性、scope 权限或交易授权；也未实现可信工厂绑定、插件实际装配、完整 Cerebro 下单链和真实 paper 账户。I52-A 全部清点与统一合同仍未完成。
- 保留：父仓既有 gateway 子仓工作区与 evidence JSON 修改未纳入；replay、shadow、旧本地撮合未运行或验收，生产写入未授权。

本轮父仓仅更新 `bt_api_runtime` gitlink、README 状态和本记录；具体父仓 commit 由 Git 历史查询。
