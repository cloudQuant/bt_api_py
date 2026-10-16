# MIG01-R2 status — 2026-10-16 (re-home 4 retired-factory test modules)

Scope: re-home the 4 breakage-matrix modules off the retired BtApi-ZMQ factory,
preserving asserted semantics. No commit/stage (root agent commits). No worktrees.

## Resume points (FINAL)
- [x] 0. git status checked: no foreign dirt on any of the 4 target modules
- [x] 1. read docs: mig01-r1-status.md / mig01-recon.md / mig01-r1-review-status.md
- [x] 2. per-module re-home design (direct backend + doubles); preview runs green
- [x] 3. implement 4 modules (2 re-homed; #3/#4 = precise gap report, kept RED)
- [x] 4. QA runner + 2 clean runs + collect-only + targets-and-regression + scan delta done
- [x] 5. status + final report (root agent owns commit)

## Re-homed modules (WHAT+HOW)
T1 `tests/bt_api_contract/test_operation_dispatch.py` (pin e9b670bb…):
- `_direct_api()` line 114 = `BtApi(debug=False)`; `_retired_zmq_api()` line 119 keeps the
  verbatim retired factory call for the explicit-retirement node.
- 12-param routing `test_v1_operations_route_only_to_backend` line 191 (was zmq): assertions
  verbatim; backend = RecordingBackend double (line 47) assigned to direct `api._backend`.
- `test_legacy_cancel_is_converted_to_a_typed_request` line 223; `test_operation_backend_protocol…`
  line 256; `test_get_trades_is_an_explicit_capability_failure` line 236 (OperationBackend has no
  get_trades + retired construction raises, create_feed booby-trapped); `test_forwarding_async_surface_and_reconciliation_fail_closed`
  line 276 (ZMQ rejected; direct get_command_status → CapabilityNotSupportedError, backend untouched).
- 1 skip (`test_direct_legacy_async_reads…`): pinned-env asyncio-plugin limitation, same as R1.
T2 `tests/bt_api_contract/test_environment_guard.py` (pin 3beb5c45…): 3 nodes re-homed onto the
file's existing `direct_factory` at lines 663 / 690 / 708 (unverified-proof exact dict; unprovable
required env → required_environment_unverified definite reject; market_data_only read ok + write
market_data_only definite reject). Its zmq residual refs: fully removed (file dropped off scan).

## QA evidence (fixed source, NO host)
Dir: D:\temp\it10mig01-r2-20261016-r1  (fresh; per-run subdirs rehomed-r1/-r2/-collect/
targets-and-regression each with command.json/junit.xml/exit.txt/stdout.log/stderr.log;
root: spec.json/runner.py/result.json/source-before/after.json)
Runner: .codex-tmp/iteration10-mig01-r2-qa.py; spec: .codex-tmp/iteration10-mig01-r2-spec.json
- rehomed-r1: exit 0, 65 tests, 64 passed / 1 skip. rehomed-r2: identical (determinism pair True).
- rehomed-collect: exit 0 (--collect-only clean).
- targets-and-regression: exit 1 = EXACTLY the expected 17 RED nodes (3 session + 14 gateway
  ids listed in result.json adjudication.gap_counts; unexpected_failures []), 653 passed of 690,
  20 skipped (async-plugin class), regression spot-run set all green.
- qa_ok=true; sources_unchanged=true; heads_unchanged=true (f30cb459 anchor); serialization gates:
  4/4 clear on first check, 0 waits (no foreign python runner seen; only resident daemons).

## Residual-scan re-run (same r1-scan.py + patterns)
- .codex-tmp/iteration10-mig01-r1-scan-spec-r4.json → D:\…\it10mig01-r2-20261016-r1\scan
  (after-guard raced root-agent commit MIG03A f0731ec2 mid-run; scan bytes + pins unaffected).
- .codex-tmp/iteration10-mig01-r1-scan-spec-r5.json → D:\…\it10mig01-r2-20261016-r1\scan-r2
  CLEAN: heads stable 7dbc7b71/b4aabd1e, exit 0, 3rd clean pytest of the pair (65/64/1).
- DELTA vs R1 r3 (docs/…/mig01-r1-retired-entrypoints-20261016/):
  totals 168h/36f → 166h/35f. Only pattern zmq_transport_mode moved: 49h/11f → 47h/10f.
  File-level: REMOVED tests/bt_api_contract/test_environment_guard.py; CHANGED
  tests/bt_api_contract/test_operation_dispatch.py 1h→2h (intentional explicit-retirement refs).
  Unchanged: legacy_dirs (14/4/16 py), wheel_legacy_entries 19, egg_info_legacy_lines 90,
  ci_workflow_files 3, scan-artifacts.json sha 18c9885c… (== R1), scan-behavior.json sha
  e5dd9848… (== R1, retirement behavior probe identical).

