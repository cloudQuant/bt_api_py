# MIG01-R2 independent adversarial review — 2026-10-16 (read-only)

Reviewer: external adversarial reviewer. Scope: uncommitted R2 change set (2 files) in
D:/bt_api_py @ dev/ea2429f8. No commit/stage/modify of repo files; no other agents' files touched.
Evidence: D:\temp\it10mig01-r2-review-20261016-r1 (fresh; author dir it10mig01-r2-20261016-r1 untouched).

## Resume points
- [x] 1. Hash + git status: change set scope confirmed
- [x] 2. Full diff read vs HEAD; assertions/doubles/skips audited
- [x] 3. 4 wire-gap claims independently probed (none falsified)
- [x] 4. Fresh reproduction (pair + per-file + red targets) — matches author
- [x] 5. Serialization complied; residual-scan delta recomputed 166/35
- [x] 6. Status + verdict

## Verdict: GO-with-disclosures
Change set is sound to commit. All author claims verified; the two disclosures are documented
semantic re-expressions (R1-accepted pattern), not hidden weakenings.

## Checklist detail
1. Hashes @ review start AND end (unchanged, no mid-review edit):
   - tests/bt_api_contract/test_operation_dispatch.py = e9b670bbf6aa6a436c95e258fc11557479d71769da7e30d2c5a184054fd4c03a ✓ exact
   - tests/bt_api_contract/test_environment_guard.py = 3beb5c45ba1e2506eec88f07acc56cf910247181518b1f03631231ca1c6b938c ✓ (prompt text truncated last char; matches author spec pin + author status "3beb5c45…")
   - git status --porcelain tests/bt_api_contract/ = exactly these 2 modified. Red targets
     (test_execution_session.py 6496100809…, test_gateway_runtime_unknown_execution.py 27f30fd7…)
     match author spec pins → untouched. Other dirty files are other agents' (not attributed/touched).
2. Diff audit:
   - Routing node (12 params) + legacy-cancel node: assertions byte-identical; only harness
     _zmq_api→_direct_api (diff operation_dispatch hunks 65-84, 78-84).
   - RecordingBackend: PRE-EXISTING, identical at HEAD (not newly added). Non-masking: returns
     {"operation": <method-name>} and tests assert return value == {"operation": operation} AND
     backend.calls args (feed key, request isinstance, consistency kwarg). A wrong route/args fails.
   - ExplodingFeed: message-string change only.
   - Two re-expressed nodes (DISCLOSURE 1): get_trades node and async-surface node now assert
     (i) OperationBackend lacks get_trades, (ii) retired ZMQ construction raises
     LegacyForwardingRetiredError("transport_mode=ZMQ") (bt_api.py:3646-3656, reached at :988
     BEFORE init_exchange :995), (iii) ExchangeRegistry.create_feed booby-trap (real call site
     bt_api.py:3926) + backend untouched for get_command_status. Old ZMQ runtime assertions are
     unreachable since R1 retirement → no real failure can hide; pattern matches R1 GO-reviewed
     in-surface tests (test_direct_zmq_bt_api_contract.py, test_normalized_public_api.py).
   - No new skips/xfails/masks in either file (grep clean). The single pin-env skip
     (test_direct_legacy_async_reads_reject_plain_def_returning_none) is pre-existing at HEAD,
     exactly as claimed. Test-def counts old==new (dispatch 7, guard 24).
   - Guard node 1 exact-dict equality still guards secret leaks (fixture injects api_key/secrets
     into OKX params). Guard test 3 tail (market_data_only write rejection) unchanged; rejection
     is production (bt_api.py:2829/3068), Backend double only returns canned success.
   - DISCLOSURE 2 (minor doc nit): `_retired_zmq_api()` docstring says "kept verbatim" but adds
     `{"SIM___SPOT": {}}` — deliberate strengthening (makes the no-feed-construction trap
     meaningful if retirement ordering regresses). No behavioral loss.
