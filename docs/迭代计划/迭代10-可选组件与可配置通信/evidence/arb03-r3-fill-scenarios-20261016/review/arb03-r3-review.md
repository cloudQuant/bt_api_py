# ARB03 R3 adversarial review — first-leg zero/full fill + hedge partial cancel

Reviewer: independent adversarial pass, 2026-10-16 (Beijing), window 14:05-14:12.
Producer artifacts reviewed read-only; no edits to tracked files, no git add/commit/stage performed by this review.
My writes only: this file + `D:\temp\it10arb03-r3-review-20261016-r1\`.

## Verdict: GO-with-disclosures

All three producer claims verified against the test source, production code, and formal evidence; none falsified.
The increment is safe to commit as a dev increment (single test file), with the disclosure rows below unchanged
(synthetic-provider evidence only; ARB03 acceptance case remains **部分**). One cosmetic doc discrepancy noted
(claimed 2358 lines vs actual 2357; sha/bytes match). Commit ordering note: MIG01-R3 staged its files into the
index during this review window (root HEAD still e137193d, uncommitted) — commit only the ARB03 test file, and
treat e137193d-or-newer as the parent.

## Repo state at review time

- `git rev-parse HEAD` -> `e137193d17ced6e065567e8ab1f3ae12edf6b020` (unchanged from producer record; not advanced to MIG01-R3 commit yet).
- `git status --short`: ARB03 test file `M` (producer change); MIG01-R3 files now **staged** (`M bt_api_py/_direct_backend.py`, `A bt_api_py/_gateway_wire.py`, `M bt_api_py/bt_api.py`, contract tests, mig01-r3/mig03 evidence docs); ARB02 test `M`, LOAD01 test `M`, DIM08 submodule untracked content; `?? .codex-tmp/`, `?? %SystemDrive%/`, `?? 0`. All other-workstream dirt per mission; untouched by me.

## Check 1 — the three nodes pin the claimed semantics (file `tests/integration/test_arb03_dim05_group_partial_fill_service.py`)

Diff: `git diff --numstat` = **497/0**; hunks `@@ -49,6 +49,7 @@` (import), `@@ -131,6 +132,10 @@` (3 constants), `@@ -1858,3 +1863,495 @@` (3 nodes at EOF). No line modified; `grep '^-'` deletions only the diff header. Assert counts per node match producer claim exactly (59/78/69).

Node 1 `test_arb03_first_leg_zero_fill_closes_group_without_hedge` (L1868-2017):
- `snapshot.state is GroupOrderState.CLOSED`, `error_code is None`, `residual_base_units == Decimal("0")` (L1903-1905).
- First leg REJECTED/0 fill/`reason_code=="provider_rejected"`, resolution `TERMINAL_NO_FILL_RESOLVED`, `dispatch_latch_active is False` (L1918-1933).
- No hedge: `leg_b.submitted_intent/child_status/claim_receipt/resolution_receipt is None` (L1936-1939); account-b provider writes 0 incl. counters 0/0 (L2017); risk vector `("leg-b", False, False)` (L2011-2013).
- Reservations: both `phase is GroupReservationPhase.RELEASED` with `(leg-a, True, True)` / `(leg-b, False, False)` and `RESERVATION_RELEASED` x2 covering both accounts (L1943-1950, L1978-1980).
- Events exactly-once: `GROUP_CLOSED_NO_EXPOSURE` x1 with `first_leg_id=="leg-a"`, `reason=="terminal_no_fill"`; no `HEDGE_LEG_SIZED`/`GROUP_PAIRED`/`GROUP_RECOVERY_REQUIRED` (L1963-1969); persisted snapshot round-trip `persisted == snapshot` (L1959).
- Execution store: `dispatch_attempts == 1`, outbox tuple pinned exactly `(intent_recorded, intent_admitted, dispatch_claimed, provider_observation)` with rejection reason (L1993-2004).

Node 2 `test_arb03_first_leg_full_fill_sizes_hedge_exactly_once` (L2020-2188):
- `PAIRED`, error None, residual 0 (L2053-2055). Hedge resized: `leg_b.submitted_intent.quantity == Decimal("4.000")` and `!= Decimal("6")` (L2088-2089); `HEDGE_LEG_SIZED` x1 payload `"4.000"`/`"2"` (L2119-2122).
- Exactly-once: `GROUP_PAIRED` count == 1 (L2117), no recovery (L2118); per-leg `dispatch_attempts == 1`, exact 4-event outbox each, `_assert_provider_writes(...,1)` per account => provider-side writes==1, counters `{order_queries:1, fill_queries:1}` (L2141, L2145-2152, L2165-2176, L2188).
- `GROUP_PAIRED` payload `2`/`4.000`/`0` (L2124-2126); both risk legs `(True, True)` + holds resolved (L2180-2187).

Node 3 `test_arb03_hedge_leg_partial_cancel_requires_recovery` (L2191-2357):
- `RECOVERY_REQUIRED`, `error_code=="hedge_residual_unresolved"`, `residual_base_units == Decimal("1")` (L2224-2226). Hedge sized `3.000` from requested 4 (`HEDGE_LEG_SIZED` payload `"3.000"`, L2249, L2282-2284).
- Dual-observation chain: outbox exactly `(..., provider_observation, provider_observation)` with PARTIALLY_FILLED then CANCELLED and equal payloads, per leg (L2307-2318, L2334-2345) — production-enforced by `_assert_current_execution_partial` (`bt_api_py/service_execution_group_authority.py:381`, called at 1621; `CANCELLED_PARTIAL` gate in `service_execution_group_proofs.py:1845/1961-1968`).
- No oversell: `dispatch_attempts == 1` both, provider writes 1 + counters 1/1 both (L2304, L2331, L2355-2357); hedge filled 2 < sized 3; risk legs `(True, True)` + holds resolved.

No assertion found that could pass trivially on absent behavior: zeros/no-events are asserted with explicit `not any(...)`/count==0, positives with exact counts and exact outbox tuples. Counter provenance: `dispatch_attempts`/outbox/group events are read back from production-persisted SQLite stores (production self-report), but they are cross-validated by provider-side DB observations (`provider_writes`/`provider_counters` read directly from the synthetic provider ledger via `_provider_snapshot`, L1128-1142; `make_order` persists one write per submission; `query_order`/`get_deals` increment counters — `tests/unit/_service_group_provider_fixtures.py:97-200`) and by risk-gate DB snapshots (`_risk_legs`, `_assert_holds_resolved` incl. `pending_holds() == ()`). No counter lives in test-local memory. Two minor assertion-looseness points (not falsifications): node 3 asserts recovery event with `any(...)` rather than exactly-once (snapshot state/error/residual still pin it exactly); `Decimal("4.000") == Decimal("4")` is scale-insensitive, but scale is pinned by the `HEDGE_LEG_SIZED` payload string — confirmed by the producer's smoke-1 failure/fix.

## Check 2 — zero-fill nuance (verified, cite accurate)

Gate confirmed at `bt_api_py/service_execution_group_proofs.py:2291-2298`: any CANCELLED with `not (Decimal("0") < facts["filled_quantity"] < facts["quantity"])` enters `_mark_unknown("GROUP_ORDER_NOT_TERMINAL")` + `GroupProofSourceError("GROUP_ORDER_OUTCOME_UNKNOWN")` (L2299-2301). I.e., zero-fill CANCELLED is UNKNOWN, so REJECTED is the accurately expressible zero-fill terminal form. Coordinator closes it via `_close_no_fill` (`bt_api/bt_api_execution/src/bt_api_execution/group_coordinator.py:695-697` for `status.filled_quantity == 0`; `_close_no_fill` at 1053-1067 emits `GROUP_CLOSED_NO_EXPOSURE` payload `{"first_leg_id", "reason": "terminal_no_fill"}`, CLOSED, residual 0, error None). Hedge sizing math confirmed at 709-745 (base = filled x base_units_per_order_unit; step quantization 0.001 -> "4.000"/"3.000") and `_finish_hedge` at 785-815 (residual = first_base - second_base -> PAIRED vs RECOVERY_REQUIRED/`hedge_residual_unresolved`; payload format matches). Test metadata: base_units_per_order_unit account-a=2 / account-b=1 (L1889-1891, 2038-2041, 2209-2212) -> base 4=2x2 (node 2), 3=1.5x2 and residual 1=3-2 (node 3). Characterization accurate.

## Check 3 — formal evidence integrity (all verified)

Dir `D:\temp\it10arb03-r3-20261016-r1`:
- `exit.txt` = 0; junit `tests=5 failures=0 errors=0 skipped=0 time=47.180` (5 named nodes); stdout `5 passed, 1 warning in 47.48s`; stderr empty.
- `stdout.log` sha256 = `54ad70d09d1aec...9741`, `stderr.log` = `e3b0c442...` (empty), `result.json` sha256 = `e111cdd3a3de...f51f` — all match producer claims.
- `spec.json` sha256 = `5cef0e41ca80...cb31`; byte-equal to `.codex-tmp/it10arb03-r3-20261016-r1-qa/focused-qa.json` (same sha, 53645 B). Spec nodes = the 5 nodes; env = the documented recipe; python `C:\anaconda3\python.exe`; cwd `D:\bt_api_py`.
- Spec pins ARB03 file at `2f06291dbed35bc56f54d6e502c001fd3316f9408cf4a0fab615acaff03a033e` == current file sha; 353 sources; `sources_unchanged=true`; `heads_unchanged=true`; heads match producer pins (root e137193d; base b4aabd1e; execution f5c03064; risk adb6ec58; gateway 59b164ab; transport a397fdb4) and still match live submodule HEADs.
- Stronger re-check: hashed all 353 pinned sources against the current working tree now -> **0 mismatches** (survives MIG01-R3 index staging, which changed no file bytes).

## Check 4 — only the one test file touched

- ARB03 diff confined to `tests/integration/test_arb03_dim05_group_partial_fill_service.py`, +497/-0; new group IDs/`_GROUP_ARB03_*` constants and R3 node names appear nowhere else (`grep -rn` over `bt_api_py/ bt_api/ tests/` -> no hits).
- Other modified/untracked files match the documented other-workstream list (MIG01-R3 now staged, ARB02, LOAD01, DIM08, contract tests); MIG01 diffs (`_direct_backend.py`, `bt_api.py`) contain no group/ARB03 references. mtimes of those files (13:50-13:55) fall in the shared window and cannot be authorship-attributed from the filesystem alone; per mission they are other workstreams' dirt and were left untouched. No commits were made this round (HEAD unchanged).

## Check 5 — independent re-run (my evidence: `D:\temp\it10arb03-r3-review-20261016-r1\`)

- Pre-run snapshot: PIDs 128016/132224 (services, untouched), 13961 (other agent drill, 13:44:48), plus other agents' transient short-suite PIDs 16300/126104/142108; no kill; one suite at a time for me.
- Command (exact formal recipe): `PYTHONPATH='D:\bt_api_py;...bt_api_base\src;...bt_api_execution\src;...bt_api_risk\src;...bt_api_gateway\src;...bt_api_transport_zmq\src' PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 C:/anaconda3/python.exe -m pytest -q --no-header -p no:cacheprovider --junitxml=...\junit.xml --basetemp=...\pytest-tmp tests/integration/test_arb03_dim05_group_partial_fill_service.py`
- Result: **exit 0; `5 passed, 1 warning in 49.93s`; junit tests=5 failures=0 errors=0 skipped=0 time=49.662** incl. all 3 new nodes. stdout sha256 `a669cdc6e584...a9be`; stderr empty. Post-run process snapshot: no orphans from my run (services + other agents' drill only). Run window 14:06:32->14:07:22, before 15:00.

## Falsified claims

None. Discrepancies found (non-blocking):
1. Producer report/status say the file is "2358 lines"; the file is 2357 newline-terminated lines / 97160 B (sha matches the stated value; `git diff` +497 consistent). Cosmetic documentation off-by-one.
2. Node 3 recovery-event assertion uses `any(...)` (not exactly-once); semantics still pinned by snapshot state/error/residual assertions.
3. `dispatch_attempts`/outbox/group events are production-side self-reports read from persisted stores; acceptable because independently cross-checked against provider-ledger writes/counters and risk-gate vectors/holds.

## Residual risks (carry forward; producer-disclosed, confirmed)

- Synthetic provider only (fixture-injected fills, no venue/fee/slippage reality); not a real-account ARB03 PASS — clause stays 部分.
- Cross-combination fills not covered: first-leg FULL + hedge REJECTED and first-leg FULL + hedge partial-cancel still missing; zero-fill CANCELLED is production-gated UNKNOWN and thus not expressible as a scenario.
- D2 caveat stands: PROOF_READY ordering is production-enforced, not asserted at test level.
- Parent may advance by the MIG01-R3 commit before the ARB03 test file is committed; treat e137193d-or-newer as acceptable parent, ARB03 file must stay byte-identical (`2f06291d...`) through commit.
