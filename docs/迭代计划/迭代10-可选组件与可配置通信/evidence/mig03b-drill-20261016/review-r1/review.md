# MIG03b increment-B — independent review status

- Status: REVIEW COMPLETE (READ-ONLY; no file edited except this report; no pytest run; no git writes; no processes killed)
- Date: 2026-10-16
- Reviewer scope: tests/unit/test_service_migration_drill.py (untracked), D:/temp/it10mig03b-20261016-r1 evidence, .codex-tmp/mig03b-status.md + mig03b-report.md
- Verdict: **GO-with-disclosures** (zero contradicted claims; disclosures below are recording-granularity notes only)

## Checklist results

### 1. Test-file integrity — VERIFIED

- `sha256sum tests/unit/test_service_migration_drill.py` → `f5cb66102575abe22217d6caba7ea952552fafe6875c9caa7f0e000af2c91d8f` == pinned expected; `stat` → 87816 bytes; 2162 lines.
- Read ALL 2162 lines in three chunks. Confirmed two nodes: line 1250 `test_two_epoch_controlled_migration_drill_preserves_facts_and_refuses_old_epoch_after_first_new_write` (success path) and line 1911 `test_controlled_migration_rollback_window_and_import_failure_negatives` (rollback/import-failure negative).
- Genuinely real-process/real-SQLite: `multiprocessing.get_context("spawn")` spawns real ServiceHost children (`_host_process`, labels host-a/b/b2, old-deployment-refused); real ZMQ CURVE RPC via ServiceClientRuntime/ZmqTransportFactory; real SQLite stores for registry, execution, risk gate, provider journal, gateway journal. UNKNOWN created by `_DurableTimeoutProvider` dispatching then losing the reply.
- `grep skip|xfail|monkeypatch|pytest.mark|@pytest` → no matches (no weakening). `grep "def test_"` → exactly the two nodes. No dead/duplicated bodies; test 2 has distinct rollback-window flow. Assertions are substantive (exact durable-tuple equality, counts {1,0}, distinct epochs, marker byte compares, REVERSE_MIGRATION_REFUSED / ORDER_OWNER_ALREADY_ACTIVE / SCOPE_DENIED / INTENT_CONFLICT / STREAM_EPOCH_* negatives) — none trivially true. The only runtime rebinding is `service_host_module.ServiceRegistry = functools.partial(...)` inside spawned children to pin lease/clock — legitimate test clock control, restored in-child; `_assert_fixed_source_origins` re-asserts the source file of 9 symbols via `inspect.getsourcefile` (import-shadowing guard).

### 2. Pins — VERIFIED

- Live recompute: service_migration_gate.py `70700cd648da85d9dcca4ff0e55022cd44f2f5e12090a0734a852b5f3f0cc39d` (16721 B), service_host.py `07e95f92b5782dd18412b0397ab1c50e3ecf8482c720392314d93f4cbf9d0be1` (66489 B), tests/unit/test_service_migration_gate.py `afaca97a071d5f7069203d3a2f4aaa907c2d71607bd440d76b2cf3b58eb263cb` (17406 B) — all equal the pinned values. Zero production changes.
- `sha256sum pins-before.txt pins-after.txt` → both `0a4d062ff85009430f8d36aa9455663f92cc38c0d1ce1d8e93980861ccde228f`; `cmp` → byte-identical. pins-before.txt contains exactly the 4 pinned rows (drill test hash inside matches live).
- `git submodule status` for imported submodules: bt_api_base b4aabd1e, bt_api_execution f5c03064 (heads/dev), bt_api_gateway 59b164ab (heads/dev), bt_api_risk adb6ec58 — no `+` (in sync with index).

### 3. Evidence integrity — VERIFIED (with one artifact-set note)

- run-03-final/junit.xml: `tests=2 errors=0 failures=0 skipped=0 time=15.003` (nodes 8.052 + 5.973). command.txt: exact CMD + CWD D:/bt_api_py + `Fri Oct 16 13:43:01 2026` + `pytest-exit:0`. stdout.txt ends `2 passed, 1 warning in 14.99s` (warning = pre-existing unknown config option).
- Recomputed sha256 match the recorded values in 00-INDEX.md: stdout `83bafd61…632f`, junit `6593d9f2…4e9c`, drill obs `522b3296…663b`, rollback obs `c4050b03…bdd0a`. NOTE: run-03-final contains NO result.json and NO stderr.txt — the brief's "result.json claims" has no counterpart artifact; the only recorded hashes are in 00-INDEX.md and they match live recomputation.
- Both run-03 observation JSONs: `status: PASS`. run-02 counterparts also PASS. Recorded command consistent with report.
- regressions/: six junits `12/1/1/1/32/21 = 68 tests, all 0F/0E/0S` (attributes read directly); all six stdouts end `exit:0` with matching summaries (e.g. migration-gate `12 passed … in 2.52s`); junit timestamps serial 13:43:34 → 13:44:26; ruff.txt contains CMD + `All checks passed!` + `exit:0` (13:44:32).

