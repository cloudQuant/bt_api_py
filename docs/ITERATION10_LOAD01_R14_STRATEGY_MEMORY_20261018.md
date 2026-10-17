# LOAD01 R14：持续负载内存保护失败

日期：2026-10-18。仓库：`bt_api_py` 现有 `dev`。R14 使用 SDK HEAD `d9719ce55dfc350385d6dc3574bc80a148cca7cf` 的固定源码导出。正式状态仍为 **62/68**；LOAD01 未通过。此前已接受的支撑轮为 RPC context pool（`acd74` / SDK `9ab`）、resource diagnostics（`a70`）和 source-manifest binding（`d971`）。这些组件提交与窄测不替代本次负载验收。

## 结果

唯一试跑节点 `test_load01_hundred_strategy_sustained` 在 131.08 秒后失败。R14 pytest 输出包含 13 条 warning（1 条 pytest 配置 warning 和 12 条捕获的 Proactor warnings）。它请求 120 秒负载，未完成该时长，更未运行正式计划要求的 1,800 秒。112 个子进程、100 个策略全部 ready；启动期 lease maintenance 失败数为 0。负载目标保持 200 条上游消息/秒、每条目标 payload 约 256 字节及每账户每秒 2 次订单生命周期，原 2 GiB 可用内存保护没有降低。

25 秒和 30 秒样本的可用内存分别为 1,832,853,504 和 1,752,862,720 字节，均低于 2 GiB（2,147,483,648 字节），因此运行器按既有保护失败。策略进程聚合的 RSS 增长为 2,799,976,448 字节，private-memory 增长为 4,469,350,400 字节；增长主要发生在最初 15 秒，之后趋于平台。一次跨 2,000 个 client 的队列快照合计 2,534,325 字节、1,795 个排队项，时间跨度 15.546 秒。它不是同时快照，也不能证明内存增长原因。

正常关闭未完成。失败清理对 110 个子进程调用了 `terminate`，随后这 110 个子进程返回 `-15`；market/monitor 子进程退出码为 0，但被记录为 `normal_exit=false`。112 个 child handle 已关闭且 cleanup errors 为空。Root 随后的只读进程检查确认 112 个 child PID 和 pytest PID 均已不存在；这说明进程已清理，不把失败清理记作正常关闭。389 个源码 pin 与 7 个仓库 HEAD 前后不变。

最初的 R14 准备尝试使用 Python 3.14；stderr 未保存，首个 setup 错误原因未知，且该尝试没有开始 load、pytest 或 Host。固定导出的 no-Host preflight 另行完成：31 个 source pins、归档哈希前后相同，内存门读数为 11,740,971,008 字节，高于 10,603,200,512 字节门槛；仍未启动 Host 或 pytest。这些 setup 事实与后续 R14 负载失败分开记录。

R13 的原始失败记录和证据保持不变，见 [R13 记录](ITERATION10_LOAD01_R13_MEMORY_LIMIT_20261017.md)。本次 R14 没有增加正式 PASS；不据此给出内存根因或 30 分钟性能结论，也不原样重跑或启动 1,800 秒负载。此前支持轮与 helper-only QA 见 [RPC context pool](ITERATION10_RPC_CONTEXT_FACTORY_20261017.md)、[resource diagnostics](ITERATION10_LOAD01_RESOURCE_DIAGNOSTICS_20261018.md)、[manifest binding](ITERATION10_LOAD01_MANIFEST_HELPER_20261018.md) 及 [Registry projection cache](ITERATION10_LOAD01_REGISTRY_PROJECTION_CACHE_20261017.md)。

## 原始证据

