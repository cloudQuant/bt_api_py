# DIM02 seam deliverable — independent review status (2026-10-16) — COMPLETE

Reviewer constraints honored: read-only w.r.t. repo (no edits/stage/commit); `D:/temp/it10dim02seam-20261016-r1/`
untouched; outputs only to `D:/temp/it10dim02seam-review-20261016-r1/` and this file. 150 tool-call cap.
Deliverable: `D:/bt_api_py/tests/unit/test_service_market_dim02_seam.py` (NEW, 1129 lines).

## Steps / resume points — ALL DONE
- [DONE 1] Hash: sha256 = 7c86904384a8423d42b2cccdc92984ef185a8e2e9e9df19c705b5612531a0bc6 (matches
  records in `.codex-tmp/dim02-seam-status.md` and evidence `report.md`); 1129 lines; file read fully.
  Status file hash also verified: e47371aa... (matches its claim).
- [DONE 2] Evidence commands extracted: interpreter `C:/anaconda3/python.exe` (3.11.5), cwd /d/bt_api_py,
  PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, PYTHONPATH= bt_api_py + bt_api/{base,
  execution,risk,transport_zmq}/src, `pytest <file> --noconftest --import-mode=importlib
  -p no:cacheprovider --basetemp=... -q`. Evidence run1 exit 0 "4 passed ... 11.94s" (stdout.log OK),
  run2 exit 0 "4 passed ... 12.45s", run3 collect-only exit 0, 4 nodes matching the 4 test functions.
- [DONE 3] Fresh reproduction in `D:/temp/it10dim02seam-review-20261016-r1/`: 3 full runs, all exit 0
  "4 passed" (12.22s / 12.61s / 12.43s), stderr empty each run, hash identical before/after every run,
  no stray python child processes after every run (only the pre-existing uvicorn PIDs 62480/66688).
- [DONE 4] git status: deliverable present only as `??  tests/unit/test_service_market_dim02_seam.py`
  (+ `.codex-tmp/` scratch). Seam file mtime unchanged (12:17:33). No existing project file edited for
  this deliverable. Other in-flight items listed (not touched): ` M bt_api/bt_api_execution`,
  ` M bt_api/bt_api_risk`, ` M tests/unit/test_load01_hundred_strategy_30min.py` (mtime 12:25:01, landed
  mid-review from another agent line), `?? bt_api_py/service_market_legs.py`,
  `?? tests/unit/test_service_market_dim02_integration.py`, `?? tests/unit/test_service_market_dim02_legs.py`,
  `?? tests/unit/test_service_market_legs.py`, stray `?? 0`, `?? %SystemDrive%/`. No cache/test residue
  from runs (no:cacheprovider + DONTWRITEBYTECODE; pre-existing ignored __pycache__ prior to runs).
- [DONE 5] Assertion-quality assessment (see below). [DONE 6] Verdict: **ADOPT**.

## Serialization log (probe before every run; no waits needed)
- run1 probe 12:23:58: only PIDs 62480/66688 (uvicorn, since Oct 13) -> no wait.
- run2 probe: only PIDs 62480/66688 -> no wait.
- run3 probe 12:25:12: only PIDs 62480/66688 -> no wait. Runs strictly one at a time.
- Post-run probe after each run: no pytest/child python processes remained.

## Reproduction evidence table
| run | dir | exit | result | stderr | hash pre==post | strays |
|---|---|---|---|---|---|---|
| r1 | review/r1 | 0 | 4 passed in 12.22s | empty | yes (7c86..a0bc6) | none |
| r2 | review/r2 | 0 | 4 passed in 12.61s | empty | yes | none |
| r3 | review/r3 | 0 | 4 passed in 12.43s | empty | yes | none |

## Assertion-quality assessment (file read line-by-line)
Claimed semantics ARE asserted, not merely exercised:
- Seam pagination: full page equality vs independently constructed provider expectation
  (page1 complete=False + continuation T2; page2 complete=True + next=None exhausted at seam) (l.911-919);
  last history close == live open == T3, all closes <= live open, pairwise `_overlaps` == none (l.940-949).
- Source-time passthrough: `sent_at == _T4` exact AND != wall-clock now (l.936-937, l.905 warmups).
- History reads never move live cursor: cursor tuple re-asserted after every read (l.914, 920, 1011, 1036, 1050).
- Clean release: starts/stops == 1/1 + active_upstreams 1->0 + host close ("closed","closed",1,1) +
  graceful join exitcode 0; `_assert_no_fallback_cleanup` fails the test if terminate/kill was needed.
- No fabrication: idle-poll (empty), cursor frozen, empty history page bars==[] complete=True next=None
  observed_at == provider value (l.988-1006).
- Correction/statelessness: second-read page equals provider-corrected page; first-read bars re-asserted
  unmutated post-read (l.1048); provider fetch called exactly twice with exact 5.0s timeouts (l.1053-54).
- Verbatim forwarding asserted as OBSERVED behavior: both overlaps delivered, sequences [3,4] in order,
  exact payload/time equality with the source, duplicate == history B2 and conflict != history B1 (l.1097-1112).
- Fail-closed regression sensitivity: `pytest.raises(_ProjectionConflict)` + message content +
  projection length stays 3 + `projection.bars[_T1]` still the original B1 (l.1121-1125). Any regression to
  silent replace / double count / partial apply fails these; any SDK-side dedup/replace/drop fails the
  forwarding assertions. (Caveat by design: the projection is test-local, documented in the module docstring;
  production consumer proof is 07B, not re-run here.)
Determinism hazards scoped: no fixed ports (bind(0) free-endpoint pattern, small TOCTOU race, same as the
accepted GW01 fixture); two `time.sleep(0.4)` are SUB-join warm-ups only — correctness rests on bounded
`wait-events` polling (15s) and every receive/join is time-bounded with forced-cleanup assertions; the
anti-wall-clock check is date-stable. No lingering processes across 3 runs.

## Verdict: ADOPT
Both required fresh runs (plus a third) reproduce exit 0 / 4 passed; hash matches records; file untouched
by runs; no existing project files edited; claimed semantics genuinely asserted with good regression
sensitivity. Minor notes (non-blocking): silent-key "no head advance/freshness" is inferred from zero
events + frozen cursor rather than an explicit head/liveness query; status-file narrative says "gapless"
vs actual node name "..._is_gap_free_...".
