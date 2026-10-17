# Iteration 10 DIM07 hot-wave focused tests

Repository/branch: `D:/bt_api_py`, existing `dev`; integration parent `f590be3bfe5f5fb4a010315e931f81ef300393d4`.

This test-only round adds shared CTP_QUERY backoff/cancel-capacity checks, actual persisted LOGIN/RECONNECT grant assertions, bounded result collection, cleanup diagnostics that preserve the original failure, and fixed-bucket metrics. The candidate 100-process workload retains its existing 10,603,200,512-byte memory preflight and two-GiB reserve check; that workload has not run.

Independent static review accepted the two-file R2 patch (43,067 bytes, SHA-256 `866d375a4ba1dbe8bf88fce427261f02fbe86e3ec1c2ac085d5c1460af941963`). The author ran only the three changed regressions after review corrections: 3 passed, 5 deselected, one existing pytest configuration warning, 0.74 seconds. Ruff passed. The earlier candidate had five focused passes, before the additional grant and cleanup assertions. One intermediate fake-queue assertion failure and earlier collection errors remain documented.

Raw stdout/JUnit files were not saved for these focused runs; the recorded console results are not presented as raw artifacts. The detailed record is `.codex-tmp/dim07-hot-wave-r2-review-record-20261017.md` (3,806 bytes, SHA-256 `c898a1382a5d089a7316f084c9480571660e3c9b3ddb3a23b0124d9428c4a9c0`). Its test SHA transcription was corrected after Root's pre-stage check stopped; source, patch and QA were unchanged. Accepted tests were not repeated for delivery.

This round contains only `tests/integration/test_gateway_quota_hot_wave.py`, its metrics helper, and this record. It establishes no Host, full LOAD01, 100-strategy or real-provider acceptance. Formal progress remains unchanged. The exact scoped commit is recorded in `.codex-tmp/dim07-hot-wave-root-postcommit-20261017.json` and Git history.
