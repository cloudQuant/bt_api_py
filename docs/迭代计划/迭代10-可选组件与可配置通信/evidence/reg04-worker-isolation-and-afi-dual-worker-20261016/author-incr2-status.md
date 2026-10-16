# REG04 INCR-2 status (AFI-realization half, test-only round)

Date: 2026-10-16. Agent: REG04-INCR2 round. Cap: 150 tool calls.

## Mission
Create ONE new self-contained test file in AFI repo (D:/source_code/ai-for-investor, branch dev):
suggested `src/backend/tests/test_service_read_reg04_workers.py`, proving on real HTTP→SDK→Execution-Host chain:
1. Two AFI app processes (two JWT subjects, distinct client_session_ids) + manual book + ONE strategy run on ONE
   Execution account owner: workers register/operate without DUPLICATE_ACTIVE_RUN; strategy keeps single-active
   claim (SECOND strategy run still rejected while workers continue); workers stay functional.
2. Exactly-once dispatch per accepted op across the two workers; principal/actor/delegate attribution per intent
   preserved in durable assertions.
3. Manual book attribution stable across workers/user names/sessions (same book, no second book); quota side:
   assert closest honest machinery-visible no-double-count (account-book residuals/reservation facts) or record gap.
4. Ordinary worker operations do not touch group legs (reuse existing snapshot helpers).

Constraints: NO production code changes; no existing AFI file modified; no commits/staging anywhere; do not modify
D:/bt_api_py except reading + this status file.

## Environment (from AFI07 r9, frozen-source discipline)
- Frozen SDK extraction: D:/temp/afi_iter10_approved_target_sdk_sources_20261016_r6_fixed (6 PYTHONPATH roots:
  root, execution/src, base/src, transport/src, monitor/src, gateway/src).
- Interpreter: D:/source_code/ai-for-investor/src/backend/.venv/Scripts/python.exe (r9 harness choice; record).
- cwd: D:/source_code/ai-for-investor/src/backend
- pytest options: --noconftest --import-mode=importlib -p no:cacheprovider
  -c D:/bt_api_py/.codex-tmp/iteration10-approved-target-http-pytest-r3.ini
  --rootdir D:/source_code/ai-for-investor/src/backend -q --tb=short
- Env: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, PYTHONDONTWRITEBYTECODE=1, PYTHONNOUSERSITE=1
- Unset: PYTEST_ADDOPTS, AFI_DUAL_LIVE_PREFLIGHT_ONLY, PYTEST_PLUGINS, PYTEST_CURRENT_TEST, PYTHONHOME,
  PYTHONUSERBASE, PYTHONSTARTUP, PYTHONINSPECT
- Evidence dir: D:/temp/it10reg04-incr2-20261016-r1 (fresh) — per run command/stdout/stderr/exit; >=2 full runs
  (determinism) + a collect-only; sha256 of new file; pins unchanged where applicable.
- r9 runner.py (adapted) at evidence/afi07-remainder-owner-group-legs-20261016/qa/r9/runner.py

## Serialization protocol
Before each pytest/run: `ps -W | grep -i python | grep -v grep`; wait bounded <=120 s if other python runners active;
record waits here. Suites strictly one at a time.

## Resume points
- [step 0] status file created (this). read recon + afi07 status + r9 runner done.
- [step 1] DONE: read AFI machinery — AFI07 nodes 3-5 (lines 7054-8681), manual/approved fixtures,
  cross-process 2-app-process pattern (4727-5568), `_order` base harness, frozen SDK claim machinery
  (`service_registry._claim_strategy_write` role-gated STRATEGY_CLIENT; `service_runtime._scope`
  injects per-runtime run_id; failed 2nd run => ServiceRuntimeError DUPLICATE_ACTIVE_RUN; unregister
  releases claim but run_id row persists). r9 QA runner at
  evidence/afi07-remainder-owner-group-legs-20261016/qa/r9/{runner.py,authored-spec-r9.json}.
- [step 1b] DONE: pins analysis — all 2147 r9 pins still match the working tree; AFI head now
  794eab0c (dev) vs r9's 34f59117 (pins unchanged => later commits didn't touch pinned files).
  Evidence plan: D:/temp/it10reg04-incr2-20261016-r1/{runner.py copy, spec-run1.json, spec-run2.json,
  run1/, run2/, collect-only/, frozen-manifest + sha256 files}. Runner copy from qa/r9/runner.py.
- [step 2] design: NEW FILE src/backend/tests/test_service_read_reg04_workers.py, 2 nodes:
  N1 `test_reg04_dual_worker_processes_and_strategy_run_share_one_owner` (items 1-4 full dual-process
  scenario), N2 `test_reg04_worker_registration_under_live_strategy_claim` (register under active
  claim + 2nd run rejected + post-rejection ops). Reuses _afi07 combined owner host via
  `from tests import test_service_read_authorized_http_integration as _afi07`; app processes spawn via
  `_afi07._start_manual_order_http_app_process` (real uvicorn lifespan); same file-backed sqlite for
  owner + app processes; registry facts read read-only from `afi07-owner-registry.sqlite3`.
- [step 3] DONE: wrote NEW FILE src/backend/tests/test_service_read_reg04_workers.py (2 nodes, fixture
  reuse via `from tests import test_service_read_authorized_http_integration as _afi07`; adapted
  `_reg04_stop_app_servers` because `_afi07._stop_manual_order_http_servers` hardcodes worker count 1).
- [step 4] DONE (2026-10-16): all evidence runs GREEN via r9 runner.py (byte-identical copy, sha256
  8b7f470be77270adeb9d72a83776b827c4653004b39835eeca0f43bf63cf24ed), driver C:/anaconda3/python.exe,
  pytest python = src/backend/.venv/Scripts/python.exe, 6-root frozen PYTHONPATH, 8 env vars unset,
  plugin autoload off, ini iteration10-approved-target-http-pytest-r3.ini, --noconftest/importlib/
  no:cacheprovider, 2148 pins (2147 r9 pins + new-file pin):
  - collect-only/ EXIT 0 "2 tests collected" (final file version).
  - run1/ EXIT 0 "2 passed in 60.86s" — sources_unchanged true, heads_unchanged true (794eab0c).
  - run2/ EXIT 0 "2 passed in 57.86s" — sources_unchanged true, heads_unchanged true.
  - Preserved failed attempts (honest history): collect-only-attempt1-syntaxerror (await outside
    async fn at node2), run1-attempt2-scopeassert (registry session scope_ref is the deployment
    selector 'afi-approved-target-a-v1', not the durable-row expected scope), run1-attempt3-
    approvedcount (approved service calls = 2: submit + replay both reach the owner).
  - FINAL new-file sha256 e8c35811387873affe184462f4f7568eaacde58f277bf2bca578b78e8f87277a;
    all 6 frozen tar hashes re-verified after runs and match source-manifest.json; git status:
    only the new file untracked, nothing staged, HEAD still 794eab0c.
  - Serialization: pre-run ps checks every run; stale Oct-13 pids 128016/132224 persist throughout
    (not pytest runners; left untouched); no new waits needed beyond the one 120 s recorded above.
- Quota-gap note (report this): account-book E/R residual projection is NOT mounted on this owner
  fixture; honest no-double-count assertions = gate.reserved one-per-accepted-op + group budget
  single RESERVED + one manual book scope across sessions/users + exactly-once journal/provider.
