# R268 — AFI04 formal evidence packet (Root adjudication draft)

**Status:** `ROOT_ADJUDICATION_DRAFT`. This packet consolidates accepted source rounds and actual raw synthetic outputs for Root's case-by-case decision. It does not edit the official progress tracker or declare AFI04 PASS.

## Formal requirement

`正式迭代计划.md` row AFI04 requires: “估值/现金流/收益、敞口/回撤、VaR/ES、压力测试按版本和相同输入可复现；报告不授予交易权 | 数值基准与时间对齐/符号/单位反例；真实独立分析进程，任务限额/取消/超时；离线输入零账户登录”. The four calculation clauses are mapped separately below; lifecycle, authorization, and offline constraints are cross-cutting evidence.

## Four calculation clauses

| Formal clause | Source commits and implementation | Evidence and adjudication boundary |
| --- | --- | --- |
| Valuation, cash flows, returns; versioned and reproducible | AFI V1 adds immutable subject/portfolio/membership-version-bound offline datasets, typed facts, V1 task API, and merged Alembic head in `056d40f5df7702a1d6d8a14cf0238d3303ce2f6d`. Risk V1 dispatch/report digest is in `40850032f65d66c91b4b4299e28f27408df2add0`; the existing calculation is `spot_nav_modified_dietz_v1` with explicit two-cut valuation, external-flow coverage, flow weighting, and explicit identity/direct FX policy. | The R7 HTTP node uses the ordinary authenticated API route and independent SDK owner/Risk Host path with synthetic offline facts. It checks a successful Modified Dietz result (`0.090909090909090909`), report/input digest, versioned output, explicit unavailable/null result for incomplete flow coverage, cancellation, and orderly shutdown. R4 worker nodes independently exercise V1 calculation and fail-closed incomplete flow coverage in a spawned process. Evidence is local synthetic data, not broker/account valuations or provider-backed FX. |
| Exposure and drawdown | Risk numeric V1 module is committed at `6c81653e2a586f47c17cba5f898f23a247b1905d`; typed analytics task infrastructure is at `131175a4070c930f1dd5186ea707d6bc9bbb4310`. | Existing Root-focused numeric suite is 16/16 at `risk-portfolio-metrics-20261016/root-adjudication.md`. Its accepted cases include signed net/gross exposure and chronological drawdown examples, fixed units/cuts, and finite-input bounds. The exact source/test/QA pins and raw suite are in that pre-existing packet; R268 does not rerun it. |
| Historical-loss VaR and ES | Risk numeric V1 module `6c81653e2a586f47c17cba5f898f23a247b1905d`; V2 report/task integration `dc30c1c118cf587538150fc5016c8956e30c60a9`. | Same accepted 16-test Root numeric suite checks a fixed historical-loss quantile/rank and tail-average hand calculation, including sign and ordering edges. V2 task/report tests preserve typed versioned output and canonical input/report digests. This is a bounded calculation contract, not a claim of regulatory suitability or advice. |
| Stress testing | Risk numeric V1 module `6c81653e2a586f47c17cba5f898f23a247b1905d`, with V2 task integration `dc30c1c118cf587538150fc5016c8956e30c60a9`. | Same accepted numeric suite verifies complete scenario/position coverage, row-level and aggregate PnL conservation for multi-currency long/short rows, explicit dated FX, common UTC cut, and -100% price shock boundary. FX must remain positive; unsupported units and incomplete scenario coverage fail closed. The source package does not represent broker FX provenance or freshness. |

## Cross-cutting constraints

