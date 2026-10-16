# Workstream #32 — independent static review status (registry run-adoption)

- Repo: D:\bt_api_py, branch dev, HEAD d85ec636cb9d588afa8bf0ce52b1fd86c19e363c (verified via `git rev-parse HEAD`; untouched by reviewer).
- Subject (uncommitted): bt_api_py/service_registry.py, bt_api_py/service_runtime.py. The entire change under review = live `git diff` of exactly these two files vs HEAD (109 insertions / 35 deletions combined).
- Reviewer discipline: read-only; no git add/commit/stash; no source edits; the only file written is this review file (2026-10-16 ~11:38).
- Author status file D:\bt_api_py\.codex-tmp\reg32-status.md read; its claims cross-checked below.

## C1. Hashes and live status — PASS

- Live sha256 bt_api_py/service_registry.py = 7c708a434397287117545a144bd7264cd4aa0741dd21e0a990c022f915cd2280 == author-claimed final (reg32-status.md:69; summary.txt:11; after-verify-sha256.txt). PASS.
- Live sha256 bt_api_py/service_runtime.py = 3d7fde5919b6585dee020472dd4d268620459fc2b03b94c81f201c5895e83574 == author-claimed final. PASS.
- Diff stat vs HEAD: `service_registry.py | 36 +-`, `service_runtime.py | 108 +-` (109 insertions, 35 deletions total).
- Live `git status --porcelain` (11:37, churning due to concurrent workstreams):
  - ` M bt_api_py/service_registry.py` and ` M bt_api_py/service_runtime.py` — the ONLY files of this round; stage exactly these two.
  - Foreign, must NOT be staged: ` M bt_api/bt_api_risk` (submodule gitlink), ` M tests/unit/test_load01_hundred_strategy_30min.py`, `?? %SystemDrive%/`, `?? .codex-tmp/`, `?? 0`, `?? docs/迭代计划/迭代10-可选组件与可配置通信/risk-order-exposure-sdk-round-r20.md`, `?? tests/integration/_arb04_group_reply_loss_support.py`, `?? tests/unit/test_service_risk_order_journal_decimal_match.py`.
  - Appeared mid-review from concurrent ARB04/CFG workstreams (also foreign): `?? bt_api_py/service_group_monitor.py`, `?? tests/integration/_group_policy_host_fixtures.py`, `?? tests/integration/test_arb04_group_reply_loss_service.py`.
- Author's git-status-before-edits.txt/after.txt are historical (HEAD moved 77668953 -> f308c8d8 -> d85ec636 concurrently; service_components.py / test_service_group_policy_*.py entries were later committed by other rounds). Live status above is authoritative.

## C2. Registry semantics — PASS

