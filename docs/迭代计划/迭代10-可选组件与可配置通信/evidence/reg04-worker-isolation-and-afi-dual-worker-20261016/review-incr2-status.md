# REG04 INCR-2 Review Status (independent adversarial reviewer)

Status: COMPLETE — Verdict: GO (with minor/moderate disclosures)
Completed: 2026-10-16. Reviewer outdir: D:\temp\it10reg04-incr2-review-20261016-r1b
(created fresh; author outdir r1 never written to).

## Checklist results
- [x] Hash-verify test file: sha256 = e8c35811387873affe184462f4f7568eaacde58f277bf2bca578b78e8f87277a
      matches expected exactly; file untracked (?? src/backend/tests/test_service_read_reg04_workers.py).
- [x] Git attribution: no AFI file modified by this round. Tracked mods (README.md, scripts/*)
      all mtime 2026-10-13 (pre-existing); zero diff under src/; nothing staged; HEAD 794eab0c (dev).
- [x] Full read of file (1298 lines) + imported fixture surface (_afi07 app-process spawn, owner
      host, close-tuple contract, durable row readers, journal, group fact helpers; _order._Gate).
- [x] Adversarial judgment (a)-(f): PASS with disclosures (see below).
- [x] Fresh re-run (reviewer spec, own outdir run-rev1, r9 runner.py copy 8b7f470b...):
      exit 0, "2 passed in 57.56s", sources_unchanged + heads_unchanged true, no leftover pids.
- [x] Pin recompute: 2148/2148 same, 0 changed, 0 missing; run1 pins == r9 pins + new-file pin
      (identical to run2 pins). Independent script recheck_pins.py.
- [x] runner.py copy sha 8b7f470be77270adeb9d72a83776b827c4653004b39835eeca0f43bf63cf24ed byte-identical
      to qa/r9/runner.py.
- [x] 6 frozen tar hashes: current tars == tars-before.json == source-manifest.json (all 6 match).
- [x] Red-attempt preservation: collect-only-attempt1 (exit 2 syntax), run1-attempt2 (2 failed),
      run1-attempt3 (1 failed 1 passed) all preserved raw; collect-only-attempt2 is a GREEN early
      collect (exit 0, "2 tests collected") kept as history.
- [x] Gap honesty: disclosed quota gap is real; judgment = adequate for REG04 AFI-side clause.

## Adversarial findings
1. (a) Two app processes genuine: multiprocessing spawn + uvicorn real lifespan of production
   app.main (test_service_read_authorized_http_integration.py:4727-4903); pids distinct != parent;
   close receipts verify pid + worker count 2 each. Real HTTP via httpx; real CURVE SDK; separate
   spawned owner host. NOT in-process classes.
2. (b) Claim real: frozen bt_api_py/service_registry.py:1930-1971 `_claim_strategy_write` on real
   `strategy_write_claims` table, role-gated STRATEGY_CLIENT (1876-1884). 2nd run rejected via real
   service.register reply (service_runtime.py:1604-1616 raises ServiceRuntimeError(code)); sessions
   tuple unchanged => no ghost session. Claims counts: 0 with 4 app sessions pre-strategy; 1 held by
   strategy session under/after worker ops; released (active_registration_id NULL) on close.
3. (c) Exactly-once at durable level: journal reads SqliteExecutionStore file (8 intents, provider
   ids 1-8); 5 durable manual rows for 5 distinct keys (replay + concurrent same-key produce 1 row);
   owner close facts: closed[2] plain-submit == (), closed[3] approved dispatches == 8 ordered,
   closed[4]/[5] empty, closed[6] gate reservations == 8 unique, 11 service calls (7 manual + 2
   approved + 2 strategy) => replays never re-reserve/re-dispatch. Disclosures: closed[3]/[6] are
   in-memory receipts of the spawned owner process over a synthetic provider leaf (provider ids
   numbered by dispatch count, fixture line 7376) and recording order-gate `_order._Gate`
   (test_service_read_order_local_process.py:172); extra-journal-record absence is transitive, not
   scanned directly.
4. (d) Attribution durable: manual/approved rows read back from the SQLite authority DB
   (owner_subject/principal/actor/delegate/book/scope/intent asserted); sessions read from registry
   sqlite; journal durable; owner service-call identity facts captured via sys.settrace on production
   ExecutionOrderEntryService.handle locals.
5. (e) Group legs meaningful: canonical_json full snapshot + revision/state/budget/reservation
   phases/event log compared byte-identical at 3 points (baseline == pre-host == after-worker ==
   owner close tuple closed[7]); journal asserts both group leg intent ids absent. Non-vacuous.
6. (f) No skips/xfails/marks/stubs; single bounded retry sleep(0.1) (line 199); barrier-based
   concurrency; unused `monkeypatch` params at lines 1003/1293 (cosmetic).
7. Gap judgment: "策略/人工 book/旧 ID 映射不产生双份额度" — account-book E/R residual projection
   absent (disclosed). Closest machinery-visible facts asserted: 1 reservation per accepted op,
   single manual book scope across 4 sessions/5 rows, 2nd strategy run rejected while claim held,
   single group budget RESERVED, legs untouched. Adequate for the AFI-side clause; legacy-ID aspect
   covered only via operation-key replay dedup (same intent/provider id, single durable row).
8. Clause coverage (AFI side): worker-vs-single-active-claim = covered (N1+N2); app/service vs
   strategy/manual-book attribution separation = covered; Monitor 转交不扩权 = not in this file
   (proxy-level INCR-1 node 4; AFI realization pending elsewhere); 服务间越权预留拒绝 = not in this
   file (INCR-1 nodes 2-3 host level); no-double-quota = covered with disclosed residual gap.
9. Env caveat: runtimes 57.6-60.9s; ~3 green full runs total across author+reviewer; stale Oct-13
   pids 128016/132224 left untouched; harmless zmq Proactor warning only in failed attempts.

## Verdict
GO — commit the single untracked file as-is. No blockers. Disclosures: quota-projection gap
(disclosed, adequate), synthetic provider leaf + recording order-gate for dispatch/reservation
facts (inherent to approved fixture; journal/manual-book facts are durable and independent).
