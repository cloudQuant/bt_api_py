# ARB02 crash-before-first-leg increment — independent adversarial review

Reviewer round: 2026-10-16 ~14:04-14:12 Beijing. No tracked file edited, no git add/commit/stage,
no process killed. Review evidence: `D:\temp\it10arb02-crashprefirstleg-review-20261016-r1\`
(stdout.log / stderr.log / exit.txt / junit.xml, hashes below).
Producer evidence reviewed: `D:\temp\it10arb02-crashprefirstleg-20261016-r1\` + status/report md.

## VERDICT: GO-with-disclosures

The single new node is verified semantically sound, evidence integrity holds, and an independent
whole-file re-run reproduced 5 passed / exit 0 on a byte-identical source set. Safe to commit as a
dev increment. Disclosures and residual risks below (ARB02 case stays 部分 either way).

## Check 1 — Node semantics (read end-to-end, lines 2642-3124)

Node `test_all_reservations_submitted_crash_before_first_leg_no_oversell` in
`D:\bt_api_py\tests\integration\test_arb02_group_budget_service.py` matches the gap item:

- All reservations submitted: waits `reservation_committed["account-a"/.wait(5)`,
  `reservation_committed["account-b"].wait(5)`, `reservation_reply_entered.wait(5)`.
  Barrier is real: `complete_and_observe` (line 801) wraps `accepted_store.complete_reservation`
  and fires only AFTER the real durable accepted-store transaction; then B's reply is withheld.
- Real process kill, not a flag: `_terminate_test_coordinator` (line 1616) does
  `process.terminate(); process.join(15)` then `assert type(exitcode) is int and exitcode != 0`
  on the spawned child (`context.Process`, spawn context, `_start_coordinator` line 1537).
  Pre-kill `assert not provider_entered.is_set(), "an undispatched group reached a provider"`
  (line 2808); the kill strictly precedes the reply release: `reservation_reply_release.set()`
  only after `_terminate_test_coordinator(...)` returns (lines 2815-2821).
- No TTL: no `_wait_for_deadline` call in the node; lifetime 120s; `assert request["deadline_ns"] >
  time.time_ns()` asserted BOTH before crash (line 2809) and before replay (line 2910).
- Reopen pre-dispatch state (lines 2873-2904): `state is GroupOrderState.PREPARING`,
  `error_code is None`, budget `GroupBudgetReservationState.RESERVED` gross `Decimal("200")`,
  phases `{account-a: ACTIVE, account-b: REQUESTING}`, all legs `not claimed and not resolved`,
  `submitted_intent is None and child_status is None`; both account hosts report
  `("status", (), {"order_queries": 0, "fill_queries": 0})` (real read of provider ledger +
  counters, same store the dispatch writes to). Client lost reply asserted exactly:
  `("call_error", "submit", <group>, "RequestOutcomeUnknownError", None)`.
- Exact replay continues to PAIRED: same `request` object resubmitted; `PAIRED`,
  `error_code is None`, `residual_base_units == 0`, `provider_entered.is_set()`; budget still HELD
  gross 200 with SAME quote/policy fingerprints; both legs FILLED + claim_receipt +
  resolution `TERMINAL_EXPOSURE_RESOLVED` / trade_count 1 / `dispatch_latch_active is False`;
  `recovered_query_snapshot == recovered_snapshot` (pure query == replay receipt).
- Exactly one RESERVED->HELD transition: `assert budget_transitions ==
  [{"from": "RESERVED", "to": "HELD"}]` (line 3058). Nuance: the group budget is a single
  portfolio-level reservation ($200 across both legs), so "one hold" = one group-level transition;
  per-account single-claim evidence is the Risk vector (`claimed is True, resolved is True` per
  account), `record.dispatch_attempts == 1`, and provider writes per account. This mapping is the
  same one disclosed by the producer; consistent with production semantics in the pinned
  `group_store.py`/`group_coordinator.py`.
- One dispatch per leg / one write per account / no stranded holds: per account
  `len(writes) == 1 and len(set(writes)) == 1`, `counters == {"order_queries": 1,
  "fill_queries": 1}`, execution record `state is FILLED` + `dispatch_attempts == 1`,
  `authority.pending_holds() == ()`.
- No reject/recovery/deadline events: event log counts `FIRST_LEG_SUBMIT_STARTED == 1`,
  `HEDGE_LEG_SUBMIT_STARTED == 1`, `GROUP_PAIRED == 1` and none of
  `GROUP_REJECTED_BEFORE_DISPATCH` / `GROUP_RECOVERY_REQUIRED` / `GROUP_DEADLINE_ELAPSED`.
- Exactly 2 reserve RPCs: telemetry wrapper at the real account-owner adapter seam
  (`admission.reserve_group_vector`, line 791) queues events; test drains exactly 2 (one per
  account) and then `pytest.raises(queue.Empty): reservation_events.get(timeout=0.1)` —
  recovery re-reserving would fail this.
- Bounded waits sane: 5s event waits, 15s kill join, 20s `_receive` default, 20s call-outcome,
  30s `_next_client_message` default; all wait calls carry timeouts. Observed runtime ~30-35s.
- Not trivially passable: pre-crash zero-dispatch assert + pre-recovery zero-effect status checks
  on both accounts + post-recovery positive asserts (`provider_entered.is_set()`, FILLED,
  dispatch_attempts==1, provider write/counters==1) + replay-rejection event absence + exact
  RPC count. A sim-flag crash or duplicated dispatch cannot satisfy all of these together.

## Check 2 — Evidence integrity (cross-checked)

- Producer junit.xml: `tests="5" errors="0" failures="0" skipped="0" time="89.122"`, includes
  `test_all_reservations_submitted_crash_before_first_leg_no_oversell time="34.833"`.
  stdout.log tail: `5 passed, 1 warning in 89.54s (0:01:29)`; exit.txt `0`; result.json:
  `pytest_exit=0, source_count=248, sources_unchanged=true, heads_unchanged=true`.
- Pin file (source-before.json) = 248 entries and source-before == source-after byte-identical
  (checked with Python). Composition: 170 bt_api_py + 26 bt_api_execution + 22 bt_api_risk +
  12 gateway + 7 zmq + 6 base + 5 tests sources, including the load-bearing
  `bt_api/bt_api_execution/src/bt_api_execution/group_coordinator.py` and `group_store.py`,
  and `bt_api_py/service_execution_group*.py`.
- Heads cross-checked vs git AT REVIEW TIME (before the MIG01 commit landed):
  parent `git log --oneline -1` = `e137193d` (MIG03 incr-B); `git -C bt_api/bt_api_base rev-parse
  HEAD` = `b4aabd1e17b9...`; gateway = `59b164ab7f89...`; zmq = `a397fdb460d2...` — all exactly
  as claimed.
- Pin sweep against the working tree: 0 mismatches before my re-run, 0 after, and 0 again after
  the MIG01 R3 commit landed mid-review (see disclosure D1). The arb02 test file hash
  `639d4d847f3d8f61e2e2c4c46d0d1058d1c38ab4314bc4ed354ba93754af5391` equals the producer pin now.
- Runner `assert not out.exists()` + all artifact mtimes 14:00-14:01 + `running.json` pid/timestamp
  ~5ms gap to command.json = single clean r1 run; consistent with the producer's preflight-hiccup
  account (aborted invocation left no artifacts, otherwise this run would have refused to start).

## Check 3 — Only the pinned test file was touched

- `git diff --stat tests/integration/test_arb02_group_budget_service.py` → `1 file changed,
  488 insertions(+)` (0 deletions). Diff content = 2 one-line additions outside the node
  (`GroupReservationPhase` import + `_GROUP_CRASH_BEFORE_FIRST_LEG` constant) + the node block.
- No production file diff attributable to this increment: group coordinator/store/service files
  are clean in `git status`; the other dirty/untracked paths at review time belonged to other
  workstreams: MIG01-R3 (bt_api_py/_direct_backend.py, bt_api_py/bt_api.py, bt_api_py/_gateway_wire.py,
  tests/bt_api_contract/test_execution_session.py + test_gateway_runtime_unknown_execution.py —
  committed by that workstream as `ee3b05c2`, which does NOT include the arb02 file;
  test_venue_order_type_tif_matrix.py still untracked), ARB03 (+497 on test_arb03...), LOAD01,
  bt_api/bt_api_monitor & bt_api/bt_api_risk submodule untracked content. All left untouched by
  this reviewer. Other workstreams stayed active during the review: a new untracked
  `bt_api_py/service_migration_map.py` appeared ~14:10 (MIG03; not in the 248-pin set, not
  referenced by the arb02 node — no effect on this verdict).

## Check 4 — Independent re-run (REQUIRED) — PASS

- Command (cwd `D:\bt_api_py`, exact producer env recipe):
  `PYTHONPATH='D:/bt_api_py;...bt_api_base/src;...bt_api_gateway/src;...bt_api_transport_zmq/src'
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
  C:/anaconda3/python.exe -m pytest -q --no-header -p no:cacheprovider
  --junitxml=D:/temp/it10arb02-crashprefirstleg-review-20261016-r1/junit.xml
  --basetemp=D:/temp/it10arb02-crashprefirstleg-review-20261016-r1/pytest-tmp
  tests/integration/test_arb02_group_budget_service.py`
- Started 14:05:54, ended 14:07:18 (84s wall). `PYTEST_EXIT=0`.
- My junit: `tests=5 failures=0 errors=0 skipped=0 time=83.359`; new node `30.359s`
  (producer 34.833s — normal variance); stdout: `5 passed, 1 warning in 83.76s (0:01:23)`;
  stderr empty; exit.txt `0`.
- Artifact hashes (sha256): stdout.log `7c6667eb...f245d2de1`; stderr.log
  `e3b0c442...b7852b855` (empty); exit.txt `9a271f2a...3ab86aa`; junit.xml `d9512f9d...7a8739`.
- Post-run pin sweep: 0/248 mismatches (same pins as producer), arb02 on-disk == pin.
  My run therefore executed the exact pinned source bytes.

## Check 5 — Serialization / process discipline

- Pre-run ps (14:05:32): residents PID 128016 (ai-for-investor venv), 132224 (Python312),
  13961 `/c/anaconda3/python` started 13:44:48 = SCN02 R6 drill; transient 16132/16184
  (anaconda python, started 14:05:09/14:05:21) had exited on their own by 14:07 — untouched.
- Post-run ps (14:08:29): only the 3 residents; none of my children left behind; NO process
  killed at any point.
- Window: pytest 14:05:54-14:07:18, all review work well before the 15:00 Beijing gate
  (LOAD01 15:07-15:45 untouched). One pytest at a time from this reviewer; only overlap was the
  SCN02 drill (synthetic-provider test, no SimNow, distinct stores; contention-only risk).

## Falsified producer claims

None. All cross-checked numbers matched (junit 5/0/0/0, 34.833s node, 89.54s stdout, 248 pins,
exit 0, head values e137193d/b4aabd1e/59b164ab/a397fdb4). Only normal timing variance and the
anticipated HEAD movement (D1) were observed.

## Disclosures / residual risks

- D1 (anticipated): MIG01-R3 was committed by its own workstream during this review, moving parent
  HEAD e137193d -> `ee3b05c2` (the producer round ran at e137193d, as its result.json proves).
  The commit is git-state-only for this evidence: all 248 pins still match post-commit, and
  `ee3b05c2` does not include the arb02 test file. Commit-time parent will be `ee3b05c2`.
- D2: the runner gated HEAD only for parent/base/gateway/zmq; `bt_api_execution` (f5c030644dea)
  and `bt_api_risk` (adb6ec583081) HEADs are not in the gate, but their 26/22 source files ARE in
  the 248 pins and were byte-stable across both runs.
- D3: "one budget hold" maps to ONE portfolio-level RESERVED->HELD group transition (single $200
  group budget), with per-account single-claim/resolution + dispatch_attempts==1 + one provider
  write as the per-account evidence; acknowledged adaptation of the gap wording.
- D4: sensitive commit scope: stage ONLY `tests/integration/test_arb02_group_budget_service.py`
  (blob must hash to `639d4d84...5391`). ARB03 (+497), LOAD01 and other dirt belong to other
  workstreams and must not be swept into this commit.
- R1: synthetic providers only; no real account/SimNow behavior proven.
- R2: account-level Risk reservation rejection -> zero first-leg dispatch NOT built (ARB02 stays
  部分); also no same-instant SQLite transaction-competition coverage; crash covers coordinator
  only; reserve-RPC counting is adapter-seam telemetry, not wire-level capture.
- R3: optional belt-and-braces: a re-run at `ee3b05c2` (~85s) would remove D1 from the record;
  not required since pin bytes are proven unchanged.

## Commit recommendation

Safe to commit the one test file as a dev increment (test-side only, +488/-0, pinned blob
`639d4d847f3d8f61e2e2c4c46d0d1058d1c38ab4314bc4ed354ba93754af5391`), parent `ee3b05c2`.
