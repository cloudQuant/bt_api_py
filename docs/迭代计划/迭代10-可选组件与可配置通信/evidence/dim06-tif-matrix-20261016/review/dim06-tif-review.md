# DIM06 TIF matrix increment — independent adversarial review

- Review artifact: `it10dim06-tif-review-20261016-r1`
- Reviewer evidence dir: `D:\temp\it10dim06-tif-review-20261016-r1\` (junit.xml, stdout.log, stderr.log, exit.txt, closure-probe.txt, direct-surface-probe.txt, command.txt)
- Reviewed deliverable: `tests/bt_api_contract/test_venue_order_type_tif_matrix.py` (untracked; sha256 `6f39ed54248021b5b249b93eb34a88875dd4d2690427ea2dded28381741248e5` — matches report, spec pin, and on-disk bytes)
- Reviewer timebox: 14:10–14:15; no tracked-file edits, no git add/commit/stage, no process kills.

## VERDICT: GO-with-disclosures

Producer claims verified end-to-end: all asserted rejections/acceptances are produced by production gates (file:line cited below); NOT-asserted boundaries are honestly documented and independently confirmed absent/ambiguous in production; closure is R3-free by two methods; evidence hashes and 263 pins verify byte-for-byte after the HEAD move; my independent re-run reproduced 43 passed / exit 0. No falsified claims. Disclosures: synthetic-harness limits, LIGHT_IMPORT env deviation, a tautological container assertion (nit), and a smoke-#1 failure count that is no longer reproducible (harmless). Safe to commit as a dev increment for local gates.

## Check 1 — every claimed rejection against the production path

All nodes drive real classes (`FeedAdapter` -> `_venue_mappers.*` -> real venue feed `make_order`); only transport/access gates are fakes/stubs.

| Claim | Production gate (file:line) | Test node(s) | Verified |
|---|---|---|---|
| CTP market -> `CapabilityNotSupportedError` at mapper | `bt_api_py/_venue_mappers/ctp.py:20-23` (`operation="make_order"`, `detail="CTP supports limit orders only"`, `definite_reject=True`) | `test_ctp_rejects_market_orders_at_the_mapper_before_dispatch` (asserts all three fields + 0 submits); `test_cross_venue_market_order_type_matrix` | YES + direct probe reproduced exact fields (`direct-surface-probe.txt`) |
| CTP TIF vocab GFD/DAY only -> `ValueError` before submit | `bt_api_ctp/feeds/live_ctp_feed.py:1137-1143` (DAY normalized at :1138-1139; msg `CTP time_in_force ... is unsupported` at :1142) | `test_ctp_rejects_non_gfd_tif_before_any_submit[gtc/ioc/fok/post-only]` (msg substring + `execute_calls==0`); cross-matrix CTP False cells | YES |
| CTP feed kind guard rejects non-limit before TIF handling and before request-id allocation | `live_ctp_feed.py:1104-1107` (kind check precedes `_next_request_id()` at :1146 and submit at :1156) | `test_ctp_feed_kind_guard_also_rejects_market_orders[gfd/gtc]` (msg + `execute_calls==0` + `next_request_id_calls==0`) | YES |
| CTP limit GFD/DAY accepted: `TimeCondition="3"`, OrderRef preserved, status True | `live_ctp_feed.py:1135` (TimeCondition hardcoded "3"), `:1145` (OrderRef=client_order_id), `_make_request_data` default `status=True` (:999-1010); 12-digit OrderRef validated at :55-56/:1087 | `test_ctp_accepts_gfd_and_day_limit_orders[gfd/day-normalized]` | YES; nit: `result.data[0].get_order_time_in_force()=="GFD"` is tautological — `CtpOrderData.get_order_time_in_force` hardcodes `"GFD"` (`bt_api_ctp/containers/ctp/ctp_order.py:152-153`); the load-bearing wire assert is `field.TimeCondition=="3"` |
| OKX TIF vocab GTC/IOC/FOK (+post_only) -> `InvalidOrderError` otherwise | `bt_api_okx/.../trade_mixin.py:66-78` (msg `Unsupported OKX time_in_force` at :77) | `test_okx_rejects_tif_outside_its_vocabulary_before_any_request[day/gfd/unknown-token]` (0 requests); cross-matrix OKX False cells | YES + direct probe (`OKX DAY ... Unsupported OKX time_in_force`, 0 requests) |
| OKX post_only requires GTC | `trade_mixin.py:67-72` (msg `post_only is incompatible with IOC/FOK` at :71); render at :79-80; px kept for non-market at :97 | `test_okx_rejects_post_only_with_non_gtc_tif_before_any_request` (0 requests); `test_okx_post_only_limit_renders_ord_type_post_only_with_price` | YES |
| OKX market accepted, no px | `trade_mixin.py:97` (px only if `ord_type != "market"`) | `test_okx_market_order_renders_without_price`; cross-market node | YES |
| Binance limit TIF passthrough GTC/IOC/FOK | `bt_api_binance/feeds/rest_trade.py:38` (kwargs, no validation), `:53` | `test_binance_passes_supported_tif_through_exactly[gtc/ioc/fok]` | YES (accept side only; no fake rejection) |
| Binance market drops timeInForce+price | `rest_trade.py:63-65` | `test_binance_market_order_drops_tif_and_price` | YES |
| Registry: 5 venues, CTP___ prefix fallback, BYBIT->None | `bt_api_py/_venue_mappers/__init__.py:15-29` | `test_direct_path_venue_registry_covers_matrix_venues` | YES |
| FeedAdapter routes mapper -> feed.make_order with `time_in_force` kwarg | `bt_api_py/_feed_adapter.py:27-56` | exercised by all adapter-driven nodes | YES |

NOT-asserted items — honesty and independent absence check:

- Binance "no local TIF gate": documented in module docstring (`:38-39`) and matrix comment (`:490-492`); Binance cells omitted from `TIF_TOKEN_MATRIX` for GFD/DAY/post_only. Independently confirmed: `rest_trade.py:38` has no validation — ANY token passes for limit. Non-assertion is warranted; faking a rejection would be fabrication.
- OKX market+post_only boundary: documented (`:40-41`). Independently confirmed by direct probe: no local raise; renders `ordType=post_only` with no `px` (`direct-surface-probe.txt`). Non-assertion warranted (production does not reject).
- CTP default-TIF surface (`:42-43`): documented; not asserted beyond the GTC direct case — acceptable scope cut.

## Check 2 — closure / hygiene

- Module imports (lines 58-77): stdlib, `bt_api_base.exceptions`, `bt_api_py._contracts.*`, `bt_api_py._feed_adapter`, `bt_api_py._venue_mappers`, three venue adapter modules, `NormalizeMixin`/`RestTradeMixin`/`TradeMixin`/`CtpRequestData`. No import of `bt_api_py.bt_api`, `_direct_backend`, `_gateway_wire`.
- Independent standalone import probe (not pytest; `closure-probe.txt`): `bt_api_py.bt_api`/`_direct_backend`/`_gateway_wire` all absent from `sys.modules`; 257 site files under `D:\bt_api_py` in closure; 0 R3 files. In-suite guard nodes (`:263-281`) passed in the formal junit and in my re-run.
- Pin set excludes R3: computed check of spec.source_sha256 -> R3 paths among 263 pins = `[]` (incl. `bt_api_py/bt_api.py`, `_direct_backend.py`, `_gateway_wire.py`, both R3 test files).
- No `monkeypatch`, no skip/xfail (grep: only `@pytest.mark.parametrize`). Harness stubs are subclass overrides of `_ensure_trading_ready` (`live_ctp_feed.py:943-949`) and `_ensure_execution_permitted` (`:848-891`) — pure execution-access/connection gates, both called BEFORE the kind/TIF/TimeCondition/OrderRef code that is asserted; plus counting fakes for transport (`submit_order_insert`, `request`). The logic under test (`:1104-1145`, `trade_mixin.py:66-97`, `rest_trade.py:38-65`) runs as production code.
- Disclosed: `tests/conftest.py:17-23` autouse fixture monkeypatches `bt_api_py._execution_session._ledger_registry_root` (tmp ledger isolation). This is global test infra unrelated to order-type/TIF gates — the exercised order paths never read the ledger root. Not a conflict; noted for completeness.
- Env deviation `BT_API_PY_LIGHT_IMPORT=1` is a genuine package contract (`bt_api_py/__init__.py:195-203` skips eager `from bt_api_py.bt_api import BtApi`; lazy `__getattr__` at `:231-235`). Repo `bt_api_ctp\src` on PYTHONPATH because site-packages copy is stale — documented in spec `author_evidence`.

## Check 3 — evidence integrity

- `junit.xml`: `tests="43" failures="0" errors="0" skipped="0"` (sha `11e0e78a...`); `exit.txt` = `0\n` (sha `13bf7b30...`); `stdout.log` sha `9ffd6e58...` == `result.json.stdout_sha256` (content `43 passed, 1 warning in 17.28s`); `stderr.log` 0 bytes sha `e3b0c44...`; `spec.json` sha `2fff58f4...` — evidence copy byte-identical to `.codex-tmp/it10dim06-tif-20261016-r1-qa/focused-qa.json`; `result.json` sha `520d2a76...`. All match the producer report.
- 263 pins: `source-before.json == source-after.json == spec.source_sha256` (identical maps); I recomputed all 263 SHA-256 from disk AFTER the HEAD move -> **0 mismatches**. The pinned set is byte-unchanged across e137193d -> ee3b05c2.
- Heads: root now `ee3b05c2ed81ea27598dab1c60bb6f6d3339324d` (MIG01-R3 committed after the run window); submodule HEADs base `b4aabd1e`, ctp `a497bab6`, okx `8c0019db`, binance `b438a277` — all match pins. Producer-pinned root `e137193d` is corroborated by `result.json` (heads_before==heads_after, `sources_unchanged=true`); the artifact is valid for its pinned source set. HEAD move documented-acceptable.
- Test file sha `6f39ed54...` matches pin and disk; unchanged since the formal run.

## Check 4 — scope

- Producer artifact: exactly ONE untracked new file, `?? tests/bt_api_contract/test_venue_order_type_tif_matrix.py`. No tracked file modified by the producer (also implied by 263/263 pins intact).
- Other dirt (ignored, other workstreams): `M tests/integration/test_arb02_group_budget_service.py`, `M tests/integration/test_arb03_dim05_group_partial_fill_service.py`, `M tests/unit/test_load01_hundred_strategy_30min.py`, submodule dirt `bt_api/bt_api_monitor`, `bt_api/bt_api_risk`, `?? .codex-tmp/`, `?? %SystemDrive%/` (Oct 15), `?? 0` (Oct 15).
- Additional untracked items appeared AFTER the run (mtime 14:10, other workstream): `bt_api_py/service_migration_map.py`, `tests/unit/test_service_migration_map.py`, `docs/.../evidence/arb02-crashprefirstleg-20261016/`, `docs/.../evidence/arb03-r3-fill-scenarios-20261016/`. Not in the pin set; do not attribute to this increment.
- R3 fence files are no longer dirty (committed in `ee3b05c2`); `git status --short bt_api_py/ tests/bt_api_contract/` shows only `?? bt_api_py/service_migration_map.py` (other WS) and `?? tests/bt_api_contract/test_venue_order_type_tif_matrix.py`.

## Check 5 — independent re-run

- ps before: resident 128016/132224 (Oct 13, unrelated); SCN02 drill 13961 (13:44); other short-run anaconda pythons 16935, 142928, 118748, 130092. Nothing killed.
- Exact command (recorded in `command.txt`):
  `unset PYTHONPATH PYTHONHOME PYTEST_ADDOPTS PYTEST_CURRENT_TEST PYTEST_PLUGINS PYTHONDONTWRITEBYTECODE PYTHONNOUSERSITE; export PYTHONPATH='D:\bt_api_py;D:\bt_api_py\bt_api\bt_api_base\src;D:\bt_api_py\bt_api\bt_api_gateway\src;D:\bt_api_py\bt_api\bt_api_transport_zmq\src;D:\bt_api_py\bt_api\bt_api_ctp\src' PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 BT_API_PY_LIGHT_IMPORT=1; C:/anaconda3/python.exe -m pytest tests/bt_api_contract/test_venue_order_type_tif_matrix.py -q --no-header -p no:cacheprovider --junitxml=...\junit.xml --basetemp=...\pytest-tmp`
- Result: **exit 0; 43 passed, 1 warning in 17.40s; junit tests=43 failures=0 errors=0 skipped=0; stderr 0 bytes.** Producer's formal result reproduced exactly. Overlap rationale: ~17s in-process synthetic suite, finished ~14:12, well before the 15:00 gate.

## Falsified claims

None. Near-misses (disclosures, not falsifications):
1. Smoke #1 "35 passed / 8 failed" is not independently reproducible (artifact not preserved); smoke #2 and the formal run are reproducible. Harmless historical claim.
2. `get_order_time_in_force()=="GFD"` container assertion is tautological (hardcoded getter); the wire-level `TimeCondition=="3"` assertion carries the DAY->GFD proof. Cosmetic nit.
3. Producer report's stated fix (`_BinanceHarness` missing `NormalizeMixin`) is consistent with the pinned file (`:175`).

## Production gates NOT found (falsification signal check)

None missing for any asserted claim. The only genuinely absent gate is the Binance local TIF gate (`rest_trade.py:38`) — and that absence is exactly what the file documents as NOT asserted. OKX market+post_only local gate likewise absent and correctly documented.

## Residual risks / disclosures for the commit

- Synthetic fakes only: counting `_CtpTrader` (`submit_order_insert` returns 0), fake `request` for OKX/Binance; feed/mapper classes real. Provider-side behavior (real OKX/Binance/CTP servers, e.g. real post_only-on-market handling) not verified — deferred to VENUE02 (plan clause 正式迭代计划.md:778). No real account.
- Env deviations mean the default eager-import path is not covered by this run; closure exclusion of R3 is by construction of `BT_API_PY_LIGHT_IMPORT=1`. If later merges change the venue adapters' closure, pins must be rebuilt and the artifact re-run.
- Formal artifact is valid against its pinned source set (byte-verified), which nobody guarantees equals tree `ee3b05c2` semantics for the fenced (unpinned) files — those remain outside the pin set by design.
- DIM06 case stays 部分: sub-item 6 becomes covered for LOCAL gates only; margin-pool attribution + the 3 execution_session nodes remain.
- Safe to commit as a dev increment: one untracked test file, no production edits, closure R3-free, 43/43 green under fixed-source pins.
