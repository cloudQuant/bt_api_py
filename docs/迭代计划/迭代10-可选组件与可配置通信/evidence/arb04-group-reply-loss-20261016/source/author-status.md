# ARB04 workstream status (iteration 10) — COMPLETE

Date 2026-10-16. Mission: build next ARB04 increment — two-leg group scenario for
submit-reply loss/timeout over REAL service chain with durable journal; prove
UNKNOWN → no resubmit, no early release; LATE fill recovered via original
business ID exactly once; no blind reverse/compensating order before facts
confirmed. Plus DESIGN-ONLY section on group-level cancel under reply loss.

Plan anchor: `D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/正式迭代计划.md:770`.
Gapmap: `.codex-tmp/arbdim-gapmap.md` ARB04 section (was 45%: 不重发/不早释/原 ID 查询齐；socket 故障与撤单缺).

## Deliverables
- NEW `D:/bt_api_py/tests/integration/_arb04_group_reply_loss_support.py`
  (`_WireFaultState`, `_ReplyLossTransport`/`_ReplyLossTransportFactory`, `_arb04_coordinator_host_process`,
  `Arb04GroupCluster`; reuses ARB03 helpers/constants — only the coordinator spawn target differs).
- NEW `D:/bt_api_py/tests/integration/test_arb04_group_reply_loss_service.py`
  (Test A terminal via original-ID; Test B late-fill freeze). No production file touched.
- Evidence `D:/temp/it10arb04-20261016-r1/` (command.json-style facts, before/after pins,
  junit xml x4, stdout/stderr, exit codes, results summary). NO commits made anywhere.

## Runs (all from D:/bt_api_py; PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, PYTHONDONTWRITEBYTECODE=1,
## PYTHONPATH=bt_api_py + 4 src trees, -q -p no:cacheprovider, fresh --basetemp under D:/temp)
- run1: test_arb04_group_reply_loss_service.py — exit 0, 2 passed (17.8s); run2: exit 0, 2 passed (18.2s);
  deterministic (junit equal up to timestamps).
- regression ARB03 (test_arb03_dim05_group_partial_fill_service.py) — exit 0, 2 passed.
- regression R121 (test_service_execution_group_local_process.py) — exit 0, 1 passed.
- Repo HEADs unchanged across the window (00-before/after-pins.txt). Other workstreams' dirty
  files (risk admission.py, service_registry.py, service_runtime.py, ...) pre-existed and are
  not attributable to me.

## Findings / handbacks
1. RESIDUAL GAP (no owner file edit needed; product decision): a provider fact that first
   materializes AFTER ATTEMPT_UNKNOWN (genuine late fill) has NO commit path:
   `_read_receipt_locked` is a pure journal read (service_execution_group_proofs.py:1594-1767),
   allowed_event_sequences (1646-1653) forbid re-observation after ATTEMPT_UNKNOWN, and no
   group re-observation op exists. Test B pins the honest freeze (stays UNKNOWN,
   error "order_outcome_unknown"). Hook candidates for a future increment:
   (a) a new journal event/sequence for a late SOURCE_OBSERVED under the SAME attempt id
   gated by the provider revision/watermark, (b) reuse of facade.reconcile_with_port
   (facade.py:752) which is currently not wired to the group chain, (c) an explicit
   `orders.reobserve_group_leg` op on the account write policy. Until then the group
   correctly refuses to adopt, release or compensate.
2. NO bugs found in CFG06b-owned dirty files during this scenario (they were read-only and
   behaved per contract). Nothing to hand back.

## Group-level cancel under reply loss (DESIGN ONLY — contract level, no implementation)
Current contract facts (file:line):
- Strategy-visible group ops are exactly {groups.submit, groups.query} (service_execution_group.py:525-581);
  no cancel op, no unwind op anywhere in the group chain.
- Group legs are LIMIT + TIF=IOC + OPEN effect only (test host ResourceGrant / provider contract
  supported_time_in_force=("IOC",), supported_position_effects=("OPEN",)); Dispatcher.cancel must
  raise (group-only owner must not dispatch cancellation) — cancel semantics do not exist yet.