3. Wire-gap claims — all four verified, none falsified:
   (a) DirectBackend ctor (bt_api_py/_direct_backend.py:27-40) has no client hook; make_order
       (:292-330) routes FeedAdapter/venue mapper/feed only. Zero GatewayCommandRouter/wire_mapping
       usage under bt_api_py/*.py; only server-side runtime_plugins/gateway_dispatch.py:518.
   (b) bt_api/bt_api_gateway/src/bt_api_gateway/wire_mapping.py (145 lines): generic envelope
       schema "bt_api_gateway.command.v1", exact-field checked, opaque payload; NO OrderCommand/
       CommandAck/quantity_unit/position_id projection, no ack model. Old bt_api_py/forwarding/
       schema.py still on disk (OrderCommand.extra, CommandAck) but unreachable (retired).
   (c) router.py:397-426 confirmed: executor exception → mark_unknown + GatewayCommandStatus.
       UNKNOWN + raise GatewayDispatchError (~403-418); executor return (action_claim) →
       RETURNED_UNVERIFIED "a local executor return says nothing about provider acceptance"
       (~420-433). Old GatewayRuntime._handle_command (bt_api_base/gateway/runtime.py:110-223):
       exception-after-dispatch → unknown ack (no raise); explicit rejection → definite_reject+
       terminal_confirmed; accepted → terminal. Inversion/absence-of-definite on new surface TRUE.
   (d) bt_api_py/_normalization.py make_order path (~2675-2706) consumes order-shaped rows via
       order(); zero GatewayDispatchResult/GatewayCommandStatus refs under bt_api_py (only
       server-side plugin :554). No outcome bridge exists.
4. Fresh reproduction (author's exact env; separate stdout/stderr/exit per run in evidence dirs
   reviewer-r1/ reviewer-red/ reviewer-perfile-*/):
   - Pair: exit 0, "64 passed, 1 skipped, 2 warnings in 3.34s" — matches author rehomed-r1/r2.
     Per-file: dispatch 17P+1S exit 0; guard 47P exit 0 (task's "each 64/1" = pair total, as author says).
   - Red targets (+pair, one session): exit 1, "3 failed, 245 passed, 3 skipped, 2 warnings,
     14 errors". junit parse: exactly 17 red = 3F test_session_uses_real_gateway_command_roundtrip
     _with_native_mt5_intent[result0-True/result1-False/result2-False] + 14E gateway file (6+4+1+2+1).
     All reds are the expected nodes; zero unexpected. Deferred: author's full-suite "653 passed
     elsewhere" not re-run (lean run covers 4 modules; optional per mission).
5. Serialization: foreign runner PID 76928 (dim09/service_execution_group suite) start 12:54:45 +
   multiprocessing burst till ~12:56:20 → bounded wait ~100s, 0 kills, gates clear for all runs
   (only resident daemons 128016/132224). Recorded in each reviewer-*/serialization.txt.
6. Residual scan recomputed from saved scan JSONs (no re-execution):
   - R1-r3 baseline (D:/temp/it10mig01-r1-20261016-r3/scan-residual.json): 168 refs / 36 files
     (bt_api_py.forwarding 59/24, gateway 6/5, bt_api_base.gateway 12/6, GatewayRuntimeRegistrar 9/3,
     ZmqBtApiBackend 33/7, zmq_transport_mode 49/11).
   - R2 (author scan-r2): 166 / 35 (zmq_transport_mode 47/10; others equal). Only file deltas:
     test_environment_guard.py REMOVED; test_operation_dispatch.py 1→2 hits [97]→[4,124]
     (docstring + explicit-retirement call). Exact match to author claim.
   - scan-artifacts.json sha 18c9885cfeefc85c… and scan-behavior.json sha e5dd9848323773b0…
     recomputed == claims (== R1). scan-r2 pytest 64P/1S exit 0.
7. R1 precedent: .codex-tmp/mig01-r1-review-status.md = GO; retirement fail-closed verified at
   bt_api.py:988 before plugins/feeds — consistent.

## Unproven / deferred
- Author's whole-spec targets-and-regression aggregate (653 passed / 20 skipped) not re-run —
  only the 4 target modules (lean, per time constraint).
- R1-r3 baseline figures taken from author's saved scan artifact, not re-executed.
- Red-by-design nodes remain red (expected); their future green depends on the planned wire round.

## Run log (reviewer)
- 12:53 wc/diffs export; 12:54-12:56 foreign runner observed (waited); 12:56:27 ps clear.
- 12:57 reviewer-r1 pair: exit 0 64P/1S. 12:58 reviewer-red: exit 1 3F/14E/245P/3S exact 17.
- 12:59 per-file: 17P+1S / 47P. 13:00-13:05 scan recompute, wire-gap probes, final hashes/status.
- Nothing staged/committed; no repo file modified.
