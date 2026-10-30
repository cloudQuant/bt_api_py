# 迭代 52 A1：账户与行情双 Host 绑定

日期：2026-10-31。仓库：`bt_api_runtime` 的既有 `dev`；基线 `da5961dbe4473403caabbd0299c327174d006803`，提交 `4236fa7c2e540196013abf2f343fbbf0f64231d2`。

标准 SDK Host 分别提供账户和行情角色，已有 SDK runtime 支持多服务 binding；原只读工厂却要求两者共用服务实例。V2 工厂现支持由可信代码指定账户、行情 target 对，在单一 runtime、owner 线程和 run ID 下分别验证服务、实例、scope、transport、peer、endpoint 与精确操作集。旧单 Host 配置归一化到同一逻辑路径；当前账户和行情两项仍均为必需。

配置中的 endpoint/credential 是逻辑部署 bundle 引用；每端物理目标由可信 target 对绑定。通信 options 仅受现有 SDK parser 约束，不是授权或可信身份凭证。独立账户 profile 不能附加未用行情 endpoint；共享 profile 必须与行情目标一致。清理以 SDK 公共状态和未确认计数判断，历史累计失败数仅为诊断；远端注销未知时保留 owner 待对账，不保证重试成功。

独立源码审阅通过，修正了累计 cleanup_failures 被误判为未决以及独立账户 profile 多余行情地址两项问题。最终受影响源码模拟测试 65 passed；从最终源码构建 `bt-api-runtime` 0.1.12 并与合同包 0.1.5 安装到单独目标目录后，受影响 runtime 两份测试 148 passed、1 条既有 Quandl 弃用警告。源码、wheel 和目标安装的相关模块字节一致，依赖 metadata 匹配；wheel SHA-256 `6a11e9cdc6ecd81e13498ad0a17aa39b6fc2964a059e7fbda46b4a7baf5c7898`。目标安装依赖本地 SDK/Backtrader，不是隔离环境或发行验收。

首次两次测试收集未进入用例：一次误导入旧安装版 Backtrader，一次遗漏合同包源码根；最终固定并核验五个源码根后完成测试。早期一项新测试错误假设账户 facade 构造即刷新快照，修正测试期望后通过。Ruff、py_compile 和 diff-check 通过。

本轮是 source/fake 及目标安装一致性证据；未用两个真实 Host/网络完成纵切，未证明只读 permit、远端续租/清理、真实 provider 或账户授权，也未增加正式 case PASS。I52-A 仍未完成。