## T3/T4 GAP REPORT (final; nodes kept failing by design — fail-closed)
Missing surface (what the tests need vs what the new wire provides):
1. Client write path: tests drive `BtApi.make_order/cancel_order(normalized=True)` into a
   command-oriented gateway client. The retired `ZmqBtApiBackend` had the `_client` hook;
   direct `DirectBackend` (bt_api_py/_direct_backend.py) routes writes to venue feeds only and
   has no gateway-client injection point. NO production bridge exists: grep shows zero
   `GatewayCommandRouter`/`wire_mapping` usage under bt_api_py/*.py; the only consumer of
   `bt_api_gateway.GatewayCommandRouter` is the server-side factory
   `bt_api_py/runtime_plugins/gateway_dispatch.py` (managed service host, not a BtApi client).
2. Wire schema for native MT5 intent: old `bt_api_py.forwarding.schema.OrderCommand/CommandAck`
   carried `extra` fields (quantity_unit="lots", position_id) through to_dict/from_dict.
   bt_api_gateway.wire_mapping carries generic `GatewayCommand.payload` envelopes
   (schema "bt_api_gateway.command.v1"); no order-intent projection, no ack model.
3. Ack/unknown policy: old base `GatewayRuntime._handle_command` mapped adapter
   exception→unknown ack, explicit adapter rejection→definite, accepted→terminal ack, and the
   SDK normalization consumed that ack. New `GatewayCommandRouter.dispatch`
   (bt_api/bt_api_gateway/src/bt_api_gateway/router.py:397-426) does the opposite for executors:
   exception→ durable UNKNOWN **and raises** `GatewayDispatchError`; any local executor return→
   `RETURNED_UNVERIFIED` ("a local executor return says nothing about provider acceptance"), so
   "explicit adapter rejection remains definite" has NO new-surface equivalent.
4. No `GatewayDispatchResult`→SDK normalized-write bridge: `normalize_result("make_order", ...)`
   (bt_api_py/_normalization.py:2675-2706 via `order()`) consumes order-shaped
   status/execution_unknown/client_order_id rows; nothing maps GatewayCommandStatus/
   outcome payloads. Synthesizing that mapping test-side would fake the very roundtrip the
   tests assert (tests/test_gateway_runtime_unknown_execution.py title: outcomes survive wire
   serialization AND BtApi normalization) — refused per mission.
What a re-home would need (production work, later round): (a) a first-class SDK write route
from BtApi typed writes to a gateway command client over the new wire; (b) an outcome bridge
(GatewayDispatchResult/adapter outcome → normalized write result incl. execution_unknown /
terminal_confirmed / definite_reject + client_order_id echo); (c) defined terminal-vs-unknown
policy compatible with the assertions (adapter-explicit reject = definite; post-dispatch
exception = unknown/blocked). Until then both modules stay RED (3 session nodes + 14 gateway
nodes) — THIS STATUS IS THE GATEWAY-WIRE ROUND RECORD (DIM06 待处置 items resolved here).

## Exact RED node ids (from QA result.json)
- test_execution_session.py::test_session_uses_real_gateway_command_roundtrip_with_native_mt5_intent
  [result0-True / result1-False / result2-False] (3)
- test_gateway_runtime_unknown_execution.py: 14 (whole file; parametrized set of
  test_adapter_exception_after_dispatch_is_unknown[3 exceptions × 2 commands],
  test_explicit_adapter_rejection_remains_definite[2 × 2], test_explicit_unknown_takes_precedence_over_adapter_error,
  test_logging_failure_cannot_replace_unknown_result, test_normal_adapter_outcome_stays_normal[2])

## Footprint / fences
- Dirty (mine, uncommitted): only tests/bt_api_contract/test_operation_dispatch.py +
  tests/bt_api_contract/test_environment_guard.py. test_execution_session.py +
  test_gateway_runtime_unknown_execution.py untouched. No fence file edited. No waits needed.
- Retirement pins unchanged vs R1 (bt_api.py abceffc2…, __init__.py 6ee02d42…, errors.py 7bf21144…).
- This is an increment; it does NOT claim MIG01 acceptance.
