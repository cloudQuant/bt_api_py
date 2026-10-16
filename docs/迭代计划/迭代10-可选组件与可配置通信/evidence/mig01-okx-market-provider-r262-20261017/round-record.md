# R262：MIG01 OKX ticker queue → GatewayMarketHub source slice

日期：2026-10-17
SDK：`D:\bt_api_py` / `dev`
源码包状态：Base/Gateway/OKX 子包已按精确 scope 提交；SDK 父仓 gitlink 与三件证据由当前父仓提交闭合。
证据目录：`docs/迭代计划/迭代10-可选组件与可配置通信/evidence/mig01-okx-market-provider-r262-20261017/`

## 范围

R262 接入 provider-neutral `MarketProviderEvent` 与可选 `MarketProviderPolling` 能力；对有 `poll` 的 provider，Hub 仅在 owner-thread `maintain()` 以有限预算读取队列、按 acquisition generation 拒绝陈旧事件，再复用已有 `sdk.market.event` / `market.event` 的 `{`key`, `data`}` 合同。没有 callable `poll` 的既有 manual provider 仍可由 owner-thread `GatewayMarketHub.publish()` 发事件；`maintain()` 不为其合成事件。OKX adapter 将既有 ticker queue 投影到该 Hub 路径。

精确源码范围为 9 paths：Base 2、Gateway 4、OKX 2、SDK integration test 1。三个子包只 stage 各自 manifest 中列明的 scope。AFI09 并发候选 `bt_api_gateway/session_owner.py` / `tests/test_session_owner.py` 不属于 R262；Gateway package status 中该新 owner resolver 文件保持排除。SDK 父仓还存在其他未提交工作，本轮不得扩大 stage scope。

## AUTHOR_QA 与复核

作者在 2026-10-16 执行 R5 五个定向 selector：5 passed、0 failed、0 skipped，pytest exit 0；存在一条 `PytestUnknownMarkWarning`（`pytest.mark.integration`），stderr 为空。输入来源 pins 12/12 匹配，执行前后均未改变；五个 package/root HEAD 执行前后相同。该结果分类为 `AUTHOR_QA`，不是 Root QA。

独立静态 reviewer `/root/resume_mig03_gap/mig03_integration_static_review` 返回 `GO_STATIC`，确认缺少 `poll` 的 manual-publish provider 兼容、无合成事件、有限轮询/轮转/stale-generation 语义和 OKX adapter 路径。Root 已接受这次历史 R5 结果；root HEAD 后续仅 evidence commits 演进，按 Root 指令不做 metadata rebind 或重跑。

AUTHOR_QA 输出以原始字节保存在 `raw-evidence.zip`；zip SHA-256 为 `9bba726a7d1c58de5bf534cc18459999611e576919b3f8fe5bd132cb81624e37`，大小 9644 bytes。原 R3/R4 尝试仍在其原始输出目录保留；本轮只引用最终 R5 五节点成功结果。

## 子包父提交连续性

Base R261 `4812cad42e67d2aad3ad5306a08f1aeef6bbf6f0` 与 Gateway R261 `6d26789d9a03ad486c07153af1590be855b090a2` 已提交。逐路径对照 R5 执行 HEAD 与 R261 后新 HEAD，Base 两个旧 blobs、Gateway 四个旧 blobs 均保持完全相同；无需重新生成源码，也不重跑 R5。OKX 当前 HEAD 仍是其 R5 执行 HEAD `848b7fb77a496fe4f3f88707bfd4332983e084b6`，两个候选为新增文件。

R262 子包提交已按 Base → Gateway → OKX 顺序完成：Base be51f280b70f52e5d86b37d3e3f9a148700deb9a（parent R261 4812cad42e67d2aad3ad5306a08f1aeef6bbf6f0，2 paths）、Gateway 147ef365706bebab383ec12c628be243b8e0b381（parent R261 6d26789d9a03ad486c07153af1590be855b090a2，4 paths）、OKX 7691ab439438e827da104de501aea380f70e4d9c（parent 848b7fb77a496fe4f3f88707bfd4332983e084b6，2 paths）。逐仓提交后核对 commit scope 与 manifest 相同、各 index empty；Gateway 并发 account_session_worker.py、account_position_snapshot.py、session_owner.py、test_session_owner.py 继续保留为未提交改动，不在此轮。

父仓 R261 commit 为 116e83ea4b44fad03dd4d7dd5abba50da64f09f2，其 R261 Base/Gateway links 和 R262 前 SDK index 均经核对。父仓交付 scope 是三个 R262 gitlinks、新增 SDK Host integration test 与本目录三件证据；R5 的 QA provenance 与源码输入 pins 保持原样，不重新执行 QA。父仓 commit identity 由 Git 提交本记录的 commit 给出，避免记录自身 commit SHA。

## 证据边界

证据等级：`SOURCE_FAKE_OKX_TICKER_TO_SERVICE_V2_HUB_ONLY`。该轮是合成队列到本地 Hub 的源码/测试证据，未启动 production ServiceHost 或 OKX websocket，没有账户、外部交易所或 live-provider 操作。生产 owner resolver/bootstrap wiring 仍是后续独立实现；本轮不宣称该 producer 已可部署，也不构成正式 MIG01 PASS。
