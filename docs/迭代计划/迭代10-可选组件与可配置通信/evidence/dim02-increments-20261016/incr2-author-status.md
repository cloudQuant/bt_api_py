# DIM02 seam test-authoring status (iteration-10, 2026-10-16)

## Mission
Author NEW file `D:/bt_api_py/tests/unit/test_service_market_dim02_seam.py` covering DIM02
sub-items (2) 回补切实时 and (3) bar 修正 on the SDK side, with a REAL spawned ServiceHost +
synthetic deterministic provider (history pagination AND live publish) + real spawned strategy
client. Verify with real runs. Never modify production code or existing tests; never git
add/commit/stash/etc. Budget 150 tool calls.

## Constraints (verbatim-critical)
- Only create/modify: the new test file, this status file, evidence under
  `D:/temp/it10dim02seam-20261016-r1/` (must not pre-exist; did not exist at start).
- Evidence per run: cmd.txt / stdout.log / stderr.log / exit.txt as SEPARATE files, never overwrite.
- Invocation env (from `D:/temp/it10dim02-20261016-r1/run1p-probe/cmd.txt`):
  cd /d/bt_api_py; env `PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`
  `PYTHONPATH="D:/bt_api_py;D:/bt_api_py/bt_api/bt_api_base/src;D:/bt_api_py/bt_api/bt_api_execution/src;D:/bt_api_py/bt_api/bt_api_risk/src;D:/bt_api_py/bt_api/bt_api_transport_zmq/src"`
  `C:/anaconda3/python.exe -m pytest <file> --noconftest --import-mode=importlib -p no:cacheprovider --basetemp=<evidence>/runN/basetemp -q`
  (bt_api_gateway resolves from the anaconda env paths, verified.)
- SERIALIZATION: before each pytest run execute `ps -W | grep -i python | grep -v grep`; if another
  python runner is active, wait in <=120 s chunks up to ~15 min; record waits. One pytest at a time.
- Do NOT touch `tests/unit/test_service_market_dim02_integration.py` (other agent) or any existing file.
- Do NOT re-run delivery nodes (GW01 10-client, TRN02, 07B, round-88, package-level history/hub).
- No Backtrader changes.

## Recon findings (from dim02-recon.md §1(2)(3), §3 "(2)+(3)", §5.2, §6.1 + code read)
- Coordinator decision: bar correction policy = FAIL-CLOSED, documented; no silent replacement,
  no partial projection, no double-count; conflicting overlap must fail at the consumer boundary
  (07B BT proxy already proven; not re-run).
- SDK surface: `bt_api_py/service_market.py` `ServiceMarketClient.history(request)` -> exact
  bounded page passthrough (no cache; validates request echo/epoch/binding). `_HISTORY_OPERATION
  = "market.history"` must be in binding operations (grant + requested_operations + hub
  `history_provider`).
- Hub (`bt_api_gateway/market_hub.py`): `GatewayMarketHub(..., history_provider=, history_timeout_seconds=)`;
  `fetch_history` -> provider.fetch(request, timeout) once, no retry/cache, revalidates page.
  `publish(key, data, observed_at_utc, generation=)` treats data as opaque; puts observed_at into
  header.sent_at_utc (source-time passthrough); no dedup/merge/conflict logic for payloads.
- SDK client consumes live via SUB + head/read_after replay; forwards each event verbatim
  (validate key/epoch/sequence only). Port operations include market.history
  (`PORT_OPERATIONS["service.market_source"]`), gated by hub.history_enabled; component builtin
  declares ALL_MARKET_OPERATIONS and intersects with adapter's supported_operations.
- Fixture pattern to mirror: `tests/unit/test_service_market_integration.py` (GW01) —
  `_CountedSyntheticProvider`, `_service_host_process`, `_strategy_client_process`,
  `_free_endpoints`, `_start_process`, `_bounded_reap_spawned_process`.
  Round-07A host wiring reference: `tests/unit/test_service_history_host.py`.

## Deliverable node plan (4 test functions, deterministic, no network venues)
1. `test_history_to_live_seam_is_gapless_and_passes_through_source_times`:
   spawn host+client (grant 6 ops incl market.history); silent bootstrap assert; 2 warmup
   markers consumed (SUB-join) and discarded; paginated history [T0,T4) limit 2 -> (B0,B1,
   complete=False,next=T2) then [T2,T4) limit 2 -> (B2, complete=True); live cursor unchanged
   across reads; publish live B3 [T3,T4) with observed_at_utc=T4 -> delivered seq 3, epoch ==
   bootstrap epoch, key/identity exact, sent_at == T4 exact (source time, not wall clock);
   seam: last history close T3 == live open T3, no overlap, complete page2 exhausted at seam.
