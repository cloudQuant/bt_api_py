# DIM02 legs increment-3 — independent review status (COMPLETE)

Reviewer: independent review agent (read-only w.r.t. repo). 2026-10-16.
Output dir: D:/temp/it10dim02legs-review-20261016-r1

## Verdict: ADOPT (no defects found)

## Evidence summary
1. Hashes (all 3 match status.md + report.md; identical before/after my runs):
   - bt_api_py/service_market_legs.py  1d6456c2...1edf2 (240 lines)
   - tests/unit/test_service_market_legs.py  d99776ab...c53b (339)
   - tests/unit/test_service_market_dim02_legs.py  a9de557c...a4b4 (703)
2. Commands extracted from evidence (unit2/cmd.txt, spawn3/cmd.txt):
   cwd=/d/bt_api_py; C:/anaconda3/python.exe (3.11.5); PYTHONDONTWRITEBYTECODE=1;
   PYTHONPATH="D:/bt_api_py;<base|execution|risk|transport_zmq>/src";
   pytest --noconftest --import-mode=importlib -p no:cacheprovider -q.
   bt_api_gateway resolves to local bt_api/bt_api_gateway/src (editable install) — real chain OK.
3. Fresh reproduction (serialization checked before each run; only 2 long-lived Oct-13 daemons active):
   - unit: exit 0, "9 passed, 1 warning in 0.83s", stderr empty.
   - spawn: exit 0, "1 passed, 1 warning in 3.19s", stderr empty.
   - ps -W after: no stray python children. Warning = pre-existing pytest config notice.
4. git status --porcelain: deliverable files untracked-only; other entries (.codex-tmp/, %SystemDrive%/,
   "0", sibling dim02_integration/seam tests, modified gitlinks execution/risk) belong to other lines.
   Gateway + base submodule worktrees CLEAN -> "no server edits" holds. market.head is a pre-existing op
   (market_hub.py:58/968).
5. Assertion quality: spawn asserts exact leg tuples (stream,head,observed,age,no_data,ready) over 6
   transitions incl. silent->stale-blocks->catch-up->expiry; unit covers both silence forms, offset
   equivalence, negative age, fail-closed (cause_code), strict reply validation. Mutation kills:
   - mutantA (silent counted fresh): spawn FAILS (exit 1) -> test detects it.
   - mutantB (stale never blocks): spawn FAILS (exit 1) -> test detects it.
   (mutant overlays in this dir are throwaway package copies; repo untouched, -P used to force order.)
6. spawn1 failure (recorded): assert active_registrations == 1 vs actual 2. Hub semantics verified:
   readiness sums len(stream.registrations); subscribe adds registration_id per stream/key
   (market_hub.py:447) -> 1 registration x 2 keys = 2 refs. Test-side expectation bug CONFIRMED;
   unrelated to the reviewed module. Current test correctly asserts ==2 with comment.
7. ruff 0.16.2: check exit 0 "All checks passed!"; format --check exit 0 "3 files already formatted".

## Caveats
- spawn1 pre-fix revision not preserved; fix validated via hub counting semantics, not diff.
- _free_endpoints has small probe-bind TOCTOU (standard); no sleeps/waits in test paths.
- Module silently treats source-ahead (negative age) as ready; spec-conformant per claim; no client
  health gating (documented out of scope).
- Sibling DIM02 test files (other agents) not reviewed.

## Run log
- All runs: ps -W checked; no waits needed (no active runners observed at any point).
- review runs: unit exit 0 / spawn exit 0; mutantA exit 1; mutantB exit 1 (expected).
