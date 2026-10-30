# 迭代 52 A1：SDK 行情来源接口兼容检查

日期：2026-10-31。`bt_api_runtime` 现有 `dev` 提交 `cf992760b1050a5ca927e8fe337713b612848d6c`，父提交 `4c823a1ba5c01a0c64856360f136106a189fb5c6`；runtime wheel 版本 0.1.7。

SDK 依然延迟加载，普通 runtime 导入不要求 SDK。SDK-backed 预检区分依赖不可导入和行情来源身份 API 不兼容；前者返回 `sdk_read_dependencies_unavailable`，后者返回 `sdk_market_source_identity_unsupported`。接口检查要求 DTO 是类型且客户端构造签名显式提供预期身份参数；仅 `**kwargs` 不算支持。该检查在创建 runtime、transport 或 subscriber 之前失败关闭，只证明结构存在，不证明 SDK 的实现语义或实际来源真实性。

验收：作者 source-fake 主测试 29 passed，独立补充的仅 `**kwargs` 分支及相邻兼容用例 4 passed；Root 使用 runtime 0.1.7 和合同 0.1.4 wheel 安装目标、配合本地 SDK/Backtrader 源码组合测试 87 passed，随后单独运行新补充的 `**kwargs` 用例 1 passed，均有 1 条现有 Quandl 弃用警告。源码、wheel 与安装目标关键模块字节一致；runtime wheel SHA-256 `d8124c2d58f42c6028a29863d0da03be9577273179a4dfce4f9bf48cddcb0c1c`。独立源码审阅 GO；Ruff 和暂存差异检查通过。

Python 3.11.5 下检查了 SDK 延迟导入；Python 3.9 只做语法解析，未实际运行。Python 3.10/3.12 的单独导入检查因环境缺少 pytz 停止，不计通过。测试使用合成服务与本地源码依赖，不是隔离发行、真实 provider/账户或正式 case 验收。SDK 发布版本与完整依赖矩阵仍待单独确定。