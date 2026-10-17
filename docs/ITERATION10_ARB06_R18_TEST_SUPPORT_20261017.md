# Iteration 10 ARB06 R18 test support

This SDK test-support bundle preserves the exact support files used by the R18 synthetic Cerebro roll-exit run. It is kept under `test_support/arb06_r18_sdk_support/`, outside pytest's test-discovery tree, so it does not replace current SDK tests or their fixtures. A Backtrader test runner can bind `BT_API_PY_TESTS_ROOT` to that directory; the existing loader reads the `integration/` and `unit/` paths and checks their SHA-256 values.

The four files and pins are:

| Path | Bytes | SHA-256 | R18 role |
| --- | ---: | --- | --- |
| `integration/_dim05_roll_exit_risk_host_fixture_diagnostic.py` | 38,220 | `ec2eddf64ce3f17db39e24e4ab2f8cd1d8dadf8cf624d6fcab087ce269a20a94` | Exact executed diagnostic Risk Host fixture |
| `integration/test_arb03_dim05_group_partial_fill_service.py` | 101,321 | `d9e516f3b8cbc3cdde4f7e477c3526109603ff0c3a1b01f58be2df9086787887` | Exact pinned DIM05 source/seed helper |
| `integration/test_service_execution_group_local_process.py` | 61,209 | `b9f60ce8cf29dcdd798672f4ad501abe6dc2346c7ffedf327376c83d90cd574b` | Exact pinned SDK group-host loader fixture |
| `unit/_service_group_provider_fixtures.py` | 9,484 | `9bcf021649104243a2b16a93ef159ceca8cacce6ffad9c22afd94eed4ee3b1e2` | Exact pinned synthetic provider fixture |

The diagnostic fixture comes from the R15 loader-pinned SDK test-support export. The other three files come from the R9 DIM05 SDK test-support export. The tracked SDK files at the R18 SDK HEAD were retained unchanged; two current files have different bytes from the historical support copies, so the bundle stores them side by side instead of substituting current files and changing the test's source contract. The four payloads are covered by path-specific `-text` rules so checkout preserves the exact bytes the R18 loader pins.

## Evidence boundary

Root's R18 run used `D:\temp\arb06-group-roll-exit-cerebro-abe0-4f3-fixed-r18-query-diagnostic-20261017\host-spec-r18.json` (183,356 bytes, SHA-256 `6e9dd1724df7571ac097b21221b647fd1d35a62461e02e9dfeb45232537323a0`) and passed one local synthetic Cerebro integration test. The executed selector was `tests/integration/test_arb06_group_roll_exit_cerebro_start_diagnostic.py::test_arb06_two_strategy_cerebro_roll_exit_preserves_peer_account_allocation`. The run observed one Risk CLOSE provider call, zero remaining source-strategy closable exposure, and the peer strategy's `0.75` allocation preserved. Its result was 768 bytes / `e0f2a577a7bee049b777cf28ada6e1a845edfd9e1766a94d48fb42da382d026a`; stdout was 1,770 bytes / `c5d88e8d4285bb4f93e3647594d21b4e332aaefa374a81298c0305546582f954`. The 605-byte cleanup receipt (`652ee57dac0f016bc586ba528dd9dc12b2f8f4bcab1a6ba6e645233479f948e9`) confirmed all hosts closed and joined, with no cleanup errors. The run was evidence for a local synthetic integration only; it did not close the aggregate group, whose durable state remained `PAIRED` at revision 18. Formal ARB06 remains **HOLD**. The intermittent R17 initial-query `UNKNOWN` remains unexplained. No test was rerun to create this bundle.

This bundle is test support, not a new test result. The corresponding Backtrader test source is a separate delivery path; its exact executed diagnostic copy must be preserved there rather than represented by the distinct canonical candidate. No test was rerun to create this bundle.