2. `test_silent_key_and_beyond_head_read_do_not_fabricate` (store empty): bootstrap snapshot
   None + seq 0; idle-poll returns no events (no synthetic bars/freshness) while
   active_upstreams==1; history read of empty interval -> explicit empty complete page
   (bars==[], complete=True, observed_at == provider value); release -> stops==1.
3. `test_corrected_history_reread_returns_provider_supplied_values`: read [T0,T3) -> (B0,B1,B2);
   provider corrects B1 (same open_time, different OHLCV); same request second read -> corrected
   B1 returned (no SDK cache), page1 unchanged, live cursor unchanged, provider fetch called
   twice with equal request + exact timeout.
4. `test_conflicting_live_overlap_fails_closed_in_consumer_projection`: history (B0,B1,B2)
   delivered; live events: exact duplicate of B2 then B1 revision (same open_time T1, different
   OHLCV) -> SDK forwards BOTH verbatim in order (documented boundary: SDK does not
   dedup/reject); test-local `_FailClosedBarProjection` then: identical re-delivery dedups once
   (no double count), conflicting overlap raises `_ProjectionConflict` leaving projection
   untouched (no partial application / no silent replacement).

## Steps / resume points
- [DONE step 1] Read recon, GW01 fixture, service_market.py, market_hub.py,
  market_history_protocol.py, test_service_market_history.py, test_service_history_host.py,
  cmd.txt env. Confirmed evidence dir D:/temp/it10dim02seam-20261016-r1 does not pre-exist.
- [DONE step 2] Wrote status file + designed 4 nodes.
- [THIS RUN step 3] Write test file D:/bt_api_py/tests/unit/test_service_market_dim02_seam.py.
  RESUME: if file exists, re-read it first; check it compiles (py_compile with anaconda) and
  then continue to step 4.
- [step 4] Serialization check (`ps -W | grep -i python | grep -v grep`) then run full file:
  evidence dir D:/temp/it10dim02seam-20261016-r1/run1-{cmd.txt,stdout.log,stderr.log,exit.txt}
  (mkdir -p the dir first; basetemp run1/basetemp). On failure: focused node reruns run2+.
- [step 5] sha256 the test file; write report.md with commands/exit codes/pass counts, the
  documented correction policy, proven vs NOT proven, findings/gaps.

## RESULTS (COMPLETE, 2026-10-16)
- New file written: `D:/bt_api_py/tests/unit/test_service_market_dim02_seam.py`
  sha256 `7c86904384a8423d42b2cccdc92984ef185a8e2e9e9df19c705b5612531a0bc6` (1129 lines, 4 nodes).
- Run 1 (full file) exit 0: `4 passed, 1 warning in 11.94s` (run1 evidence dir).
- Run 2 (rerun)  exit 0: `4 passed, 1 warning in 12.45s` (run2 evidence dir).
- Run 3 (collect-only) exit 0: 4 tests collected (run3 evidence dir) — node list matches plan.
- No production/existing test file modified; nothing staged/committed (`git diff --cached` empty;
  file shows as `?? tests/unit/test_service_market_dim02_seam.py`). No seam `__pycache__` written.
- Serialization: probes before each run — only two long-lived uvicorn dev servers; one transient
  `python -c "import backtrader ..."` probe from another agent line (12:19:20) already exited
  before run 2; no waits needed (recorded in each cmd.txt).
- Observed finding (matches expected honest behavior): SDK forwards identical duplicates AND
  conflicting revisions of already-read bars verbatim, ordered, no SDK-side dedup/merge/replace/
  drop; fail-closed enforcement demonstrated at the consumer projection boundary
  (`_FailClosedBarProjection`) — documented in the module docstring; 07B is the accepted
  BT-side consumer proof and was NOT re-run.
- Report: `D:/temp/it10dim02seam-20261016-r1/report.md` (same findings; read there for details).

## Resume point for a successor
- THIS increment is DONE; nothing pending or partially edited in this file line. If continuing
  DIM02: remaining scope belongs to other agent lines — (1)+(4) multi-key/sparse options in
  `tests/unit/test_service_market_dim02_integration.py`, (5) `bt_api_py/service_market_legs.py` +
  `tests/unit/test_service_market_dim02_legs.py` / `test_service_market_legs.py` (untracked,
  other lines; do not touch). The optional BT-feed-level seam node is deferred and would live in
  the Backtrader repo, not here; do not re-run 07B as delivery. Final merge/registration of
  accepted DIM02 rounds is a coordinator/merge step outside this file's scope.
- To re-verify this increment from scratch: re-run the exact command in
  `D:/temp/it10dim02seam-20261016-r1/run1/cmd.txt` (serialization probe first).
