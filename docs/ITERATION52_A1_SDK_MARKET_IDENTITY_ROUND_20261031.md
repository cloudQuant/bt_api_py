# 迭代 52 A1：SDK 行情来源声明一致性

日期：2026-10-31。仓库 `D:/bt_api_py`、分支 `dev`，本轮父提交 `0d6226d5`；提交哈希以 Git 历史为准。本轮只修改公开行情客户端和其单元测试，并记录本说明。

新增由部署代码显式提供的不可变 `ExpectedMarketSourceIdentity`，比较响应描述符的 source、clock domain、clock session 和 clock source。预期不从响应推导；提供预期必须同时要求 descriptor。单订阅和恢复在请求快照前校验；支持批量启动时先校验整组 descriptor，再解析或交付响应内的快照。批量接口缺失且任一成员要求预期校验时提前拒绝；未提供预期的旧逐个启动路径保持原行为。

验收：作者定向源码测试 35 passed；Root 在本地源码上组合运行市场与历史 facade 测试 43 passed、1 条现有 pytest 配置警告。独立静态审阅认可来源字段、恢复、批量和兼容分支；Ruff 与暂存差异检查通过。默认 pytest 插件曾在收集阶段因环境兼容问题失败，禁用非必要 asyncio 插件后才执行通过；该收集失败不计通过。所有测试使用合成服务，未连接 provider 或账户。

声明一致不证明 provider 身份、授权、行情真伪、数据新鲜度或时钟校准。动态 clock session 必须由部署侧独立提供可信预期。`bt_api_runtime` 尚未在该轮接入该期望，历史/实时数据进入策略前的组合验收仍待单独完成；不计正式账户 case。