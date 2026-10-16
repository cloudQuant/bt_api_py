# DIM09 Step-1 status (iteration 10)

Status: COMPLETE (pending main-agent review/commit). All planned code + tests done; all in-scope suites re-run with the new code and match/exceed baseline. Worktree intentionally left dirty (no git add/commit/stash anywhere).

## Repo / HEAD
- Submodule `D:/bt_api_py/bt_api/bt_api_execution`, branch `dev`, HEAD `dc8bd7a3682d00cfaef4f15261607043c6d24fca` at start, worktree CLEAN (verified); start HEAD unchanged at end.
- Evidence dir: `D:/temp/it10dim09-20261016-r1` (cmd.txt/stdout.log/exit.txt per run; all line endings preserved as CRLF).

## Verbatim run command
```
cd D:/bt_api_py && PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="D:/bt_api_py/bt_api/bt_api_execution/src;D:/bt_api_py/bt_api/bt_api_base/src;D:/bt_api_py/bt_api/bt_api_risk/src;D:/bt_api_py/bt_api/bt_api_transport_zmq/src" C:/anaconda3/python.exe -m pytest <TESTFILE> --basetemp=D:/temp/it10dim09-20261016-r1/<run>/bt -p no:cacheprovider
```

## Final run table (all with cmd.txt + stdout.log + exit.txt)
| run dir | suite | exit | result |
|---|---|---|---|
| baseline-coordinator | test_group_coordinator.py | 0 | 12 passed (pre-change) |
| baseline-test_group_budget | test_group_budget.py | 0 | 9 passed (pre-change) |
| baseline-test_group_order_entry | test_group_order_entry.py | 1 | 22 failed at CLEAN HEAD (FOREIGN, see below) |
| baseline-test_group_risk_integration | test_group_risk_integration.py | 0 | 3 passed (pre-change) |
| newfile2-test_group_coordinator_three_leg | tests/test_group_coordinator_three_leg.py (NEW) | 0 | 13 passed |
| final-test_group_coordinator | test_group_coordinator.py | 0 | 12 passed 1.70s |
| final-test_group_budget | test_group_budget.py | 0 | 9 passed 1.45s |
| final-test_group_order_entry | test_group_order_entry.py | 1 | 22 failed 2.41s — FAILURE SET IDENTICAL to baseline (diff of `^FAILED` lines empty) |
| final-test_group_risk_integration | test_group_risk_integration.py | 0 | 3 passed 1.60s |
| extra-test_service_component | test_service_component.py (extra assurance; only role-string overlap) | 0 | 25 passed |

## Decisions pinned in code + tests
(a) v3 `residual_base_units` scalar = sum of abs(vector components) (exact Fraction -> Decimal via `_fraction_to_decimal`); vector in new snapshot field `residual_vector: tuple[Decimal, ...] | None`; v2 keeps byte-identical scalar `first_base - second_base` and `residual_vector is None`.
(b) v3 mid-chain zero-fill (leg 2/3 terminal with filled_quantity == 0): fail closed, later legs never submitted, code `CHAIN_INTERRUPTED_ZERO_FILL` -> stored `chain_interrupted_zero_fill`, residual vector recorded.
(c) v3 side invariant: `"v3 group legs cannot all use one side"`; v1/v2 keep exact `"P5A legs must have opposing sides"`.
Additional: residual-unresolved code `chain_residual_unresolved` (v3) vs `hedge_residual_unresolved` (v2); `chain_quantity_rounds_to_zero`, `chain_step_shortfall` (defensive), `chain_reduction_not_converged`, `chain_prior_leg_not_terminal_filled`, `chain_state_inconsistent`; all lowercase-match `^[a-z][a-z0-9_]{0,127}$`.
Staging: iterated fixpoint whole-group reduction to common B* (≤64 rounds); v3 reuses HEDGE_LEG_* store event types; v3 unquoted strategy request allowed at accept() (version=3 built quote), v2 unchanged.

