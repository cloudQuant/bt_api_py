# 迭代 52 A1：运行时行情来源声明接入

日期：2026-10-31。`bt_api_runtime` 现有 `dev` 提交 `4c823a1ba5c01a0c64856360f136106a189fb5c6`，父提交 `46a2ace5d3662933ec68c111d4268c22953d5884`；SDK 父仓行情客户端功能依据 `96e4e48962e8ccd3235949478818a67e2f8f8b5b`。runtime wheel 版本 0.1.6。

SDK 只读 owner 的 code-owned spec 必须显式给出 source、clock domain、clock session 和 clock source 期望，在创建 runtime/transport/subscriber 前检查其格式并传给真实 SDK 行情客户端。单例首次订阅和恢复通过 SDK 共享检查；四字段各自错配的合成用例均在策略首 bar 与 history 请求之前拒绝，恢复错配后不交付第二根 bar。直接调用 SDK recover 失败后，调用方仍须关闭 owner；组合测试由运行清理完成。批量路径按 SDK 前一轮已明确的行为处理。

验证：作者的 source-fake 纵切 27 passed；Root 使用 runtime 0.1.6 和合同 0.1.4 wheel 安装目标，加本地 SDK/Backtrader 源码组合运行 85 passed、1 条现有 Quandl 弃用警告；模块来源、源码/wheel/安装目标关键字节一致。runtime wheel SHA-256 `b8f0cfde3ba35aed74f86c02e80a3ee9ee65fbacbb83e67f798a0773540bc225`。独立源码审阅 GO；Ruff 和暂存差异检查通过。首次源码测试因导入旧版 site-packages Backtrader 而在收集前失败，指定当前源码路径后才通过。此为 source-fake 与非隔离 wheel-target 证据，没有连接真实 provider 或账户。

descriptor 字段匹配只证明返回声明与部署侧独立预期一致，不证明预期来源权威、真实 provider 身份、授权、数据真伪或时钟校准。runtime 必须配合具有该公开 API 的 SDK 版本；正式发布/依赖矩阵仍未验收。未运行 replay、shadow 或旧本地撮合，不增加正式账户 case。