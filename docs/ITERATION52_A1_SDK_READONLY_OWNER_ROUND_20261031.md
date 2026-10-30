# 迭代 52 A1：SDK 只读装配与资源所有权小轮

日期：2026-10-31。范围：`bt_api/bt_api_runtime` 的 `dev`，提交 `f6f70ca4538f86b2eee68cca627a136a11299435`，父提交 `d4b55575f7eec6daf683ef2ee0c0ef4c0ccaf0fa`。

新增 SDK 只读工厂与同线程资源 owner，复用 SDK 的 runtime、账户/行情客户端和 Backtrader 的 ServiceReadProxy。对选中路由及代码持有的 SDK 映射在工厂启动前检查；普通工厂不提供该检查的保证。关闭失败或远端注销状态不确定时保留可见的不确定状态，不宣称已完成清理。SDK 加载延迟到使用时；runtime 包版本为 0.1.4。

验证：作者的源码合成 I/O 专项 17 项通过；Root 使用新建的 wheel 安装目标运行 assembly 与 SDK 合成 I/O 测试，共 55 项通过，1 条现有 Quandl 弃用警告；依赖使用本地 Backtrader 和 SDK 源码，不是隔离发布环境。Ruff 与 Git 暂存差异检查通过。独立源码审查结论仅为 `SOURCE_FAKE_SDK_READ_ONLY_COMPOSITION_ONLY`。runtime wheel SHA-256：`26ee2ec209147126de7bb624ba6ee63113121106e2e7eedf2216256717f3b14b`。关键运行模块的源码、wheel 和安装目标字节一致。测试没有连接真实账户或 provider；Python 3.10 的单独导入尝试因环境缺少 pytz 在 Backtrader 导入阶段停止，不能声明其通过。

行情来源描述符只验证 SDK 协议结构与请求键；真实来源、时钟校准、账户身份和授权仍未证明。合成测试不计入正式账户用例，也未增加迭代 41 的正式通过数量。paper/live 写入和真实账户验收不在本小轮范围。replay、shadow 和旧本地撮合未运行或验收。