- _claim_strategy_write: service_registry.py:1936-1997. Claim rows fetched once (1944-1952) inside the `with self._transaction()` (opened at 1847; BEGIN IMMEDIATE + self._lock at 911-929) — released-claim detection uses only durable table state (run_id, active_registration_id).
- ANY active claim (1953-1954) => plain `_failure("DUPLICATE_ACTIVE_RUN")` (1343-1344) => result exactly `{}`. PASS.
- All-released + single OTHER run (1958-1970) => tuple refusal carrying `{"strategy_write_run_id": <durable run>}` — the only wire-visible delta.
- Mixed/multi-run vectors (1956-1957) => plain refusal; pre-fix these vectors were also plain DAR (any run mismatch or active). Identical outcome.
- Same-run released rebind: guard at 1958 skips the signal; falls through to the unchanged INSERT OR IGNORE + `UPDATE ... AND active_registration_id IS NULL` (1971-1996). Identical to pre-fix. test_service_registry.py:667 confirms (run-reg3 31 passed).
- Call site 1882-1888: tuple => `_failure_with_result` (1347-1348) whose envelope {ok, code, result} equals `_failure` (1343-1344) except result content. PASS.
- Interleavings: BEGIN IMMEDIATE serializes registrations; two racing adopters — winner rebinds the single durable run, loser's fetch sees active non-NULL => plain refusal with empty result => runtime raises (no second adoption). No two-live-run window found.
- Expired-lease-but-alive old run: `_expire_sessions` (958-969, called inside the adopter's transaction at 1848) NULLs active_registration_id and deletes the stale session before rebind; the write path refuses the old process via session-missing NOT_REGISTERED (2113-2118) and, independently, via `_strategy_claim_exists` (2344-2363) which requires run match AND active_registration_id == that registration (dispatch gate 2161-2166). Fail-closed confirmed.

## C3. Runtime semantics — PASS (one nuance)

- ServiceRuntimeError.result defaults to {} (service_runtime.py:132-134); no remote text by construction. PASS.
- _rpc failure branch 1630-1648: result attached only when `body.get("ok") is not True` maps to ServiceRuntimeError (1645-1647); outcome-unknown path (1634-1639) untouched. Nuance: it applies to any failure reply that raises ServiceRuntimeError (control or known-code business call), not literally control-only; the adoption guard is enforced in the helper, so no semantic impact.
- Retry guard: loop 1254-1267; helper `_released_run_adoption_run_id` 1704-1723 requires exact code DUPLICATE_ACTIVE_RUN, `identity.role is WireRole.STRATEGY_CLIENT`, `type(run_id) is str` non-empty, `!= scope.run_id`; retry only when `attempt == "initial"` (1276-1277) => exactly one retry, with `replace(scope, run_id=adopted)`.
- Adopted scope reaches subsequent orders: `scope = attempt_scope` (1293) -> placeholder RegisteredBinding (1307-1312) and final binding (1356-1361) store the adopted scope -> later calls use `rpc_scope=binding.scope` (487, 545) and cleanup `_unregister_during_startup(..., scope, ...)` (1387-1394). Target test asserts exactly this: tests/unit/test_service_risk_only_order_integration.py:2224 (`cross_run_command[4]["run_id"] == run_id`) while 2212 keeps the fresh client runtime.run_id.
- Non-definitive failures still book unknown registration: ServiceRuntimeError branch 1278-1284 and BaseException branch 1285-1292 preserve the pre-fix condition (`request_started` and not `server_rejected`/`request_not_sent`); the retry path (`continue`) only follows a definitive refusal.
- DUPLICATE_ACTIVE_RUN membership in `_is_definitive_control_rejection` for service.register: service_runtime.py:1675 — present; the function is untouched by the diff. PASS.
- Root cause confirmed: run_id minted per runtime (336) and injected into scope (1016); fresh uuid per restart.

## C4. Repo-wide exact-result cross-checks — PASS

Grep `DUPLICATE_ACTIVE_RUN` in tests/:
- tests/unit/test_service_write_capability_registration.py:186 (only exact-dict assert: `second == {"ok": False, "code": "DUPLICATE_ACTIVE_RUN", "result": {}}`). Context 166-186: second registration while the first claim is ACTIVE => any-active branch => empty result. HOLDS.
- tests/unit/test_service_group_registration_lifecycle.py:236, 357 — group write-path refusals (plain `_failure`), code-only asserts. HOLDS.
- tests/unit/test_service_registration_durable_business_lifecycle.py:810 — live competitor with same run id => active => plain; `.code` assert. HOLDS.
- tests/unit/test_service_registry.py:216, 620, 645, 661, 723, 1154 — code-only asserts (645/661 now additionally carry the run id in result, code unchanged; 1154 is a SERVICE_ERROR_CODES membership set). HOLDS.
- All were re-run post-fix in run-reg3 (31 passed) / run-reg03 (1 passed) / run-rh (81 passed).

## C5. Evidence audit D:\temp\it10reg32-20261016-r1\ — PASS with one stale artifact (D2)

- run1.txt/run1-junit.xml: 20 passed; junit `errors="0" failures="0" skipped="0" tests="20"`, timestamp 11:22:22. run2.txt/run2-junit.xml: 20 passed; junit same counts. Both match "20 passed". Transcripts show only the full 20-node file (classname tests.unit.test_service_risk_only_order_integration).
- run-reg3.txt: 31 passed (registry + write-capability + group-lifecycle). run-rh.txt: 81 passed. run-reg03.txt: 1 passed. run-arb.txt: 6 passed. run-dim01.txt/run-dim01-junit.xml: 1 passed, tests=1. run-oe.txt: 6 passed. All match author claims.
- run-grp.txt: 59 passed / 1 failed — TypeError `allowed_call_errors` (foreign; see D3). run-hostint.txt: 1 failed (RESOURCE_KEYS_MISMATCH) / 1 passed — (foreign; see D3). Consistent with author's stated outcomes.
- No run-reg/run-reg2 logs saved (only -tmp dirs) — historical, acceptable; no exit-code files exist in r1 (exit codes present only in summary/R2).
- sdk-fix.diff: STALE — byte-compared vs live diff, two hunks differ; contains the FIRST predicate (`if durable_runs and (len(durable_runs) != 1 or scope.run_id in durable_runs)`) matching after-edit-sha256.txt 60712659... (11:21); live file mtime 11:22:11 (predicate fix) predates run1 junit timestamp 11:22:22, so all final runs used the final bytes; after-verify-sha256.txt (11:29) == live == R2 post-run-sha256.txt. See D2.
- R2 (D:\temp\it10reg32-20261016-r2\, complete at review time): run1 20 passed, exit 0, junit tests=20 failures=0 (timestamp 11:33:51, current committed tree); run2 (config_scoped suite) 20 passed/1 failed, exit 1; run3 (host_integration four_sqlite node) 1 failed, exit 1. post-run-sha256.txt == live hashes. R2 status: COMPLETE (no longer pending).

## C6. Test-file identity — PASS

tests/unit/test_service_risk_only_order_integration.py: `git diff` vs HEAD empty; live sha256 9d29b5884dfeada810a0c0c4885965ef04d3bd49c91da8091f7ce51d1412f552 == `git show HEAD:` blob sha == author claim (DIM01 pin).

## D-register (disclosures for the commit message)

D1. Foreign dirty/untracked files that must NOT be staged (11:37 live status): bt_api/bt_api_risk (M), tests/unit/test_load01_hundred_strategy_30min.py (M), %SystemDrive%/ (??), .codex-tmp/ (??), 0 (??), docs/迭代计划/迭代10-可选组件与可配置通信/risk-order-exposure-sdk-round-r20.md (??), tests/integration/_arb04_group_reply_loss_support.py (??), tests/unit/test_service_risk_order_journal_decimal_match.py (??), plus mid-review arrivals bt_api_py/service_group_monitor.py, tests/integration/_group_policy_host_fixtures.py, tests/integration/test_arb04_group_reply_loss_service.py. Stage ONLY the two subject files.

D2. Stale evidence artifact: sdk-fix.diff (and after-edit-sha256.txt) capture the pre-predicate-fix version (blob 60712659..., first predicate that refused same-run rebind). They do NOT match the live diff. The live bytes were the ones tested (registry mtime 11:22:11 < run1 start 11:22:22; final hash after-verify 11:29 == live == R2 post-run-sha256). Commit message must cite the live diff + after-verify hashes, not sdk-fix.diff; recommend refreshing the diff artifact if evidence is archived.

D3. Two red nodes at the committed HEAD d85ec636, reproduced by R2 on the current tree, NOT caused by this fix (reviewer assessment):
- tests/unit/test_service_config_scoped_execution_routes.py::test_group_host_cleanup_attempts_every_action_and_raises_first_failure — deterministic TypeError: tests/integration/test_arb02_group_budget_service.py:279 passes `allowed_call_errors=` to the positional-only fake `close_clients` at tests/unit/test_service_config_scoped_execution_routes.py:407. Both files clean at HEAD; traceback contains no SDK frames; argument binding fails before any SDK code. Introduced by other rounds (arb02 file last touched by 69cffe84). Not attributable to #32.
- tests/unit/test_service_host_integration.py::test_spawned_curve_host_serves_four_sqlite_read_adapters_and_closes_normally — RESOURCE_KEYS_MISMATCH raised at bt_api_py/service_components.py:1295-1296 during host startup, before any registration; blame of that region shows d85ec636 (group_admission block) and 04707f4b among contributors; neither subject file influences the component resource-key contract. Pre-existing red node at HEAD; not attributable to #32. (Author's R1 attribution to the then-dirty service_components.py is now upgraded: the same check is committed in d85ec636.)

D4. Residual risk / behavior-change notes:
- Wire-visible change: a DUPLICATE_ACTIVE_RUN register refusal for the released-single-run vector now carries `result={"strategy_write_run_id": ...}` where it previously carried `{}`. In-repo exact-dict consumers verified safe (only write_capability:186, an active-claim scenario); external consumers asserting exact {} on the released vector would observe the change.
- `.result` attach applies to any failure reply mapped to ServiceRuntimeError (including non-control known-code failures), not literally control-only; the registry emits only `{}` or the durable run id, and wire size is bounded — negligible leak surface, noted for accuracy.
- Fail-closed review found no two-simultaneously-live-runs window: adoption requires all claims released (active NULL) with one durable run; concurrent adopters serialize via BEGIN IMMEDIATE and the loser gets a plain refusal; the old process's session is expired/deleted before rebind and the write path re-checks the exact claim.
- Multi-run/mixed released claim vectors intentionally stay plain refusals (manual intervention).
- Expired-alive-run adoption window: an old process whose lease lapsed cannot write after adoption (session deleted => NOT_REGISTERED; claim check requires active==registration_id); its reconnect as "same run" would itself take the rebind path. No defect found; residual operational reliance on lease expiry as release proof is by design.
- R1 evidence ran on a tree based on f308c8d853 (pre-d85ec636); the current-tree verification is R2 (20/20 green, exit 0). R2 run2/run3 green is NOT claimed (foreign failures D3 persist).

## Verdict: GO

Reasons: both file hashes match author claims; the registry/runtime diffs implement exactly the described fail-closed adoption (durable-only release detection, tuple signal only for released single-other-run, one guarded retry, adopted scope reaches the wire); all repo-wide DUPLICATE_ACTIVE_RUN asserts still hold; 20/20 target nodes green twice in R1 and once more in R2 on the current committed tree; remaining red nodes are foreign and disclosed (D3); the only evidence defect is the stale sdk-fix.diff (D2), which does not affect the code under review because all final runs postdate the final bytes (mtime/hash/junit timeline) — commit is approved provided the commit message discloses D1-D4 and stages only the two subject files.
