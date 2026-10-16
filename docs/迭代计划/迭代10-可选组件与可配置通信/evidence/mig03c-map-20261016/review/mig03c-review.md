# MIG03c independent adversarial review — `service_migration_map.py` + focused tests

- Date: 2026-10-16. Reviewer: independent review agent (no code written or fixed; no staging/commit).
- Workspace: `D:\bt_api_py`, branch `dev`, HEAD `ee3b05c2ed81ea27598dab1c60bb6f6d3339324d` ("feat(sdk): add gateway wire client path ... (MIG01 R3)").
- Purpose: adversarially verify that increment C honestly closes flip-audit finding F1 (L854 positive persistent legacy→new mapping).
- Evidence dir (mine): `D:\temp\it10mig03c-review-20261016-r1`. Producer dir `D:\temp\it10mig03c-map-20261016-r1` read-only, untouched. Flip audit `D:\temp\it10mig03-flip-audit-20261016-r1\audit.md` read-only (F1 confirmed: `UNCOVERED（F1）` at its row 19).

## Verdict: GO-with-disclosures

The two files under review are exactly as claimed (hashes/lines), are the only MIG03c-attributable changes, are stdlib-only, implement a real persistent conflict-checked mapping for all five legacy kinds with typed fail-closed errors and a register-only write surface, and the 7 test nodes genuinely assert the claimed invariants (real SQLite files, close/reopen persistence, conflict refusals with zero mutation). The literal wording of two author claims is not fully reproducible/accurate (see Falsified/Not-reproduced). Both are environmental or disclosed-in-module, not deliverable defects. Commit of exactly these two files at the pinned hashes is safe once the disclosures below are carried.

## Verification results (with raw evidence)

### 1. sha256 + line counts — MATCH exactly

Command: `sha256sum` + `wc -l` (full capture: `checksums-wc-gitstatus.txt`)

```
9267adac485d28d160a01944696aacc15b2fb5d44df527426ba6fe5a655ddb32 *bt_api_py/service_migration_map.py
0836aac67065a52747c467eab437c3e7a3b4a624d1f4433abf919bd776fa055c *tests/unit/test_service_migration_map.py
  560 bt_api_py/service_migration_map.py
  372 tests/unit/test_service_migration_map.py
  932 total
```

Also cross-checked against producer evidence `D:\temp\it10mig03c-map-20261016-r1\sha256-new-files.txt` — byte-identical hash strings.

### 2. Scope — only these two files are MIG03c-attributable

`git status --porcelain` (full capture: `checksums-wc-gitstatus.txt`, `git-status-after-review.txt`; identical before/after my runs):

```
?? bt_api_py/service_migration_map.py
?? tests/unit/test_service_migration_map.py
```

Both untracked, nothing staged. Pre-existing/unrelated dirty entries (present at review start, not attributable to MIG03c, left untouched):

- `M bt_api/bt_api_monitor`, `M bt_api/bt_api_risk` (submodule gitlinks)
- `M docs/迭代计划/迭代10-可选组件与可配置通信/开发与验收进度.md`, `.../迭代10验收状态汇总.md`
- `M tests/integration/test_arb02_group_budget_service.py`, `M tests/integration/test_arb03_dim05_group_partial_fill_service.py`, `M tests/unit/test_load01_hundred_strategy_30min.py`
- `?? %SystemDrive%/`, `?? .codex-tmp/`, `?? 0`, `?? docs/.../evidence/arb02-crashprefirstleg-20261016/`, `?? docs/.../evidence/arb03-r3-fill-scenarios-20261016/`, `?? tests/bt_api_contract/test_venue_order_type_tif_matrix.py`

