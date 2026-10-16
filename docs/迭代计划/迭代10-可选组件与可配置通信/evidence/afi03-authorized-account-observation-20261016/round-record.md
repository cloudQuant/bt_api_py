# AFI03 R222：授权 AccountObservation 投影

状态：源码候选已通过本轮定向合成验收，待 Root 对 AFI `dev` 做九文件精确提交。本记录与 `manifest.json` 仅在 bt_api_py 证据目录；AFI 仓库本轮只提交清单列出的九个源码/测试文件。

本轮把已授权的账户观察从真实 ServiceHost / ServiceAccountClient 链路投影到 portfolio API 和组合页。账户总额保持既有口径；原币种、数量单位、均价标签、coverage、cut、consistency 与异常/陈旧状态明确保留。不会对外泄露完整 AccountKey 或内部 execution scope，也不将账户资金合并进账户总额、不做 FX 估值。

Root 定向节点结果：

- `client-r1`：授权 provider observation 的 ServiceAccountClient 投影测试 1 passed，exit 0，无警告。输出：`D:\temp\iteration10-afi03-client-root-20261016-r1`。
- `api-r1`：portfolio compositions HTTP API 投影测试 1 passed，exit 0，1 条继承的 Quandl deprecation warning。输出：`D:\temp\iteration10-afi03-api-root-20261016-r1`。
- `ui-r2`：选定 Vitest 1 passed、37 filtered/skipped；`vue-tsc --noEmit` exit 0。输出：`D:\temp\iteration10-afi03-ui-root-20261016-r2`。
- `http-r13`：正常 SDK ServiceHost → authorized ServiceAccountClient → Portfolio API 的合成 HTTP 链 1 passed，exit 0，1 条继承的 Quandl deprecation warning；2,772 个源码 pin 前后未变、五个仓库 HEAD 前后未变。JUnit 总用时 4.752 秒，测试 2.751 秒。输出：`D:\temp\iteration10-afi03-http-root-20261016-r13-sdk-f29-afibase-r1`，固定 spec：`D:\bt_api_py\.codex-tmp\iteration10-afi03-http-root-spec-r13-sdk-f29-afibase-20261016.json`（SHA-256 `ab2b517030d46f3be4058500ecf322d8ffcb20f98164029b5bda3fca490568c2`）。

R13 的测试通过后断言了模拟 Gateway close ack、provider connect/read/close、Host/registry/worker/guard/journal 关闭、正常退出码 0、无 forced cleanup，以及 AFI deployment/app DB 清理。`cleanup-assertions.json` 精确列出断言来源；这不是独立抓取的 close-ack 回执，原始 stdout 也没有单独回执。压缩包包含四个成功节点的原始输出（client/API/UI/R13 共 42 项）和一个 R13 cleanup 断言摘要，共 43 项，未打包 pytest scratch。

本次 AFI 候选 HEAD `5be2e26c9cbb558187ed4ba7cce622989cb50c0e`；九个当前文件的 byte/hash pin 与本轮 source-binding 相符，见 `manifest.json`。R1 client/API/UI 在 AFI 源 HEAD `0c8aa93c1ca83363f13bd2128d40808b481e9e61` 下通过；R13 对当前 AFI HEAD `5be2e26c9cbb558187ed4ba7cce622989cb50c0e` 及固定 SDK F29 source export 验收。R13 的五仓库 HEAD 和来源 pin列于 manifest。Root 仍需只 stage/commit manifest 列出的九个 AFI 文件；不要求这轮更新 AFI progress，亦不在本轮提交 SDK 改动。

R7、R9、R10、R11、R12 的失败输出原目录保留，未复制进包；R8 仅为 preflight 拒绝且没有 output 目录。失败历史索引见 manifest。

证据限于合成场景。此节点不构成真实账户/provider 接受或 AFI03 正式完成。仍待闭合：FX/mark 的来源与时间、多币种估值、合约乘数、跨账户快照偏差，以及 missing/stale/unreconciled/coverage-incomplete 的完整估值矩阵。不得据此宣称 AFI03 完成。

文件 pin：`raw-evidence.zip` 1072384 bytes / SHA-256 `4dd5be533a372c9f72c8fcc70d478eb688d2fc78cc8c81d0e358a2bfce7e8bfa`；`manifest.json` 15590 bytes / SHA-256 `5b392eb9383e39b143f968fb177599872bf48d5f5e703fa39ffdcd400752553f`。
