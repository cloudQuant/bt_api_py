# REG04 INCR-1 status (iteration-10, test-only round)

Started: 2026-10-16. Goal: ONE new host-level test file
`tests/unit/test_service_reg04_worker_isolation.py` proving at proxy/service level:

1. 2 application workers (distinct client_session_id, manual-book scope) + 1 strategy run
   coexist on SAME account owner; strategy claim held; 2nd strategy run →
   DUPLICATE_ACTIVE_RUN; workers unaffected.
2. Account-only write and unknown/other manual book → SCOPE_DENIED.
3. Service registering service.group_reservations WITHOUT TrustedGroupReservationPolicy →
   SCOPE_DENIED at register/call, zero handler/provider calls.
4. Monitor-control handoff with wrong scope/route/actor → rejected (no privilege escalation).

Constraints:
- NEW FILE ONLY. No edits to existing files. Never touch service_risk_order_entry.py,
  service_risk_order_journal.py, bt_api_risk/**, service_execution_group*.py,
  service_group_reservations.py, the 3 listed integration test files.
- No staging/committing.
- Evidence dir D:\temp\it10reg04-20261016-r1 (fresh). >=2 full runs + 1 collect-only, exit 0.
- Serialization: `ps -W | grep -i python | grep -v grep` before each run; bounded waits <=120 s.
- Interpreter C:/anaconda3/python.exe (py3.11.5).

## Resume points
- [x] recon read
- [x] pattern files read (registry 771-1051, group lifecycle, monitor wiring, host spawn patterns)
- [x] status file written (this file)
- [x] design nodes
- [x] write test file
- [x] evidence collect-only + run 2 + run 3 + confirmation run 4 (run 1 was pre-fix; see Evidence log)
- [x] final report

## Deliverable
D:\bt_api_py\tests\unit\test_service_reg04_worker_isolation.py (new file only;
no existing file was edited or staged/committed).
sha256 = 7c749a6810295b8f7431d6e01bf39663e3477b248c32455f588e338685910861

Nodes (all real machinery: spawned ServiceHost processes over real CURVE/zmq
transports, real ServiceRegistry/SqliteExecutionStore/DurableRiskGate/
DurableControlLedger; deterministic synthetic providers; ephemeral ports; no sleeps):
1. test_spawned_execution_owner_isolates_application_workers_from_strategy_claim
   (clause 1+2): spawned execution-owner host; worker A + worker B application
   runtimes register against the same account mount (distinct client sessions,
   manual-book scopes, account_ref binding); strategy run "run-a" registers
   broker.orders write mount and its claim row (run-a, rid-a) is held while the
   run is live; second strategy runtime "run-b" start() raises
   ServiceRuntimeError DUPLICATE_ACTIVE_RUN (state "failed"); claim row
   unchanged; both workers renew with degraded_count 0; raw proxy probes:
   account-only write (no manual_book) → SCOPE_DENIED, unknown/other manual book
   → SCOPE_DENIED, exact book scope → accepted; ordered close; after close the
   strategy_write_claims table shows exactly one row (run-a, released).
2. test_host_start_refuses_group_reservations_without_trusted_reservation_policy
   (clause 3, host level): ServiceHost.start() for a risk component bound to
   service.group_reservations with an authority lacking per-instance
   TrustedGroupReservationPolicy fails with ServiceHostError code
   GROUP_RESERVATION_POLICY_REQUIRED; resource_provider was called exactly once
   and the adapter was never constructed (state "failed", endpoint None,
   summary start_failure). Contrast: same host WITH the policy proceeds past
   the gate (fails later on GROUP_RUNTIME_BUNDLE_*, proving the policy was the
   discriminator and the provider was still called once).
3. test_reservation_route_without_trusted_policy_is_denied_with_zero_handler_calls
   (clause 3, registry/proxy level): registry.handle() register+dispatch of
   service.group_reservations without the trusted policy → SCOPE_DENIED with
   zero handler calls; with policy: unbound dispatch → DUPLICATE_ACTIVE_RUN
   (zero calls), bind_trusted_group_run then wrong-scope dispatch →
   SCOPE_DENIED, forged actor identity → DENIED, unknown registration →
   NOT_REGISTERED, all zero calls; valid dispatch → exactly one handler call.
4. test_spawned_monitor_control_rejects_wrong_scope_route_and_actor
   (clause 4): spawned monitor-control host (MonitorControlService with a
   caller_bindings entry keyed by full WireIdentity, HMAC v2 verifier,
   DurableControlLedger, DurableRiskGate behind a counting activate_freeze_once
   wrapper). Authenticated caller: wrong wire scope/other account →
   SCOPE_DENIED; internal owner op monitor.claim over the wire →
   CAPABILITY_UNAVAILABLE (no escalation); cross-scope/account signed commands
   via the runtime → SCOPE_DENIED; a fully authenticated intruder principal
   using the SAME scope/route grant as the caller → SCOPE_DENIED at the
   caller_bindings identity check; freeze call counter stays 0 across all
   rejections; one valid signed freeze applies exactly once
   (effect_ref == monitor_freeze_cause_id, ledger row
   (acknowledged, risk-owner:reg04, applied, expected)); rejected command ids
   have no ledger row; ordered close with no fallback cleanup.

## Findings / production gaps
- No production gap found. All four clauses hold on current code; zero
  production changes were needed or made.
- Node-4 first-run failure was a TEST-side bug (intruder grant scope_ref did
  not match its client binding scope_ref, so denial happened at resource
  authorization instead of the caller_bindings identity check; and the spawned
  host's command parser compared bare strings while the shared spawn helpers
  send tuples). Fixed in the new file only; final two runs are green.
- Environmental note: pytest 8.0.0 + pytest_asyncio 0.23.0 in C:/anaconda3
  crash collection for ANY repo file ("'Package' object has no attribute
  'obj'"); `-p no:asyncio` works around it (reproduced on
  tests/unit/test_service_registry.py). New file needs no asyncio plugin.

## Evidence log
Window: D:\temp\it10reg04-20261016-r1
  - commands.txt        exact commands, rationale, results summary, wait record
  - run-collect-final.out / .err   collect-only: exit 0, 4 tests collected
  - run2.out / run2.err / run2.exit   full run: exit 0, 4 passed, 1 warning, 4.74s
  - run3.out / run3.err / run3.exit   full run: exit 0, 4 passed, 1 warning, 4.74s
  - run4.out / run4.err / run4.exit   confirmation run after concurrent agents
      changed production files: exit 0, 4 passed, 1 warning, 4.92s
  - run1.out / run1.err / run-collect.out / run-collect.err
      historical pre-fix artifacts (run1: 1 failed 3 passed — test-side node-4 bug)
  - test-file.sha256    7c749a6810295b8f7431d6e01bf39663e3477b248c32455f588e338685910861
  - production-modules.sha256   hashes of service_registry/host/components/runtime/
      config/group_reservations/execution_group taken at run-4 time
Serialization: `ps -W | grep -i python | grep -v grep` before each run. Runs 1-3:
only two stale Oct-13 processes present, no waits. Before run 4 other agents'
python runners were active; bounded waits of 30 s + 60 s + a 120 s loop (15 s
polls) were used; run 4 started once the environment cleared at 12:46:14
(per-wait bound <=120 s respected). Post-run checks confirmed no leftover
children. A concurrent agent modified bt_api_py/service_execution_group*.py and
bt_api_py/service_group_reservations.py at 12:41:51-12:41:55 (right after run 3);
those changes were NOT made by this round (this round edited only the new test
file), and run 4 re-validated the suite under the newer production state (exit 0).

## Remaining gaps
- None for INCR-1 scope. INCR-2 items from reg04-recon.md §3 remain out of scope
  for this round.
- Concurrent agents were seen actively editing production files (not by this
  round); evidence pins both the test-file hash and production hashes at run-4
  time, but downstream rounds should re-run this suite once the parallel work
  settles.
