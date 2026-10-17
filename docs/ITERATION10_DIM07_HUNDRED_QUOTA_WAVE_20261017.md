# DIM07 百策略共享配额波固定来源验收（2026-10-17）

Root 在现有 `bt_api_py` dev 的 SDK HEAD `77a06de88dba6d1864eb92ae02f68a310af7bbd5` 上，按用户授权回收 ChatGPT、Codex 与 Chrome 工作集（未 kill 进程），连续四次可用内存采样为 12.27–12.47 GB 后启动本轮。测试启动记录的可用内存为 12,368,101,376 B，高于门槛 10,603,200,512 B。门槛只作为启动条件，不作为功能验收证据。

固定来源唯一节点 `tests/integration/test_gateway_quota_hot_wave.py::test_hundred_strategy_processes_share_fair_quota_with_bounded_deadlines` 由 Root 执行一次：1 passed，pytest 总耗时 41.80 s，exit 0；保留 2 条 pytest warning，stderr 为空。运行使用 120 个来源 pin；执行前后来源 map 完全一致，SDK/Base/Gateway 三个 HEAD 均未变化。固定 spec 为 `.codex-tmp/iteration10-dim07-hundred-strategy-wave-r6-head77a06de8-20261017.json`，SHA-256 `4c95a583da1d521603f132e8df74fd2eb9f4d35af9eb90d4aaef287648741123`；原始运行目录为 `D:/temp/iteration10-dim07-hundred-strategy-wave-root-current-r6-f95d2056-output`。

JUnit 的本轮指标如下：

| 指标 | 观测值 |
|---|---:|
| 合成策略进程 | 100 |
| `ctp_query` 配额请求 | 200 |
| 授予 | 100；100 个策略槽本轮各获得 1 次 |
| 截止时间拒绝 | 100 |
| 授予延迟 p95 | 23,718 ms |
| 拒绝延迟 p95 | 30,062 ms |
| 子进程清理升级 / 未解决 custody / 清理错误 / 队列清理错误 | 均为空 |

每个策略槽恰好获得一次授予是该次合成运行的计数结果；它不证明不同运行间的调度顺序公平，也不独立证明每队列等待时延。p95 是该测试报告的授予/拒绝延迟，不能外推成真实 CTP 延迟或单队列独立测量。此节点没有连接真实 provider、CTP、账户或 ServiceHost，也没有构造真实 HTTP 429 响应。Gateway 配额与撤单保留容量、R291 四进程重连等其他证据仍按各自固定范围引用；它们与本轮合并后仍不足以据此声称完整 DIM07 正式通过。正式计数保持 **61/68**。

本轮 pytest 原始 warning 为：未识别配置项 `asyncio_default_fixture_loop_scope`，以及 `record_property` 与 JUnit `xunit2` family 不兼容。两项均保留在原始 stdout 中，未修改或过滤。

原始 11 件运行输出按原文件字节保存于 [root-qa.zip](evidence/iteration10-dim07-hundred-quota-20261017/root-qa.zip)，压缩包为 156,772 B，SHA-256 `aec01b649f7a6f4a0a56b24f16e80a947c7952a659bdfc7c0deee13a624a96de`。创建后逐项与运行目录原字节核对一致：

| 原始文件 | 字节 | SHA-256 |
|---|---:|---|
| `command.json` | 1,336 | `3aa5f195e71d93486335d3d0bdc16ec674deb55c3d2bc40f223321250070d8aa` |
| `exit.txt` | 3 | `13bf7b3039c63bf5a50491fa3cfd8eb4e699d1ba1436315aef9cbe5711530354` |
| `junit.xml` | 1,599 | `5950ae3757b61a3a53d092d48f298705e0e2b7ee123d85f678e9b9bac733967f` |
| `result.json` | 962 | `ef84dc086c968163f628d15e971bc02fdb2ededbb03bd0a948c2ee92ff753d7c` |
| `runner.py` | 3,204 | `8b7f470be77270adeb9d72a83776b827c4653004b39835eeca0f43bf63cf24ed` |
| `running.json` | 76 | `7208ab70d2e454bc6a1bbd1ef4ab274396252596b39293ce3e0cd27d7f3e50f0` |
| `source-after.json` | 26,441 | `62372ced5a6fd174f461bfb854e7888d08264a6f231dcf54f6ccab7aeafc19ff` |
| `source-before.json` | 26,441 | `62372ced5a6fd174f461bfb854e7888d08264a6f231dcf54f6ccab7aeafc19ff` |
| `spec.json` | 94,622 | `4c95a583da1d521603f132e8df74fd2eb9f4d35af9eb90d4aaef287648741123` |
| `stderr.log` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `stdout.log` | 980 | `3f5ae8d981d1eb3333a5c21e7f9fc441a61b8f6565fc31ed6d2343c376af1300` |

本轮是固定来源的本机合成配额波局部验收，不替代正式计划要求的完整 DIM07 覆盖；原先失败记录保持不变，没有为交付重跑本节点。