### 4. Honesty check — VERIFIED

- (i) Regression stdouts indeed lack embedded CMD lines (`grep -l "CMD:"` in regressions/*-stdout.txt → none) — as disclosed.
- (ii) Pre-fix folders contain only junit.xml + stdout.txt (no command.txt) — consistent with disclosure; status-log descriptions match the artifacts themselves: run-00 junit failures=2 with `ServiceConfigError: CLIENT_ALIAS_MOUNT_MISMATCH at $.clients[0].settings.bindings` (stdout shows `2 failed`); run-01 junit failures=1, `AssertionError: assert 'REJECTED' == 'UNKNOWN'` at line 1695, test2 passed.
- (iii) Headline finding: verified against run-01 artifacts to the extent present — the REJECTED-vs-UNKNOWN flip is directly evidenced (line 1695 assertion output). run-01 wrote no observation JSON, so the conservation details (zero risk rows, quota conserved at that moment) are not independently re-evidencable from run-01; they are asserted in the reworked test (lines 1700–1724) and confirmed in the run-03 drill JSON: in ALL five phases intent-2 risk rows empty, `provider_dispatch_counts {intent_1: 1, intent_2: 0}`, `increase_count 1 / 100000`, freeze `dispatch-inflight:mig03b-unknown-order-001` active. Report's phrasing is fair.
- All other report numbers verified from the JSONs: epochs `0d6c04fa…` / `11e4739f…`, migration_id `4f02e54a…`, cursor_epoch `3637613e…`, negatives `[null, STREAM_EPOCH_MISMATCH, STREAM_EPOCH_REQUIRED]`, renamed_scope_denials `[SCOPE_DENIED, SCOPE_DENIED]`, double-owner `ORDER_OWNER_ALREADY_ACTIVE / failed / registry_created False / provider_calls 1`, first_write_seal `order @ 2026-10-16T05:43:10Z`, restart epoch `b62cc356…` (exec 2 ids + gateway 1 id), post-seal refusals all REVERSE_MIGRATION_REFUSED (refused child exit 0, provider_calls []), hosts host-a/b/b2 normal_close_ack=true normal_exit_code 0, cleanup failures [] in both JSONs; rollback obs `old_deployment_start_permitted`, `marker_removed_before_seal`, `CUTOVER_MARKER_CONFLICT`.

### 5. Scope — VERIFIED

- `git status --short` in D:\bt_api_py: `?? tests/unit/test_service_migration_drill.py` (the round's only repo deliverable), `?? .codex-tmp/` (scratch), `? bt_api/bt_api_risk` (submodule holds only untracked `.codex-tmp/` scratch — verified inside), ` M tests/unit/test_load01_hundred_strategy_30min.py` (mtime 12:38:40, BEFORE round start 13:29 — separate LOAD01 workstream, explicitly acknowledged in status file), `?? %SystemDrive%/` (2026-10-15) and `?? 0` (2026-10-15) pre-existing strays. No other modification attributable to this round.

### 6. Import/dependency check — VERIFIED

- Imports (grep `^import|^from`): stdlib, pytest, zmq; `bt_api_base.service_protocol`, `bt_api_execution`, `bt_api_gateway[.service_component]`, `bt_api_risk`, and bt_api_py modules (service_host, service_components, service_config, service_execution, service_migration_gate, service_registry, service_runtime). Submodule code imports resolve to the four `bt_api/*` submodules, each at committed revisions in sync with the root index; no dirty tracked files in any imported submodule (bt_api_risk has only untracked `.codex-tmp/`). No reliance on uncommitted in-flight edits found. The test additionally self-verifies its import origins at runtime.

## Findings/caveats (non-blocking)

1. No `result.json`/`stderr.txt` exist in run-03-final (brief expected them); sha256 evidence lives in 00-INDEX.md and matches recomputation. Recording-granularity gap, not a claim failure.
2. run-01 has no observation JSON, so the quota-conservation detail of the headline finding is only directly evidenced post-rework (run-03 test asserts + JSON confirms across all phases). Report does not overstate this.
3. Worktree carries unrelated pre-existing changes (` M tests/unit/test_load01_hundred_strategy_30min.py` mtime 12:38; strays `%SystemDrive%/`, `0` from 2026-10-15). Must be excluded from the root commit.
4. The test depends on bt_api_execution/bt_api_gateway at current `dev` heads (gitlinks recently moved by other workstreams, e.g. f5c0306); future submodule movement will re-pin the drill. At review time all pins match and no submodule tracked files are dirty.
5. Failure-path hygiene of the test itself is strong (spawned processes bounded-reaped, observation status computed defensively, `_assert_no_fallback_cleanup`), which reduces flake risk but not zero: the drill runs ~15s with real sockets/DBs — CI timing margins should be watched.

No claim in .codex-tmp/mig03b-report.md or mig03b-status.md was found contradicted by the artifacts.
