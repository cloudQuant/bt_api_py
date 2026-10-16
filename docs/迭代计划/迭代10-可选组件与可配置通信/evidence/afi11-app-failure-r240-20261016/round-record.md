# R240：AFI11 应用进程故障与恢复源码包

本包包含一个 SDK 集成测试源码候选，以及三份证据文件。唯一源码是 `tests/integration/test_afi11_service_failure_isolation.py`，56055 bytes，SHA-256 `b4f22c31a11bcea1f86c354c796715a4bad5e89e3b8a24599c92b573c20d7cf7`。作者仅运行了 `test_afi11_app_process_failure_recovery_keeps_strategy_on_same_owner`：1 collected / 1 passed，0 failures、errors、skips，pytest exit 0，43.722 秒；有一条既有 Backtrader Quandl deprecation warning。证据等级为作者本机 fixed-export + synthetic-provider 单节点，不是 Root QA。

场景启动了真实的测试自有 AFI FastAPI/Uvicorn 进程和 SDK `ServiceHost`，后者使用既有 durable Risk admission。测试终止并回收精确捕获的 app-A PID，确认相同 Strategy session 继续连接相同 owner 与 policy。缺少已登记 instrument facts 的请求在 provider dispatch 前被拒绝；AFI app 不可用时，同 owner 仍 ACK 一笔合成 BTC-USDT 意图。随后以不同 PID 启动 app B，HTTP 恢复读取 owner 侧两条记录。合成 provider 的构造/session-open 计数始终为 1，仅成功意图被提交。

通过节点中的清理断言记录：app A 精确 PID terminate/join 后非零退出，控制管道和 Process 句柄关闭；app B 正常关闭回执为 `(True, True, True, True, True, 1)` 且子进程 exit 0；Strategy runtime 的 cleanup failures 与 unregister uncertainties 都为 0；owner 正常关闭并 exit 0；最终 cleanup error 列表为空。这些来自测试断言，没有独立清理遥测 JSON。

`raw-r5.zip` 保留 R5 的 11 个原始文件：command、exit、JUnit、result、runner、running marker、source-before/after、spec、stderr、stdout。ZIP 为 408940 bytes / SHA-256 `65ffe000a949d93fd60026d92c419a544eb23328193a3ec5ecdd0492ad7bd704`；逐成员精确长度和 SHA 在 manifest。`pytest-tmp` SQLite/scratch 被排除。R5 的 2,894 个源码输入前后快照相同，7 个仓库 HEAD 前后相同。manifest SHA-256 `058e05bd22f73fe5144319dd80392d98ee1a90f5403bdc3e3855552e5789a9ac`。manifest 同时精确列出保留的 Root R2–R8 与作者 R1–R4 失败输出路径及 spec/result/JUnit hashes。

本轮不增加正式 AFI11 通过数，也不证明 Monitor 独立故障隔离、analytics-worker failure/timeout、真实账户或生产 provider 行为。没有 AFI 生产源码修改。建议父审阅/后续 scoped stage 的精确集合为一个测试源码加本目录三份 packet 文件；当前未 stage/commit，进度 tracker 未修改。