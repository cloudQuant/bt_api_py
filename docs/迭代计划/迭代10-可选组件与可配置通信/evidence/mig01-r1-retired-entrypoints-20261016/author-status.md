# MIG01-R1 status — 2026-10-16 (final; counts corrected same day per independent review finding #1)

Scope: cut retired legacy ENTRY POINTS to explicit migration errors (additive gating only).
This is an INCREMENT, not a MIG01 acceptance claim: both legacy dirs stay on disk; the
wheel still packages them (see scan).

## What changed (working tree, NOT committed)
- `bt_api_py/_contracts/errors.py:135` new `LegacyForwardingRetiredError(BtApiContractError)`
  (attrs: `target`, `detail`; message `legacy forwarding entry point retired: {target}`).
- `bt_api_py/_contracts/__init__.py:16,79` re-export.
- `bt_api_py/__init__.py:88,173-190,226-229,331-340,383` retired-name set
  `_RETIRED_FORWARDING_EXPORTS` (13 names) + `GatewayClient` now raise
  `LegacyForwardingRetiredError` from `__getattr__` instead of importing the legacy
  runtime; names stay enumerable in `__all__` (star-import also raises — explicit, not
  silent); error class exported.
- `bt_api_py/bt_api.py:39-44,3648-3656` `_build_backend` ZMQ branch raises
  `LegacyForwardingRetiredError("transport_mode=ZMQ")` before any socket/thread;
  docstring 931-936 updated. `ForwardingConfig` kept as rejection input.
- Tests updated (in-surface only):
  - `tests/test_bt_api_quality.py:189` retired export expected to raise.
  - `tests/bt_api_contract/test_direct_zmq_bt_api_contract.py:37,48` ZMQ startup
    rejection tests (thread-stable assertion) replace 3 legacy-ZMQ behavior tests.
  - `tests/bt_api_contract/test_normalized_public_api.py:782` ZMQ init now asserts
    rejection + no `create_feed` fallback (name updated).

## Evidence
Runner: `.codex-tmp/iteration10-mig01-r1-scan.py`; spec `.codex-tmp/iteration10-mig01-r1-spec.json`.
- r1: `D:\temp\it10mig01-r1-20261016-r1` (pytest_exit=0, parent 99bec079, stdout fbb03be0…;
  superseded by a lint-only rework of one assertion afterwards; all artifacts retained
  unmodified — the r1↔r2 spec pin diff is exactly `tests/test_bt_api_quality.py`, closing
  review finding #3 by artifact).
- r2: `D:\temp\it10mig01-r1-20261016-r2` — pytest_exit=0, 83 passed / 0 fail (junit.xml),
  sources_unchanged=true, heads_unchanged=true (parent 99bec079, base b4aabd1e),
  stdout_sha256 93af382b…; retained unmodified (the reviewed run).
- r3 (final, commit-time tip): `D:\temp\it10mig01-r1-20261016-r3` — re-run per review
  finding #2 (r2's head anchor 99bec079 pre-dated the round-154/155 commits): pytest_exit=0,
  83 passed / 0 fail (junit 83/0/0/0), sources_unchanged=true, heads_unchanged=true
  (parent 5ff3da11, base b4aabd1e), stdout_sha256 ff80a781…; all 7 pins re-verified;
  scan JSONs byte-identical to r2.
- Behavior probe (fresh interpreter): top-level `ForwardingClient` → LegacyForwardingRetiredError
  (target=ForwardingClient); `BtApi(transport_mode="zmq")` → error (target=transport_mode=ZMQ);
  threads stable; `bt_api_py.forwarding`/`bt_api_py.gateway`/`zmq` NOT loaded; direct mode OK.
- Residual inventory: 168 legacy references / 36 files (forwarding 59, py.gateway shim 6,
  base gateway 12, GatewayRuntimeRegistrar 9, ZmqBtApiBackend 33, zmq mode 49).
- Artifacts: legacy dirs present 14+4+16 py; wheel 0.15.4 = 19 legacy entries / 145;
  egg-info legacy lines 68+22; CI workflow hits in 3 files.

## Breakage matrix (untouched, positively rely on legacy path — not hacked)
| test path | broken nodes | why | proposed owner/round |
|---|---|---|---|
| tests/bt_api_contract/test_operation_dispatch.py | 14–15 | `_zmq_api()` factory harness for backend-routing assertions | MIG01 final re-home (direct backend + OperationBackend double) |
| tests/bt_api_contract/test_environment_guard.py | 3 | env-guard semantics under ZMQ transport | env-guard owner / same re-home round |
| tests/bt_api_contract/test_execution_session.py | 3 | session↔base-gateway wire via ZMQ api | D2/D3 gateway-wire round |
| tests/bt_api_contract/test_gateway_runtime_unknown_execution.py | 14 (whole file) | base gateway wire outcomes via ZMQ api | D2/D3 gateway-wire round |

Corrections (2026-10-16, per independent review finding #1): the earlier per-pattern sum of
37 was an overstatement. Observed totals are 34–35 nodes — 20 failed + 14 errors under the
pinned anaconda env (dispatch 14 failed + 2 skipped), 15 failed for dispatch with a working
asyncio plugin; the direct legacy-async node at test_direct_zmq_bt_api_contract.py:279 does
NOT rely on the retired path (it passes with the asyncio plugin) and is excluded.

Direct-module legacy tests unaffected and still green: tests/forwarding/, test_forwarding_*,
test_light_import, test_type_hint_introspection, test_readonly_futures_metadata,
test_position_mode_mutation, test_gateway_runtime_integration, test_demo_futures_contract,
test_ctp_orderref_consume (198 passed spot-run).

## Not done / boundaries
No commit; no base edits; no docs edits; no service_*/risk/execution/gateway files touched.
`_contracts/errors.py`/`__init__.py` edit is outside the strict owned list but outside all
fences and required for the stably-named error (additive, 19 lines; corrected from an
earlier "21" count per independent review finding #1).

## Recommended next increment
MIG01-R2: re-home the 4 breakage-matrix modules off the retired factory (RecordingBackend/
direct construction), then D2 base-runtime slice (base gateway deletion + plugin registrar
annotations) with the same scan re-run expecting a shrunk residual set.