(These also appear in the producer's own `git-status-before.txt` from 14:08, except the two docs md files and evidence dirs which appeared later from other workstreams.)

### 3. Implementation claims — verified by code reading + independent runtime check

- 3a five legacy kinds → new ref/book/account-key: `SOURCE_LEGACY_STRATEGY_ID→strategy_ref`, `bare_symbol→booked_instrument`, `bare_order_id→order_ref`, `in_process_run→run_ref`, `account_config→account_key` (`TARGET_KIND_BY_SOURCE`, module L96-102); all five exercised in test node 1.
- 3b conflict-checked, typed refusal, no silent overwrite: `register()` runs `BEGIN IMMEDIATE`, refuses `SOURCE_ALREADY_MAPPED` (same source, different target) and `TARGET_ALREADY_BOUND` (exclusive target held by another source), rolling back before raising (`_rollback`). Test nodes 2-4 assert raise + code + prior mapping intact + zero minted rows.
- 3c history retention: retained as the immutable first-write record; see Falsified/Not-reproduced item 2 for the wording caveat. The docstring (module L28-31, L372-383) discloses the design explicitly.
- 3d typed errors, fail-closed: single `MigrationMapError(RuntimeError)` carrying machine codes (`SOURCE_ALREADY_MAPPED`, `TARGET_ALREADY_BOUND`, `INVALID_MIGRATION_MAP_ARGUMENT`, `MIGRATION_MAP_UNREADABLE`, `MIGRATION_MAP_BACKEND_FAILURE`). `_ensure_schema` refuses foreign/versioned-mismatched DBs; test node 7 proves garbage file refused byte-unchanged and an alien valid SQLite DB not adopted. Nuance: it is one typed class with codes, not a multi-class hierarchy (matches house style `MigrationGateError`).
- 3e register-only: the only write op is `register()`; no update/delete/rename/reassign exists. `register()` is internally a read-then-insert but transactionally guarded (BEGIN IMMEDIATE) and race-safe against other connections. Read APIs (`resolve`, `history`, `bindings_for_target`, `bound_*_count`, `registration_count`) are plain deterministic SELECTs; node 5 asserts resolve determinism and node 2/3 assert resolution stability after refusals.
- SQLite shape — independently verified (not just code-read), capture `wal-pragma-check.txt`:

```
journal_mode = wal
synchronous = 2          # == FULL
journal_mode reopened = wal
rows = 1
```

### 4. Test execution — 7 nodes; substance passes, but the literal claimed command fails for pre-existing environmental reasons

Raw: `stdout.txt`/`stderr.txt`/`junit.xml`/`exit-code.txt` (claimed command), `stdout-noasyncio.txt`/`junit-noasyncio.xml`/`exit-code-noasyncio.txt` (functional run), `env-repro-gate-file.txt`, `stdout-prepend.txt`.

(a) Exact claimed command `C:/anaconda3/python.exe -m pytest tests/unit/test_service_migration_map.py -v --tb=short`:

```
ERROR  - AttributeError: 'Package' object has no attribute 'obj'
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
========================= 1 warning, 1 error in 1.07s =========================
exit=2
```

(b) Same failure reproduces on the PRE-EXISTING, untouched `tests/unit/test_service_migration_gate.py` (exit 2) and also with `--import-mode=prepend` override. Root cause (environmental, repo-wide, pre-existing): anaconda env has pytest 8.0.0 + pytest-asyncio 0.23.0; 0.23.0's `pytest_collectstart` attaches a fixture via `collector.obj` on `Package` collectors and crashes (upstream bug fixed in 0.23.2; cf. the string in the traceback). Plugin files dated Mar 27 2026 — not mutated today. (The `pip show pytest-asyncio → 1.3.0` red herring is from `C:\Python314`'s pip on PATH, not the anaconda interpreter.)

(c) With the buggy plugin disabled, the deliverable's suite is green:

```
tests/unit/test_service_migration_map.py::test_positive_mapping_of_all_five_legacy_kinds_with_history_across_reopen PASSED [ 14%]
... (all 7 nodes PASSED; junit-noasyncio.xml: tests=7 errors=0 failures=0) ...
======================== 7 passed, 1 warning in 1.23s =========================
exit=0
```

Node IDs seen: 7 collected / 7 passed — matches the claimed 7. Producer's own junit corroborates a full pass run in this workspace at 14:10:32 today (`tests="7" errors="0" failures="0" time="1.127"`), and their stdout tail reads `7 passed, 1 warning in 1.41s`, exit-code 0. However their `stdout.txt` is a tail-only capture (631 B, dots = default verbosity, inconsistent with the claimed `-v` invocation) and their pack has no `stderr.txt`, so the invocation that actually worked is not visible in their evidence. Given the deterministic repo-wide crash, their successful run must have disabled the buggy plugin (e.g. `-p no:asyncio`) or used an equivalent dodge — the literal command as written cannot pass in this environment.

### 5. Honesty check on the 7 invariants — all nodes genuine; mutation analysis

No skips/xfails/monkeypatch of the logic under test; every node operates on real SQLite files under `tmp_path` and cleans up (see 7).

