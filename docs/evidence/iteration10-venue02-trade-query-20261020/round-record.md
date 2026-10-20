# VENUE02 QueryTrade completeness and shared deadline

Date: 2026-10-20. Scope: source-fake integration support for the real CTP `QueryTrade` callback window through Store and the Risk evidence/journal path. This round does not establish a formal VENUE02 pass.

The reader now requires a completed `ReqQryTrade` window with a terminal callback before treating an empty result as complete. It binds the window to the Store callback sequence and the expected session, account, trading day, request, and order identity. Callback absence, an incomplete query, or a mismatched target trade fails closed. Risk evidence records trade-history completeness; a positive fill with unavailable commission remains incomplete/review-only. No fee is inferred as zero.

One absolute monotonic deadline is created for a reader issue and propagated through preflight, QueryOrder, QueryTrade, query-lane locking, spacing waits, callback waits, and final evidence checks. Expiry fails closed and late query results are rejected. A synchronous vendor `ReqQryOrder` or `ReqQryTrade` call cannot be interrupted while inside the vendor API; if it returns after the deadline, the result is rejected then. This is not a hard wall-clock return-time guarantee.

The ten changed source/test paths and checkout pins are:

| Checkout at round start | Path | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `bt_api_py` (`ff3502add769c9a866776c07bd8bc94fea759e8d`) | `bt_api_py/ctp_order_target_query.py` | 40,880 | `24ec5f105764de484599e2e648cbcd4f2b1c0dc82c2151dccdfe78476108f08f` |
| `bt_api_py` | `bt_api_py/ctp_zero_fill_tracked_order.py` | 68,568 | `977925138c1b858c0520c51f78d6747d72074f950a73d2e551fd1dd9338702eb` |
| `bt_api_py` | `bt_api_py/ctp_risk_dispatch_bridge.py` | 63,758 | `9083dd4f95bf22189b10d7fce30bd29fdf89aab132061198e38f66dfa53b3995` |
| `bt_api_py` | `bt_api_py/service_risk_order_journal.py` | 414,742 | `2751924825ebe2d62144df4b12a83b9ba3372cc2672787ec8293ae0e8e9dcbf3` |
| `bt_api_py` | `tests/unit/test_ctp_zero_fill_tracked_order.py` | 18,166 | `04cda5f282513f7372101a98b37a8749f8f2e866afa9e63ff3c68840b746030a` |
| `bt_api_py` | `tests/unit/test_ctp_runtime_zero_fill_risk.py` | 25,969 | `8e0a11c1ba3fe2255f692ba0b504a47540893680a426ba68aa59a1e265a0fb28` |
| `bt_api_execution` (`6134fb40e441980a05960e65200a91996d1ffb74`) | `src/bt_api_execution/store.py` | 1,136,594 | `d8763af78e0a391b091139fa3195de1f1414bd01dab48816eee7189961ae1513` |
| `bt_api_execution` | `tests/test_ctp_order_query_cancel_fence.py` | 41,365 | `8b935ba4ecf36e1b32d1d494e8462d7bda228fb7f259f9582777987d6859457d` |
| `bt_api_ctp` (`dd906d04ecaa64ed8f7f74c4f8d8166a5d0859f8`) | `src/bt_api_ctp/ctp/client.py` | 454,274 | `00fba2c4d3152586e3da1000700a418b0797fc354fa8dbea033c8a209d91b125` |
| `bt_api_ctp` | `tests/test_instrument_queries.py` | 22,588 | `247648720709f87a01fb71ba8cfe2cc9e662c636e71f8bd642bdcd4736da9edd` |

The parent six-path binary diff is 67,493 bytes / `79037a7b12edea6d89a64983a4dadaca6bd1d40b3429b9b1d7986c2bd6371368`. The execution-package two-path diff is 12,547 bytes / `0ef35fc441f33d46b9eb68b6f124a90d391c68be85868d8c807fdd140d20ee93`. The CTP-package two-path diff is 16,786 bytes / `f82de30ec1973c95b8d695a86204d59d0500308fbbe03896fda83659576947f7`; `client.py` also contains the separately coordinated market-data callback additions.

Verification was source-fake only:

- The focused CTP package run passed 55 tests. It reported the existing pytest configuration warning and the expected warning from the native-import guard blocking CTP native import; the guard result recorded `ctp_native_imported=false`.
- The Store author focus passed 1 test.
- Root's corrected guarded run passed 10 of 11 tests. The remaining readback-fence test expired its arbitrary 0.25-second issue budget before reaching its readback assertion. That test now uses 2.0 seconds; Root reran only that node and it passed 1/1. Thus all 11 focused cases were covered across two Root invocations, not one all-pass 11-test run. The corrected run retained the existing pytest and native-guard warnings and verified local source origins.
- Root QA readbacks are `.codex-tmp/venue-trade-source-20261020/root-focused-result.json` and `.codex-tmp/venue-trade-source-20261020/root-readback-result.json`.
- Three earlier Root runner setup attempts stopped before test execution: the first omitted the `tests/unit` fixture path, the next lacked another fixture directory, and the next selected the installed CTP package instead of the repository source. These were runner setup failures, with zero tests executed; the corrected runner used the local source and fixture paths.
- `py_compile` and scoped `git diff --check` passed for all three checkouts.

No account session, provider request, or native CTP operation was started. There is no formal VENUE02 pass. The source changes remain uncommitted pending Root's coordinated CTP market-data integration and parent delivery.

## Scoped package commits

Accepted package source was committed on existing dev: execution `e7fe7e26ea0c703874e32e985f1b8a510fc01f92`; CTP `9808aca2c6778eb02ef9306990419b98e73de68a`. The CTP commit additionally includes the backward-compatible optional market-subscription completion callback and its one passed focused regression (`tests/test_ctp_md_subscribe_batched.py`). CTP query/callback test checkouts use CRLF while configured Git stores LF; normalized content is identical. Runtime client bytes are identical. The parent commit containing this record updates these two gitlinks and the six accepted parent files. Other market-recovery edits remain outside this round.
