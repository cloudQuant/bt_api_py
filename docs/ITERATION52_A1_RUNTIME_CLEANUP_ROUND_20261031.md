# 迭代 52 A1：运行时失败与清理报告

日期：2026-10-31。`bt_api_runtime` 现有 `dev`，本轮提交 `46a2ace5d3662933ec68c111d4268c22953d5884`，父提交 `7b05ffc6e07a86318a70d4f8e8144ffb48737b0d`；runtime wheel 版本 0.1.5。

运行或装配已有主异常时保留其身份；以有限步骤码报告清理失败与不确定状态，报告附加到异常是尽力而为，并可由 session 读取。已有主异常优先于清理期间的控制异常；无主异常时按原类型传播清理中的 KeyboardInterrupt、SystemExit 或 GeneratorExit。Store 状态未知时保留 Store/Proxy，不直接断开；显式 owner-thread 重试会跳过本 session 已确认返回的 Broker.stop，仍可重试未确认的 Store。异常底层文本不进入清理报告。

验证：作者定向 assembly 源码测试 58 passed；Root 用 runtime 0.1.5 和合同 0.1.4 的 wheel 安装目标，配合本地 Backtrader/SDK 源码，运行 assembly 与 SDK synthetic facade 两个测试文件，75 passed、1 条现有 Quandl 弃用警告。runtime wheel SHA-256 `607d66472c492bf551feafe070f270915e7699c90d90dfda909d7c93ee5f0953`；关键源码、wheel 与安装目标字节一致。Ruff、Git 暂存差异检查和独立源码审阅通过。此结果是 source/fake 加 wheel-target 组合，依赖并非隔离安装；不是 provider、真实账户或交易验收。

报告描述最近一次清理尝试，既有错误码仍表示此前清理失败；报告不可附加时保留主异常，不保证调用方能通过异常取得清理报告。新增 retry 测试中的首次 Broker.stop 在执行原实现之前抛错，未覆盖自定义 Broker 已部分执行后抛错；不能据此推断远端资源状态。没有运行 replay、shadow、旧本地撮合或真实账户操作，正式 case 数量未变化。