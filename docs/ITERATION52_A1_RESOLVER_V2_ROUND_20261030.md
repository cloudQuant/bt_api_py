# I52-A：版本二静态路由源码小轮

日期：2026-10-30。子仓 `bt_api_runtime` `dev` 提交 `5e3a90d273577622e31ca6bb1cd3d3a5dab364d2`，父提交 `0eff9b6ef1af12f7a25b740c63c3324bd39eaf30`；父仓 `bt_api_py` `dev` 本轮父提交为 `b52fe4eb40fdfcd17f29feb77b9af75b6daa59fe`。父仓本轮只更新 runtime gitlink 和本记录，父仓提交哈希由包含本文件的 Git 历史确定。

新版 `resolve_plan`/`validate_plan` 在同一入口分派 v1/v2。v2 根据环境与九个符号引用整体匹配 route，检查顶层策略参数声明和只读能力声明；写能力及启用但尚无解析器的插件明确延期。计划重验区分参数的精确类型和浮点正负零。静态 registry 由调用方提供，只能证明符号与声明匹配，不能证明真实 provider、账户、凭据、产品、数据源或时钟身份，也不授予权限。嵌套参数只完成结构和顶层类型检查，业务字段规则仍待策略描述符支持。

验证与边界：

- Python 3.8.20 与 3.11.5 源码合同 suite 各 56 passed；独立只读审阅通过静态路由范围，包含 bool/int、int/float、嵌套类型与正负零计划篡改检查。审阅提出的 `_select_route` 类型注解仅描述 v1 的问题列为后续非阻断维护项。
- 合同 0.1.3 wheel SHA-256 `498dfddebd85cb9b7ff4f32c44da0b35a8b0da82e3a559ad3e4ee31ccf0859d1`；分别安装到现有 Python 3.8/3.11 的独立目标目录后，各 56 项合同 unittest 通过。
- runtime 0.1.2 固定依赖合同 0.1.3，wheel SHA-256 `c1d38a09acd60e06fa2dc0c42ac371c1f3dbfc7a1d796e926dd92c5c8327478f`。两个 wheel 安装到相同目标目录、Backtrader 与 SDK base 从本地源码导入时，26 项装配测试通过，1 条 Quandl 弃用警告。新增测试验证有效 v2 配置被现有 v1 装配在 provider factory 调用前拒绝。wheel、安装目录与源码的 Python payload 一致，依赖元数据固定 `bt-api-run-contract==0.1.3`。
- 以上不是隔离 venv、完整依赖解析、发行或账户验收。v2 的真实 runtime 装配、可信实际资源身份、plugin 及写能力授权、策略参数嵌套业务校验、provider/account 和正式 case 均未完成。本轮未运行 replay、shadow、旧本地撮合或账户交易。

初始 Python 3.14 排查测试可能更新了既存未跟踪 pycache；生成缓存保留且未纳入提交。既有 gateway 子仓与证据记录修改保持原样。
