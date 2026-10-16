# DIM07 hot-request shared-budget source round

Status: `GO_SOURCE_SUPPORT_ONLY`; one local synthetic integration node passed. This is not a formal DIM07 acceptance or a measured fairness result.

## Scope

This round adds only `tests/integration/test_gateway_quota_hot_wave.py`. It exercises five spawned worker processes sharing one local SQLite quota domain. Each worker submits 20 distinct strategy/run queries in batches of at most two concurrent calls, for 100 total requests and at most 10 simultaneous attempts. The domain has capacity eight; the node observed eight grants and 92 `DEADLINE_EXCEEDED` results. Every granted call must finish before its 750 ms request deadline. The 95th-percentile denial latency is calculated only across denials using nearest-rank `ceil(0.95 * N) - 1`; its separate bound is 750 ms of cleanup slack after the request deadline (1.5 seconds total). Expired waiter rows are pruned through the normal admission API.

The test does not assert how grants are distributed by process or strategy, does not model 100 simultaneous attempts, and does not prove cross-machine allocation, real provider behavior, account-session Owner composition, or formal DIM07 completion.

## Pins and validation

- SDK: `D:/bt_api_py`, branch `dev`, HEAD at author run `04e32632d91aaf1ba4c6140abf154b9e249285cd`.
- Gateway dependency: `bt_api/bt_api_gateway` commit `e37b3288f07bc57bce89bcfad6f71277eb69d3dd`; `src/bt_api_gateway/quota_admission.py` is 48,541 bytes, SHA-256 `844b1b955ec17125bd9bca42815421ce31d5272ba3b517b15c9fc2cfd53ad37d`.
- Final test: 7,952 bytes, SHA-256 `A058EDA3F3A6B14FDE9910FF01B808886669CE205C6EF8253609D1A9BA3A118A`.
- Author QA ran the focused node with Python 3.14 and explicit Base/Gateway source paths. The p95 calculation change passed as 1/1 in 11.16 seconds on the immediately preceding 7,884-byte test pin `74F67FCE7F86215FDE9910FF01B808886669CE205C6EF8253609D1A9BA3A118A`. The only subsequent edit corrected a comment to distinguish this node's 750 ms deadline plus 750 ms cleanup slack from the existing busy-SQLite test's 750 ms total elapsed cap; no runtime code changed afterward.
- Independent static review of the final pin returned `GO_SOURCE_SUPPORT_ONLY` and confirmed the metric definition and evidence limits. No Root QA or Host run was performed.

Earlier 10- and 20-concurrent candidate runs exceeded their then-current latency assertion. Their measured summaries were 0.988 seconds and 1.137 seconds, respectively, but no separate stdout/JUnit artifacts were retained; these are development history, not acceptance evidence. The final candidate bounds concurrency at 10 and makes no fairness claim.
