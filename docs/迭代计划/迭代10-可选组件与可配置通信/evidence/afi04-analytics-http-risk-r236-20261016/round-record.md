# R236：AFI04 HTTP → SDK owner → Risk Host analytics source delivery

本轮交付 AFI analytics normal HTTP/task integration 的源文件与作者本机合成证据。作者固定源运行 `test_authenticated_http_analytics_uses_sdk_owner_and_independent_risk_host`，1 collected / 1 passed，Risk Host 正常 ready、stop、closed；Root 已审阅并接受这一个窄 integration node，因此本轮不重复 Root QA。

证据层级是 `AUTHOR_LOCAL_SYNTHETIC_AFI_HTTP_TO_INDEPENDENT_RISK_HOST_TEST_ONLY`。输入为合成的离线 typed facts；该结果不证明真实账户、provider/外部行情、AFI04 formal PASS、可信 FX 来源、新鲜度或完成 V1 估值/现金流收益链。`PortfolioAnalyticsTaskPanel` 与 composable 源已随候选交付，作者随后在基于 AFI 接受 HEAD 的独立前端候选中完成专属 UI focused acceptance：4/4 Vitest 通过，针对 API/composable/panel 的 scoped vue-tsc 通过。测试只 mock 最底层网络 transport，保留真实 panel、composable 和 API wrapper；覆盖精确创建/查询/取消路由、pending 到 succeeded 报告渲染、unavailable null/reason、imported 取消和 live 表单抑制。该结果仍是作者本机 UI evidence，不是 Root QA 或真实 server/browser E2E。

候选包含七个 service-read/API shared 文件的 analytics-only hunks、十个 analytics 新/更新文件、以及 `PortfolioPage.vue` 的单独 analytics mount hunk，共 18 个源路径。完整 `PortfolioPage.vue` 含其他 AFI03 WIP，未打包或作为 whole-file delivery。已接受 `main.py` 的 `api_router` 入口 → `portfolio_router` → analytics task router 注册链可静态追溯；本轮没有改 `main.py`，也没有纳入 Paper binding hunks。当前基线 `00cf38fd1dbcd3bd860972bda7f2a4be6f83f138` 的 partial candidate 通过两份 analytics shared/Page patch 应用检查，并只 overlay 本轮十个源文件；该构建未运行测试。UI 验收候选单独以 `ac52830` 前端 archive 为底。本次归档准备没有重跑后端 QA/Host，也未 stage/commit；上文记录的作者 R9 测试确实启动并正常关闭了本机独立 Risk Host。

原始作者报告、JUnit、stdout/stderr及精确源码快照/补丁在 `raw-evidence.zip`。没有打包含环境配置的 `command.json`、pytest 临时数据库、运行凭据或 host 配置。精确依赖、源 pin、证据文件成员及 zip 哈希见 `manifest.json`。

下一步由 Root 对这 18 个精确源路径做 scoped commit；本轮不增加 formal case count。随后进入 V1 估值/现金流/Modified Dietz 接线，缺失的可信历史 cut/外部现金流/dated FX 继续报告 unavailable，不补造零值。

外层包材：
raw-evidence.zip 87,177 bytes / SHA-256 ceb22c1ea2005363df2a9b64880216f380183d67d35c3a15ec735ccc865de0c6；manifest.json 26,355 bytes / SHA-256 36f203385ba4debddaef4b070d34631f2e28aa9be6bbd11bdf39df5af75c0d5f。