| # | Node | Genuinely asserts | Would fail if invariant broken? |
|---|------|-------------------|--------------------------------|
| 1 | `test_positive_mapping_of_all_five_legacy_kinds_with_history_across_reopen` | all 5 kinds registered/mapped with exact fields; resolve resolved; history `== (record,)`; then close + REOPEN on the same file path: mappings still resolve to identical values and `resolution.mapping == records[...]`; fan-in positives (two symbol spellings → one booked instrument; two config digests → one account key); counts | Yes: in-memory-only store or lost writes → reopen asserts fail; any target-kind mismatch fails the chained equality. Reopen on the same on-disk file is real persistence proof. |
| 2 | `test_conflicting_source_registration_refused_without_mutation_no_second_partition` | rename attempt (same source, new target) raises `MigrationMapError` code `SOURCE_ALREADY_MAPPED`; prior mapping and first-epoch provenance intact; `history == (original,)`; `bound_target_count == 1`; renamed ref has 0 bindings; `registration_count == 1` | Yes. Remove the conflict check: the INSERT hits the `(source_kind, source_value)` PRIMARY KEY → wrong code (`MIGRATION_MAP_BACKEND_FAILURE`) fails the code assert; remove PK too → count/history/binding asserts fail. Not a tautology. |
| 3 | `test_ambiguous_exclusive_target_refused_no_duplicate_target_minted` | for each exclusive kind (strategy/order/run ref): second source→held target raises `TARGET_ALREADY_BOUND`, second source stays `UNALLOCATED`; final counts exact; fan-in kinds deliberately allowed to share | Yes. Remove the target check → no raise + `bound_source_count` becomes 2 → fail; UNALLOCATED assert fails. |
| 4 | `test_conflict_refusal_and_partition_invariant_under_sealed_cutover_epoch` | real interplay with pre-existing incr-A/B gate (`arm_migration_cutover`, `record_migration_first_write`, `enforce_migration_start` on the same state path): new-epoch registration; old-epoch rename refused; gate independently refuses old epoch (`REFUSAL_REVERSE_MIGRATION_REFUSED`) and admits new epoch; refusal + mapping survive store close/reopen | Yes: any removal of the conflict path or of persistence fails the reopen refusal asserts; the gate symbols exist at `bt_api_py/service_migration_gate.py:65,124,164,199` (tracked-clean), so the cross-module leg is real. |
| 5 | `test_unmapped_identity_stays_unallocated_pending_reconciliation_across_reopen` | all 5 kinds unknown → `UNALLOCATED`, `reconciliation_queue == "待对账"`, `mapping is None`, stable repeated resolve, `history == ()`, `registration_count == 0`; same after reopen | Yes for the load-bearing parts: auto-inventing a mapping/credit/reorder would flip status/count asserts. Minor: `first == second` alone is weak (identical dataclasses by construction); strength comes from count==0 + reopen loop. |
| 6 | `test_idempotent_replay_of_same_registration_keeps_single_row` | replay with a DIFFERENT `recorded_at` returns the original record (first timestamp wins); after reopen, replay with different epoch+note still returns first provenance; `registration_count == 1` | Yes: an UPDATE-on-replay or append-second-row implementation fails `replay == first` / count asserts. Caveat: the in-run replay varies only `recorded_at` (note identical); epoch/note variation is covered in the post-reopen replay. |
| 7 | `test_unreadable_store_fails_closed_and_rejects_invalid_arguments` | garbage file → `MIGRATION_MAP_UNREADABLE`, file bytes unchanged (no silent re-create/repair); alien valid SQLite DB → same refusal; invalid kinds/values/types → `INVALID_MIGRATION_MAP_ARGUMENT`; count 0 | Yes: adopting any sqlite file / recreating without raising fails the raises asserts; raw `sqlite3.DatabaseError` leaking fails the type assert. |

Verdict on honesty: no tautological node; every claimed invariant has at least one load-bearing assertion that fails under its removal. Two info-level weaknesses recorded: (i) `bound_target_count` ≤1 is guaranteed by the table PK, so that single assert cannot detect a missing conflict check on its own (the raises+code asserts are the load-bearing ones); (ii) no test pins the `journal_mode=WAL` / `synchronous=FULL` pragmas (I verified them independently here; a pin would make the durability shape contractual).

### 6. Import surface — stdlib only, no gateway/forwarding/network

`grep -nE "^(import|from)\s" bt_api_py/service_migration_map.py`:

```
from __future__ import annotations
import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
```

No local imports at all in the module (not even bt_api_py), hence no gateway/forwarding/network dependency. Test file imports: `sqlite3`, `pathlib`, `pytest`, and only the two local modules `bt_api_py.service_migration_gate` + the module under test.

### 7. Test pollution — none into the repo

- All file writes go to pytest `tmp_path` (`_store_path(tmp_path)`, gate marker beside it in the same tmp dir); cleanup is pytest-managed.
- `git status --porcelain` before vs after all my runs: identical file set (no new sqlite/stray files in the repo). Captures: `checksums-wc-gitstatus.txt`, `git-status-after-review.txt`.
- My own check artifacts live only in `D:\temp\it10mig03c-review-20261016-r1` (including a `walchk-*/m.sqlite3` scratch DB from the pragma check).

## Falsified / not-reproduced claims