## Deviation from recon (documented)
The "reject before dispatch if nothing submitted" fork for v3 step shortfall is architecturally unreachable (contract reachability + staged sizing guarantee the first leg is submitted before any reduction); defensive branch `CHAIN_STEP_SHORTFALL` implemented via `_reject_before_dispatch`; tests cover the contract-level reject (`v3 leg ratio cannot be expressed in its step`) and the reachable runtime recovery fork (`chain_quantity_rounds_to_zero` with first fill 0.05).

## Files changed (submodule only; `git status --porcelain`: 3 M + 1 ??)
- src/bt_api_execution/group_order_contracts.py — sha256 67b50a4801d9f508cd9415103dd4eaab55780239fb62669877412d24318ab287 (1584 lines)
- src/bt_api_execution/group_coordinator.py — sha256 81e79337f550c06a00a161787cfd9e39c0ac80c6fce278a179f45d52bdf20e1e (1720 lines)
- src/bt_api_execution/group_store.py — sha256 efba3cd16d7547072552b966210dfd2a9daa3cbf21e35aa98d8b524f0c5c8932 (929 lines; create_or_get version {2,3} + update() residual_vector passthrough only; no schema/DDL bump)
- tests/test_group_coordinator_three_leg.py — NEW, sha256 4c48f71ce5657e8d1e3456f724295c0bb4b19dc744ebb08171d09fc38dffb6a0 (1033 lines)
Untouched: tests/test_group_coordinator.py and every other existing test file; all files outside the submodule; the 4 ARB03-pinned files; bt_api_risk.

## New-file coverage (13 tests, 11-node plan)
1 accept 3-leg quote/version 3/zero remote calls/round-trips; 2 partial first fill -> staged [2,1.5,1.5] + single 3-leg reserve vector (leg-call,leg-future,leg-put) + PAIRED + exact GROUP_PAIRED payload; 3 whole-group reduction to common B*=15 (future leg 1.5 not 1.75) + exact recovery vector (0,2.5,2.5); 4a contract unreachable-ratio reject, 4b runtime 0.05-fill -> chain_quantity_rounds_to_zero (fail closed); 5 mid-chain REJECTED zero fill -> chain_interrupted_zero_fill + vector (0,15,15) + exact event payload; 6 zero-fill first leg -> CLOSED + release once + budget RELEASED + legs 2/3 never sized; 7 lost leg-3 reply -> RECOVERY (error_code None, v2-identical), restart queries ORIGINAL intent id, no resubmit, HELD -> PAIRED; 8 partial mid-leg fill -> chain_residual_unresolved + vector (0,2.5,0) + exact payload; 9 quote bytes/request bytes constant through staging, one reserve call; 10 six contract negatives (2-legs, first-leg start, first ratio!=1, non-first ratio None/0/negative, cross-account, all-same-side, legacy ratio); 11a/11b v2 two-leg regression (byte-identical payload keys, [2,0.75] sizing, hedge_residual_unresolved, residual_vector None).

## Foreign failure (do not fix; not in allowed edit list)
tests/test_group_order_entry.py: 22 failed at CLEAN baseline HEAD and identically after changes — `ContractValidationError: paper execution requires an explicit owner domain` (raised in service_order_entry.py because the test constructs ExecutionScope("paper", ...) without the now-required paper_owner_domain). Failure-name sets before/after diff clean.

## Risks / notes
- `_chain_terminal_leg` records a preview residual on every HEDGE_LEG_SIZED update (current unhedged exposure including not-yet-submitted legs); finish/zero-fill events carry final vectors.
- v3 accept requires version==3 requests (unquoted); v1 accepted as before; v2 requests remain quoted-required (`"new group budget quotes are built by the trusted owner"`).
- No git add/commit/stash was run anywhere; parent repo untouched.

## Resume point (if work continues)
Produce final report; main agent performs staging/commit. If further verification wanted: re-run the exact run table commands above (evidence dir retained).