- `GroupOrderState.UNWINDING` is declared (group_order_contracts.py:48-82) but has no transition path;
  the hedge-residual arithmetic (HEDGE_LEG_SIZED residual + GROUP_RECOVERY_REQUIRED on hedge failure,
  group_coordinator.py:693-796) is the only "compensation" mechanism and correctly refuses to
  compensate on unproven facts.
- The ONE existing lost-reply recovery template for a cancel-like action is reservation release:
  `RELEASE_REQUESTED` phase, promote to RELEASED only from one complete durable receipt
  (`RESERVATION_RELEASED`, payload recovered_after_lost_reply=True) — group_coordinator.py:339-358;
  budget release contract: active states must keep HELD; RELEASED only with all-reservations-safe
  evidence (group_store.py:540-573; group_order_contracts.py:1371-1391).

Design (contract-level):
1. New op `groups.cancel` (strategy → coordinator), idempotent by (group_id, cancel_request_id);
   allowed from {PREPARING, FIRST_PENDING, HEDGE_PENDING}, and from RECOVERY_REQUIRED only AFTER
   the leg facts are confirmed via the original child intent id (query-first, cancel-second —
   never cancel as a substitute for proving facts; never treat a cancel as a rollback).
2. Each child cancel carries a NEW deterministic business ID derived from the group/leg
   (e.g. group_leg_cancel_intent_id(group_id, leg_id)); the account-side cancel is a proof
   session exactly like submit: claim -> dispatch -> observe -> terminal receipt, journal chain
   keyed by that original ID. A lost cancel reply must NOT resend: phase UNKNOWN_CANCEL +
   FIRST/HEDGE_LEG_STATUS_OBSERVED {"code": "ORDER_CANCEL_OUTCOME_UNKNOWN"}, recovered ONLY by
   querying the cancel intent's original ID (this increment proves the identical pattern for submit).
3. Terminal cancel facts: full cancel (fill 0) vs partial-remainder cancel (fill > 0) — the second
   must commit the exact fill facts once (the IOC remainder-cancel commit path already exists:
   proofs.py:2304-2324) and size/settle the hedge/residual arithmetic exactly as a first-leg
   remainder cancel does today.
4. Budget hold stays HELD while ANY leg or leg-cancel outcome is unproven; the group reaches
   CLOSED only with exact terminal facts for both legs (group_store requires proven-no-exposure
   or all-reservations-safe before release, group_store.py:540-573). No early release under
   reply loss — same invariants this increment verified for submit.
5. UNWINDING is reserved for explicit compensating legs AFTER facts are confirmed (a separate,
   later operation), each unwind leg a fresh group/child ID with its own UNKNOWN discipline; a
   failed unwind leg goes back to RECOVERY_REQUIRED with the exact residual — "compensation is
   not a successful rollback" is preserved by construction.
6. A cancel whose fill arrives at the provider late (after UNKNOWN) inherits finding #1 exactly:
   without a re-observation path it must stay honestly UNKNOWN; the cancel design must therefore
   land together with the late-fact re-observation decision (hook candidates in finding #1).
7. Missing hooks to build when implemented: coordinator op + grants (service_execution_group.py:525-581),
   account-side `orders.cancel_group_leg` capability + write grant, ExecutionOrderEntryService
   group-cancel path (service_order_entry.py), proof-journal cancel session + new allowed sequence
   (service_execution_group_proofs.py:1594-1767), risk leg cancel-exposure resolution facts.

## Ownership constraints (verified; respected)
- `bt_api_py/service_execution_group.py` / `service_components.py` were DIRTY (CFG06b) at recon time,
  are now committed (HEAD d85ec636 at run time) → were treated READ-ONLY; no edits by me.
- bt_api_risk `admission.py` (DIM03/CFG06b) → READ-ONLY; bt_api_base/bt_api_execution/bt_api_transport_zmq
  → non-editable this round; no production edits were needed anywhere.
- NO commits/staging/push anywhere. Evidence not overwritten (fresh dir, FRESH-verified first).