1. **Claim 4 (exact command reproduce) — NOT REPRODUCIBLE AS STATED, substance true.** The literal command exits 2 with a collection error in this environment; the same error hits pre-existing files, so it is environment-wide (pytest 8.0.0 + pytest-asyncio 0.23.0 bug, pre-existing install from Mar 27 2026), not a defect of the deliverable. Functional truth confirmed: 7 passed / exit 0 with `-p no:asyncio` (1.23 s), corroborated by the producer's junit (7 tests, 0 failures, 0 errors, 2026-10-16T14:10:32). Secondary evidence-integrity nit: producer `stdout.txt` is a tail-only, non-verbose capture (dots, no header, no `stderr.txt`), inconsistent with the claimed `-v` invocation — the actual working invocation is not shown in the evidence pack. Recommend the next ledger entry re-issue the exact working command.
2. **Claim 3c as literally worded ("re-registration/change produces history rows, nothing deleted") — NOT TRUE literally; reframed and disclosed.** The implementation never appends history rows: same-target re-registration is an idempotent replay returning the original record (first `recorded_at`/`recorded_epoch`/`note` retained), and any change is refused outright; `history()` returns at most the one retained authoritative row. "Nothing deleted" is true. The module docstring discloses this design honestly ("append-only, so the authoritative record is also the whole history", L372-383). This satisfies the plan's "持久映射及历史关联" under the retained-association reading; it does NOT provide a multi-version audit journal if that is the intended reading — the ruling should record which reading is accepted.

## Disclosures (carry into the flip ruling)

1. Component-only: no production caller — author disclosed; independently confirmed by repo-wide grep for `service_migration_map|MigrationIdentityStore|MigrationMapError` excluding the module and its test → zero hits.
2. History is a single immutable retained association, not a multi-version journal (module-disclosed; see above).
3. `MigrationMapError` is one typed class with machine codes, not a class hierarchy (functionally equivalent guard; house-style consistent).
4. No end-to-end order-partition/dual-quota test — only identity-level (author disclosed; confirmed: nodes assert counts within the map store, not order/credit systems).
5. F2 (journal-scope-only 待对账 retention) and F3 (seal trigger has no production caller) remain open per flip audit — this deliverable does not change them.
6. Environment: plain `python -m pytest` in the anaconda env is broken repo-wide by pytest-asyncio 0.23.0 + pytest 8.0.0 (Package/obj crash); use `-p no:asyncio` or fix the plugin until the env is repaired. Affects all suites, pre-existing.
7. Minor invariant-surface weaknesses: PK-tautological `bound_target_count`; `first == second` trivially-true leg in node 5; node 6 in-run replay varies only `recorded_at`; WAL/FULL pragmas not pinned by any test (verified independently here).

## Commit safety

**YES — safe to commit exactly these two files at the pinned hashes:** `bt_api_py/service_migration_map.py` (560 lines, sha256 `9267adac...ddb32`) and `tests/unit/test_service_migration_map.py` (372 lines, sha256 `0836aac6...055c`). Both are untracked, nothing is staged. Do NOT include: `.codex-tmp/` (incl. this review), `D:\temp\...` evidence, the two modified docs md files, the evidence/ dirs, modified integration tests, `tests/bt_api_contract/test_venue_order_type_tif_matrix.py`, submodule gitlinks, or the strays `0` and `%SystemDrive%/` — all belong to other workstreams or pre-existing state. Note the two hashes above are load-bearing: any edit invalidates this verdict.

## Serialization record

Before/during runs: resident services PID 128016 (`ai-for-investor` venv python) and 132224 (Python312) untouched; PID 13961 (`/c/anaconda3/python`, started 13:44:48, presumed live drill driver) untouched; producer's 14:10 capture showed additional transient anaconda PIDs (16935/142928/103052/17025). All pytest invocations were run strictly one at a time (three short runs, each ≤ ~2.3 s wall). No processes killed; nothing in `D:\temp\it10mig03c-map-20261016-r1` was modified (read-only reads).

Artifacts in `D:\temp\it10mig03c-review-20261016-r1`: `checksums-wc-gitstatus.txt`, `stdout.txt`, `stderr.txt`, `exit-code.txt`, `junit.xml` (claimed-command failed run), `stdout-noasyncio.txt`, `stderr-noasyncio.txt`, `exit-code-noasyncio.txt`, `junit-noasyncio.xml` (7 passed), `stdout-prepend.txt`, `env-repro-gate-file.txt`, `git-status-after-review.txt`, `head-branch.txt`, `wal-pragma-check.txt`, `walchk-*` (scratch DB dir).

Review re-run commands:

```
cd /d/bt_api_py
sha256sum bt_api_py/service_migration_map.py tests/unit/test_service_migration_map.py
wc -l bt_api_py/service_migration_map.py tests/unit/test_service_migration_map.py
git status --porcelain
C:/anaconda3/python.exe -m pytest tests/unit/test_service_migration_map.py -p no:asyncio -v --tb=short   # 7 passed, exit 0
```
