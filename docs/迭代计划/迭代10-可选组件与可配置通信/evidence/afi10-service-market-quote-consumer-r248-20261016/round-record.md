# R248：AFI10 service-v2 行情消费者部分切片

日期：2026-10-16  仓库：`D:\bt_api_py` / `dev`  AFI：`D:\source_code\ai-for-investor` / `dev`

R248 固定并保留一个仅含四个 AFI 路径的 partial candidate：`src/backend/app/services/quote_service.py`、新建的 `app/services/quote/service_market.py`、聚焦单测和 synthetic Host/UI 集成测。候选基于 AFI HEAD `ad9c8dcc7ba6bef1d2cbd915551fe95932e92ee`，只包含本行情消费者切片；当前 checkout 其他 dirty 路径未进入候选。精确源 blob、SHA-256、长度和父 HEAD blob OID 见 manifest/ZIP 内 `candidate-partial-index.json`。

作者此前本地执行报告 11/11 passed、0 failure/error/skip、pytest exit 0（3.65 秒）；Ruff 和 `git diff --check` 报告通过。证据分类为 `AUTHOR_LOCAL_SYNTHETIC_HOST_UI_ONLY`，不计 Root QA 或正式 AFI10 PASS。原始执行仅有 console 输出，没有可归档的 stdout/JUnit/result 文件；本包显式记录这一限制，保留代码快照与命令摘要，未伪造原始输出、未重跑该套件。Root 后续如需执行独立 QA，应绑定包内固定候选。

消费合同复用 `sdk.market.event` 的 `WireEnvelope`，`payload` 为原有 `key` / `data` 两项，OKX ticker `channel="tickers"`。当前集成测试由 synthetic provider 在 GatewayMarketHub owner thread 发布，验证真实本地 ServiceHost、SDK ServiceMarketClient 和 AFI quote HTTP/UI 投影。它没有验证生产 OKX WSS 队列进入 Hub。MIG01 的 R244 盘点确认 SDK 尚无 owner-thread bounded drain；AFI 没有跨线程 publish bridge。

只要用户/subject/symbol 没有精确配置 service-v2 route，现有 legacy 行情 source/workspace 行为仍保留。该部分切片不退役 AFI manager 的 `GatewayRuntime` 动态加载，也不删除旧 quote runtime/receiver；这些旧消费者继续阻止物理删除 `bt_api_base.gateway`。未进行真实 provider/account/Host 安装或正式案例验收。

本轮只准备候选/证据，不 stage、commit 或修改正式进度文档；Root 决定 AFI 与 SDK evidence 的精确提交范围。