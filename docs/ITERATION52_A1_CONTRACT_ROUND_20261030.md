# I52-A1：配置合同基础小轮记录

日期：2026-10-30。范围：`bt_api_runtime` 的独立 `contracts/` 源码及文档；不含服务编排、账户接入或实际交易。

- 仓库/分支：`bt_api_runtime` / `dev`；父提交 `b760396918927c738275dff7b4040ff25bb8af28`，本轮提交 `0e9ba07`。
- 交付：独立 `bt-api-run-contract` wheel 的纯数据 YAML 解析候选。它校验 schema、paper/live 环境、引用格式、插件布尔值和 YAML 约束；旧 schema 不自动转换，replay/shadow 明确延期。解析不读取文件、凭据或网络，也不加载策略或 provider。DTO 不是来源证明或授权。
- 源码证据：Python 3.8.20 和 3.11.5 下各 16 项边界测试通过；独立审查后修复了对象构造、引用 repr、类型子类及 YAML 显式 tag 边界。测试不运行 replay/shadow，也不连接账户。
- 安装证据：从本轮源码构建 `bt_api_run_contract-0.1.0-py3-none-any.whl`，SHA-256 `59527b7f797ad2cbbf4f0d570842c8f8cfd5d672b02539bf36fd23a2695295dc`；分别在不安装 Backtrader、bt_api_py、bt_api_base、bt_api_runtime 的 Python 3.8/3.11 隔离环境安装；各 16 项安装后测试、`pip check`、三份源码/wheel/安装载荷与导入位置检查通过。此为本机安装证据，不是发布、账户或运行链路验收。
- 排除项：早期一次 wheel 命令误把 `contracts` 作为包名而非本地 `./contracts`，下载并构建了同名外部包，未用于本轮源码或安装证据；以上 wheel 由明确的本地 `./contracts` 构建。
- 未完成：可信注册表必须独立绑定 provider、实际账户、endpoint、credential 与 scope 的真实环境；插件可用性、参数/产品/能力合同、配置来源、授权、Backtrader/runtime 安装依赖、Cerebro 纵切及真实 paper 账户均未完成。单纯 mode/environment 字符串相同不能证明引用匹配，配置通过不允许派单。release/license 策略、其他 Python/OS 安装矩阵与完整 A0/A1 仍待核验。

父仓库本轮仅更新 `bt_api_runtime` gitlink、对应 README 状态和本记录；既有 gateway 子仓及证据文件未提交更改保持原样。
