# DIM01 phase-2 status (successor agent, session p3 2026-10-16)

## RESULT: MISSION GREEN - integration test passes end-to-end
Canonical run20 (post all concurrent churn; note: other workstream committed submodule
bt_api_risk HEAD a1485ee -> 131175a during this session; no commits by this session anywhere):
  cd D:/bt_api_py && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/anaconda3/python.exe -m pytest --noconftest --import-mode=importlib -q --basetemp=D:/bt_api_py/.codex-tmp/dim01-p2-r1/run20/pytest-tmp --junitxml=D:/temp/it10dim01-p3-20261016-r1/run20-junit.xml tests/integration/test_dim01_identity_isolation_service.py -x
  EXIT=0; junit tests=1 errors=0 failures=0 skipped=0.
Also green: run11 (first check), run12 (junit), run17+run19 (re-checks during/after churn).
Predecessor run10 had failed at line 1546 average_price None; predecessor's average_price fixes
(journal reference + _command_reply mapping) were already in the worktree and made it pass.

## Work done this session
1. Kept all predecessor partial work, nothing reverted. Verified predecessor fixes present:
   admission.py risk_freezes.blocking + latch_dispatch_identity_conflict; entry
   _resolve_terminal_exposure latch on identity-conflict denial codes; _command_reply
   average_price=latest.get("average_price"); journal _weighted_average_price + reference
   validation; exposure.py optional trade_id_domain_ref/trade_id_scope.
2. Resolved predecessor's last dangling investigation (history item 3):
   tests/unit/test_service_risk_only_order_integration.py::
   test_terminal_source_binds_unknown_to_reserved_permit_without_command_column
   got TERMINAL_EXPOSURE_COMMAND_CREATION_MISMATCH, expected TERMINAL_EXPOSURE_SOURCE_INVALID.
   Root cause: TEST bug. It set order_request_sha256=sha256(canonical_json(request)) (bare)
   but production service_risk_order_entry.py:927-931 and journal verification
   service_risk_order_journal.py:3452-3466 require the creation envelope
   {exchange_name, operation "BtApi.make_order(normalized=True)", request}.
   Fix applied test-side only (production untouched); test now passes.

## Owned test suites (final state on current tree)
- tests/integration/test_dim01_identity_isolation_service.py: 1 passed (run19)
- tests/unit/test_service_order_entry_components.py + test_service_risk_order_journal_decimal_match.py:
  18 passed, EXIT=0 (run16)
- tests/unit/test_service_risk_only_order_integration.py: 14 passed / 6 failed (run15)

## Remaining gap (NOT fixed; registry semantics outside this workstream's ownership)
All 6 failures = client RESTART registration -> ServiceRuntimeError DUPLICATE_ACTIVE_RUN:
 - test_spawned_risk_service_gates_idempotent_orders_and_metadata_scope
 - test_claimed_but_unjournaled_order_is_unknown_after_service_restart
 - test_provider_written_order_is_reconciled_after_restart_without_resend
 - test_proof_ready_crash_recovers_from_exact_risk_receipt_without_redispatch [3 params]
Root cause (verified via leftover registry DB in run15 basetemp):
 - bt_api_py/service_registry.py _claim_strategy_write (~1930-1974): durable row keyed
   (execution_domain_ref, strategy_ref, account_ref); row.run_id != scope.run_id -> refusal.
 - unregister (_renew_or_unregister ~2045-2057) only NULLs active_registration_id, never
   clears run_id, so after a clean client close any NEW run_id is refused forever.
 - client runtime generates fresh random run_id per start (service_runtime.py:332, injected
   at 1012); tests assert restart yields a NEW run_id AND succeeds (cross-run dedup), i.e.
   they require registry takeover-on-release or client run adoption - NOT in this workstream's
   ownership (group-orders workstream is concurrently editing service_execution_group*.py and
   the risk submodule's service_component.py). Left untouched by design.

## Concurrent worktree churn (warning)
Another live workstream edited the same worktree during this session: service_components.py
+87 uncommitted then reverted; service_execution_group_authority.py; tests/unit/
test_service_account_integration.py; test_load01_hundred_strategy_30min.py; submodule
src/bt_api_risk/service_component.py became modified; new untracked
tests/unit/test_service_group_policy_control_plane.py. Re-verify before the commit round.

## Files changed by THIS session
- tests/unit/test_service_risk_only_order_integration.py (only edit: envelope-digest
  computation in the terminal-source test; sha256 9d29b5884dfeada810a0c0c4885965ef04d3bd49c91da8091f7ce51d1412f552)
- NO production edits. Predecessor uncommitted production files kept as-is:
  entry 67c98d5b..., journal 37d89c33..., admission cc595bc7..., exposure 0d1584fe...,
  integration test 733b85a8... (untracked)

## Evidence
D:/temp/it10dim01-p3-20261016-r1/: run12-junit.xml+run12-stdout.txt, run13-owned-units.txt,
run14-unit-integration.txt, run15-full-unit.txt, run16-owned-units2.txt,
run17-integration-recheck.txt, run18-spawned-recheck.txt, run19-final-stdout.txt,
run19-junit.xml, run20-final-stdout.txt, run20-junit.xml, summary.txt,
sha256-before.txt, sha256-after.txt, sha256-final.txt
Run dirs: D:/bt_api_py/.codex-tmp/dim01-p2-r1/run11..run20 (predecessor run2..run10 untouched)