- **Reproducibility/version binding:** V1 report inputs bind canonical facts, full request, principal, and model profile; the worker reply validator checks the report shape and digest. The R4 worker success node validates the exact Modified Dietz number and trace digests. Existing V2 tests separately verify canonical serialization and input/request/report hashes. This supports deterministic versioned contracts; it does not claim a repeated-run byte-for-byte comparison beyond assertions in those nodes.
- **No transaction authority:** analytics calls use the fixed `analysis.input_upload`, `analysis.submit`, `analysis.query`, and `analysis.cancel` operations under the Risk-analysis service binding. The analytics task/report API adds no order-submit operation. The R236 record describes the accepted API/SDK owner scope; the analytics report itself is not an execution grant.
- **Independent process, quota, cancel, timeout:** Risk worker isolation and lifecycle were accepted in `ae9075ebd6f2bda2802edcb41a2dc66c859478fc` (Root unit and real SDK-client → Risk Host → spawned-worker nodes, per `afi04-worker-lifecycle-20261016/round-record.md`). V1 dispatch now enters the same bounded worker lifecycle in `40850032f65d66c91b4b4299e28f27408df2add0`; the R4 V1 nodes exercise spawn, successful report, unavailable report, and cleanup, while R7 exercises V1 HTTP cancellation and Host shutdown. The startup/execution-deadline failure injections are proven by the prior shared lifecycle nodes, not by a V1-specific timeout case.
- **Offline input / no broker login:** R7 creates an explicitly labeled, immutable offline dataset and submits it through authenticated application HTTP. Application JWT authentication is used; no broker account login, real account, provider, or external finance-data call is claimed.
- **Migrations:** R1 includes one upgrade preserving a pre-existing portfolio-composition row and one fresh-database upgrade to the sole merged head. This is synthetic SQLite/Alembic upgrade evidence, not a live production-database migration.

## Current source context and raw evidence

AFI V1 code/data/migration is commit `056d40f5df7702a1d6d8a14cf0238d3303ce2f6d`; AFI V2 HTTP/SDK/UI integration is `ad9c8dcc7ba6bef1d2cbd915551fe95932e92ee1`. Risk V1 worker is `40850032f65d66c91b4b4299e28f27408df2add0`; its independent incremental static review returned GO_STATIC on the exact service and test pins. Numeric Risk source is `6c81653e2a586f47c17cba5f898f23a247b1905d`; V2 is `dc30c1c118cf587538150fc5016c8956e30c60a9`; the shared bounded worker lifecycle is `ae9075ebd6f2bda2802edcb41a2dc66c859478fc`.

The raw evidence bundles in `raw/` contain only each run's `exit.txt`, `junit.xml`, `result.json`, `source-before.json`, `source-after.json`, `stdout.log`, and `stderr.log`. `command.json`, `spec.json`, runner code, `running.json`, and pytest temporary databases are excluded. Each bundle has member SHA-256 pins in `manifest.json`.

- `raw/afi-v1-http-r7-raw.zip`: the R7 test passed 1/1 in 10.936s; 2,858 source pins remained unchanged; stderr was empty. Root reviewed and accepted this narrow synthetic HTTP-to-independent-Risk-Host node. Its runtime source guard records AFI base `fe4c83c5…`, SDK `4f5ae610…`, Risk parent `503a0b…`, Base `be51f280…`, and ZMQ `a397fdb…`, with the exact V1 candidate overlay pinned in the archived source manifests.
- `raw/afi-v1-migration-r1-raw.zip`: R1 passed 2/2 in 14.514s; 1,916 source pins remained unchanged; stderr was empty. The test source guard records AFI base `fe4c83c5…` before the exact candidate was committed as `056d40f5…`.
- `raw/risk-v1-worker-r4-raw.zip`: R4 passed 2/2; JUnit records 1.407s and stdout reports 1.95s; ten source pins remained unchanged; stderr was empty. The run used Risk base `503a0b…` plus the exact candidate later committed as `40850032…`.

At packet preparation, AFI tip is `50f6a6826ce30fb108aefead33af1cc2633f266f`, SDK tip `1ad3a0b5623d56d5182dd5f0835ab0c37b9e17e8`, and Risk tip `6d51dc8ec28364cb79688caeb0073e0d3615beff`. These are context only. In particular, Risk `6d51dc8` is a later FX-policy descendant and was not imported by the R4/R7 source snapshots; no whole-current-HEAD QA is claimed. Raw run manifests are authoritative for their exact runtime inputs.

Existing accepted V2 HTTP, numeric metrics, and worker-lifecycle records remain at:

- `evidence/afi04-analytics-http-risk-r236-20261016/round-record.md` (raw archive SHA-256 `ceb22c1ea2005363df2a9b64880216f380183d67d35c3a15ec735ccc865de0c6`)
- `evidence/afi04-portfolio-analytics-v2-20261016/round-record.md`
- `evidence/afi04-worker-lifecycle-20261016/round-record.md`
- `evidence/risk-portfolio-metrics-20261016/root-adjudication.md`

## Decision boundary

This packet is prepared for Root's formal AFI04 adjudication. It does not change `开发与验收进度.md`, the official matrix, repository indices, or source. No real account, provider-backed facts, trusted external FX/freshness, or trading authorization is claimed. The explicitly deferred two-machine acceptance is not used as an AFI04 blocker.