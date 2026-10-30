# 迭代 52 A1：清理报告的范围与未决义务

日期：2026-10-31。`bt_api_runtime` 现有 `dev` 提交 `20744c78d3eade7b161115823ab56d068d5317e6`，父提交 `94a634fa6626fc921a5b215fdbef19ce5ccf8f2c`；runtime wheel 版本 0.1.9。

清理报告显式记录适用范围、必需动作和匿名逐项结果；空报告或缺少确认不再被认定为 complete。正常运行报告只描述 session 所观察的 Store/Proxy 边界，不证明 Broker、Feed 或整个 Cerebro 均已关闭。补偿清理保留 Feed 的对象身份与未决义务，即使 Feed 已从当前列表移除也不会丢失；重试时重新读取受支持的 Broker/Feed 状态，无法确认则保留 unknown。公开报告可由调用者构造，不具有来源认证、账户权限或远端清理凭证效力。

验证：作者在指定源码根运行 assembly 测试 76 passed；Root 使用 runtime 0.1.9 和合同 0.1.4 wheel 安装目标，配合本地 SDK/Backtrader 源码运行 assembly 与 SDK 合成 facade 测试，111 passed、1 条现有 Quandl 弃用警告。关键源码、wheel 和安装目标字节一致；runtime wheel SHA-256 `6709992e8baad56b7401e313159b88c62fb601907f98811638b84fa9cc59736a`。独立静态审阅、Ruff 与暂存差异检查通过。开发过程中曾误导入旧 Backtrader 导致收集失败；改正并验证源码来源后运行。另一次完整 assembly 测试为 67 passed、1 failed，因旧确认项断言未包含新 proxy 证据；核实 Store/Proxy 终态后修订断言，再通过完整测试。

此轮仅是 source-fake 与非隔离 wheel-target 证据。Broker 标志不是 lifecycle generation，不证明账户或整个框架关闭；真实 provider/账户、发行依赖矩阵和 I52-A 全部验收仍未完成。未运行 replay、shadow、旧本地撮合或真实账户，不增加正式 case 数量。