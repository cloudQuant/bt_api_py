# 迭代 52 A1：v2 工厂路由预检

日期：2026-10-31。`bt_api_runtime` 现有 `dev` 提交 `94a634fa6626fc921a5b215fdbef19ce5ccf8f2c`，父提交 `cf992760b1050a5ca927e8fe337713b612848d6c`；runtime wheel 版本 0.1.8。

schema v2 的 provider factory 必须提供 `preflight(selected_route)` 且严格返回 `True`。预检缺失、不可调用、拒绝或普通异常时，在调用 factory 和外部 I/O 前以有限错误码失败；控制异常原样传播。schema v1 继续使用既有可选预检路径。代码持有的预检只检查本地绑定一致性，不证明真实 provider 身份、账户权限或写许可。

验收：作者的源码组合测试 99 passed；Root 使用 runtime 0.1.8 与合同 0.1.4 的 wheel 安装目标，加本地 SDK/Backtrader 源码组合测试 99 passed、1 条现有 Quandl 弃用警告。关键源码、wheel 和安装目标字节一致；runtime wheel SHA-256 `750e499940fa97fde9c4f1abec1291ec08ab1d6a7fac9ef7c057a708d7b08f3a`。独立静态审阅 GO；Ruff 和暂存差异检查通过。Black 检查报告原文件中的既有格式差异，本轮未全文件改写。

结果仅为 source-fake 与非隔离 wheel-target 证据；SDK、本地源码依赖和真实 provider/account 不在隔离发行验收范围。未运行 replay、shadow、旧本地撮合或真实账户，不增加正式 case 数量。