[Root evidence ZIP](evidence/iteration10-load01-r14-strategy-memory-20261018/root-evidence.zip)：204,178 字节，SHA-256 `03dabdc922c6736fca20396e0a6135d049b572c145c9cfa80807a0f298f6d5de`。它包括 R14 实际输出目录中的 11 个 generic 运行文件、独立的 pilot result、prepared spec、Root launcher、前后内存/PID 收据、source-export pins、preflight 和 no-Host recovery preflight。Root execution spec 与 R14 的 `spec.json` 逐字节相同，因此只保留一份；未混入旧 helper-only QA 文件。ZIP 不含 56 MB parent archive 或 SQLite 数据库。

下表列出 ZIP 成员及其源文件 SHA-256。19/19 个成员均已与所列源文件逐字节比较。`generic-qa/` 与 `pilot/` 成员均来自 `D:/temp/iteration10-load01-pilot-20261018-r14-current-dev/`；其余来源对应 prepared spec、Root `.codex-tmp` 收据及固定导出目录。

| ZIP 成员 | 字节 | SHA-256 |
|---|---:|---|
| `generic-qa/spec.json` | 76,852 | `6f0931120a02cd687a8aa997c2242e97a86a9aae84618852389eec0a8193733b` |
| `generic-qa/command.json` | 1,901 | `e45742bdf31eaf9190082425d72374c30ccea2282013d5f1fd3d081f275a7b8f` |
| `generic-qa/running.json` | 76 | `64034a89d88cb75ce237819e837e5c14bf6022ac46888cb84c1c3820c355c232` |
| `generic-qa/runner.py` | 3,204 | `8b7f470be77270adeb9d72a83776b827c4653004b39835eeca0f43bf63cf24ed` |
| `generic-qa/source-before.json` | 72,998 | `72fb5e8d6648b9530bd67d5352ac0031560df5ee5d0d49857ac39020ad0c3321` |
| `generic-qa/source-after.json` | 72,998 | `72fb5e8d6648b9530bd67d5352ac0031560df5ee5d0d49857ac39020ad0c3321` |
| `generic-qa/result.json` | 1,687 | `bcb1df88dba3b73da256028223767bf5603fb7052f0bbd2f12d479337f02e55e` |
| `generic-qa/stdout.log` | 5,496 | `9abfd308979db47a84929ebb51bf753eb9dae9b3fab7d6c58fca16ddfe4a87d9` |
| `generic-qa/stderr.log` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `generic-qa/exit.txt` | 3 | `f1b2f662800122bed0ff255693df89c4487fbdcf453d3524a42d4ec20c3d9c04` |
| `generic-qa/junit.xml` | 708 | `a81119e1e9d2e3eedbeffffdf326f67fba6d898f701fc71a9f73957409714110` |
| `pilot/load01-pilot-result.json` | 2,231,414 | `649b3566146b3c06f66f5470262810d903465380bcf0b1639f7801078e934ebb` |
| `spec/prepared-spec.json` | 23,378 | `d3e0bf652d2728222187253ba7e49bcd8f218711d29fd5adbdf0cc12b8a4c674` |
| `spec/root-launch.py` | 5,940 | `216b606cb545a5a58a9c4dd671958f7728791c0eeb8ed49328aa4a1a5b83e183` |
| `memory/pre-run-receipt.json` | 593 | `d674c68a5e527b51890874f7bef6815ac62d681e5e799354e6cf1c1c2399d2d5` |
| `memory/postrun-pid-summary.json` | 871,403 | `5ccf88e5c09c7338243fbcdac0641efb7f7a6635394a5c28bc69b9241159b22e` |
| `source/source-export-pins.json` | 4,675 | `b64a2011d48ce5efd1dbc5f333848a2b71e5a4374bccb3cd38ea2ac28788e69c` |
| `source/preflight.json` | 4,887 | `3687bc3334343bfc8f1d49a0b2cd80eadb3b11e45746aaf3e7019049eaf6743a` |
| `source/nohost-reuse-preflight.json` | 2,013 | `564a35a3207cf6b11c92c9674e552f76227a8f904b2a880a9aae68fc8cb1878d` |
