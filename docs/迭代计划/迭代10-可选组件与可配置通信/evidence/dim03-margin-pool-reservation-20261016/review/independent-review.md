# DIM03 margin-pool commit round — independent static review

Reviewer: independent static reviewer (READ-ONLY run; only new file written = this file).
Date: 2026-10-16. Submodule: D:\bt_api_py\bt_api\bt_api_risk (branch dev, HEAD c7fc809b5b3fbcf0a19b06f9082cb010a095174e, committed round — untouched).
Round under review: DIM03 margin-pool sub-item commit (risk submodule commit first, then parent gitlink update at D:\bt_api_py).

## Hash verification (all recomputed from live bytes)

| Artifact | Claimed sha256 | Recomputed | Match |
|---|---|---|---|
| bt_api/bt_api_risk/src/bt_api_risk/core/admission.py | 28b9918db57ebd62024d09d3a32124b639a60c03da214d6187c7fbebfdbba73c | same | YES |
| bt_api/bt_api_risk/tests/test_margin_pool_reservations.py | 9b92ac635690b66d9697d8ff3e8e69fd069b0c13c7a558fac2ccdf6f8ee96a79 | same | YES |
| .codex-tmp/dim03-status.md | adbfb2e62b1f8b3af1253f941aec717f41a14eb535e4b771d19bff4c14500267 | same | YES |
| live diff vs risk HEAD admission.py | +252 / -27 | `git diff --numstat` = 252 27 | YES |
| r4 spec.json | 085587e8… | 085587e8fbc5901ed3b5918e87babde3a5711c7500113a638846a49714423ab8 | YES |
| r4 result.json | 7826dd8f… | 7826dd8fae8574fd2497faff89118cca0cf9f431a0734678b976bf70736b05fe | YES |
| r4 junit.xml | 56d92231… | 56d9223101e68a42e02ddb57932cb4f122048d6a0b9a0d854018ff2c399a832b | YES |

Also verified: r1 admission_head.py sha256 = 859a072c2f6edf65f0545422e092f3c78cab987e8e19a4bfbf3ad451021deef4 = `git show c7fc809b:src/bt_api_risk/core/admission.py` (r1 diff base is exactly HEAD).

## Scope guard (live risk-repo `git status --porcelain`)

- ` M src/bt_api_risk/core/admission.py` — DIM03-owned (under review).
- `?? tests/test_margin_pool_reservations.py` — DIM03-owned (under review).
- `?? tests/test_trade_identity_claims.py` — FOREIGN (DIM01 trade-identity, sha256 c0d977ce2d77b8a6cb4f73e54862661b8cd69dbd03dd43f1d24eb04751af13e2). Must stay out.
- `?? .codex-tmp/` — scratch (contains DIM04 fx-admission / portfolio-analytics QA areas). Must stay out.
- No staged changes anywhere (`git diff --cached` empty). Parent repo: ` M bt_api/bt_api_risk` gitlink still c7fc809b (= risk HEAD; dirty-worktree flag). Parent also has FOREIGN modified/untracked files (see D-register). During this review, additional foreign files appeared in the parent tree (bt_api_py/service_group_monitor.py, tests/integration/_group_policy_host_fixtures.py, test_arb04…, test_arb06…) — concurrent foreign workstream is live-writing; strictly foreign, keep unstaged.

## Checklist findings

### 1. Hashes, full live diff, additive-only semantics — PASS
- Read the complete live diff of admission.py (426-line diff text). Changed hunks: import (l.36), RiskIntent.resource field (l.275) + validation (l.293-294), fingerprint inclusion (l.374-378), set_margin_pool_limit (l.2444-2544), reserve() assert call (l.2598) + INSERT column list/bind (l.2629-2655), reserve_group_vector() assert (l.2825) + INSERT (l.2847-2887), _assert_margin_pool_limit (l.7913-7971), risk_reservations CREATE TABLE (l.8496-8498), two new CREATE TABLEs (l.8685, l.8695), ALTER migration columns (l.8932-8933), new index (l.9040-9045), _risk_intent_json (l.9103-9133), _risk_intent_from_json shape acceptance (l.9155-9193).
- None-resource trace: `_assert_margin_pool_limit` returns immediately (`resource is None or action is not INCREASE`, l.7926-7928); fingerprint property only appends `resource` key when not None (l.374); `_risk_intent_json` only appends when not None (l.9126); both INSERT sites bind NULLs for the new columns (l.2654-2655, l.2886-2887); all SELECT/UPDATE/trigger/constraint statements unchanged. For resource=None no new validation can fire anywhere in reserve()/reserve_group_vector()/serialization; behavior is byte-equivalent to HEAD in effect.
- No weakening: `_risk_intent_from_json` still enforces exact key-set equality but against the union of the two allowed shapes (l.9155-9159) with strict `AccountResourceKey.from_dict` (exact 2-field mapping) plus end-of-parse canonical re-check (l.9195-9196); existing strictness for malformed docs preserved. Both INSERT placeholder/param alignments re-counted manually (30 cols/22 params single; 30 cols/29 params group) — aligned; corroborated by 63 durable-risk-gate tests green.
- Both old insert sites are the ONLY `INSERT INTO risk_reservations` in src (grep).

### 2. Same-transaction assertion on reserve() and every group leg; clean refusal — PASS
- Both call sites are inside `with self._transaction()` (BEGIN IMMEDIATE): reserve at l.2556+2598; group per-leg inside `for leg in ordered_legs:` at l.2818/2825, all within the single l.2761 transaction. `_transaction` (l.9082-9091) does BEGIN IMMEDIATE → yield → ROLLBACK on any BaseException → COMMIT on success. Group legs inserted after their own assert in the same connection/transaction, so each leg's usage read sees prior legs' uncommitted rows (SQLite own-writes visibility) and the asserted sum covers legs 1..k exactly once (no double count; manual `+ Fraction(intent.notional)` adds the not-yet-inserted current leg).
- Conflict path: writer contention handled by sqlite `timeout=5.0` busy wait (l.9069); loser re-reads under its own BEGIN IMMEDIATE and denies via MARGIN_POOL_LIMIT_EXHAUSTED → ROLLBACK; race test asserts durable counts (1,0,0,0) (test l.719-727).
- No `pin()` exists in admission.py (only unrelated `service_component.GroupPolicyStore.pin`). Row-unchanged paths: idempotent existing-permit return (l.2565-2586) re-runs only the same checks HEAD ran (pool checks not re-run — consistent with HEAD's established idempotency semantics for existing permits); read snapshots (`read_group_vector_snapshot` wraps QUERY_ONLY+BEGIN/ROLLBACK l.2946-2963) untouched. Zero-write on denial verified in tests 1-5,7 via `_durable_counts` on 4 tables.

### 3. Fail-closed codes, equality, version rules — PASS (two defensive-only codes disclosed)
- All four codes are string constants raised as `RiskDeniedError` before COMMIT (fail-closed):
  - MARGIN_POOL_UNCONFIGURED (l.7934-7938) — reachable, tested (test l.440, l.448).
  - NOTIONAL_UNIT_MISMATCH (l.7939-7943) — constant; DEFENSIVE: unreachable through the normal API sequence because `_assert_intent_notional_unit` (l.7898-7911, called first at l.2595/2822) already forces intent unit == policy unit and `set_margin_pool_limit` only installs the current policy unit (l.2481-2483); fires only for foreign/tampered limit rows. NOT covered by the 7 tests.
  - MARGIN_POOL_UNIT_USAGE_UNAVAILABLE (l.7957-7961) — same defensive pattern as pre-existing NOTIONAL_UNIT_USAGE_UNAVAILABLE in `_assert_increase_limit` (l.6576-6581); reachable only with foreign row units. NOT covered by tests.
  - MARGIN_POOL_LIMIT_EXHAUSTED (l.7965-7970) — reachable, heavily tested.
- Equality allowed: strict `>` at l.7965 (test: exact 300/300 admitted, +0.01 denied, test l.195-236).
- Version rules precisely: versions are opaque `_ALLOCATION_ID` strings, NOT monotonic; enforced = (a) same current version with identical facts → idempotent no-op (l.2493-2499); (b) same current version with changed facts → ValueError (l.2500); (c) any version already in `risk_margin_pool_limit_versions` → ValueError (l.2501-2508), so replaced versions can never be replayed; a new unseen version may supersede freely. There is no allocation-sum concept — one ceiling per (scope_key, product_type, margin_pool). Docstring l.2456-2464 describes exactly this. FAIL-CLOSED quirk: "identical facts" compares `_decimal_text` strings (l.2495), so numerically equal but differently scaled retries (e.g. 300 vs 300.0) are refused as changed — errs on refusal (disclosure D6).

### 4. Migration additive + idempotent + old rows — PASS
- Fresh schema: 2 nullable TEXT columns appended in CREATE TABLE (l.8496-8498); new tables `risk_margin_pool_limits` / `risk_margin_pool_limit_versions` with IF NOT EXISTS (l.8685, l.8695).
- Existing DBs: ALTER TABLE loop guarded by `PRAGMA table_info(risk_reservations)` membership (l.8914-8938; new entries l.8932-8933) → idempotent on re-run; columns nullable, no default, no backfill → legacy rows keep working with NULL.
- Index: `CREATE INDEX IF NOT EXISTS idx_risk_reservations_margin_pool (scope_key, resource_product_type, resource_margin_pool, action, status)` (l.9040-9045), created after the ALTER loop → column existence guaranteed; idempotent.
- Usage query filters `resource_product_type = ?` (l.7944-7956) — SQL NULL never matches, so legacy rows never enter any pool accounting. Old-format stored child-intent JSON round-trips via shape acceptance + canonical re-check (l.9155-9196). `_initialize_schema()` runs on every gate construction (l.2011) → old DBs auto-migrate on next open.
- No destructive operation added. Note: DB-level old-format row re-read path is statically covered only (no test creates a pre-migration DB file); see D-register.

### 5. New test file — PASS (7 non-vacuous nodes)
- 7 tests counted (`grep -c` and manual): l.152, 272, 364, 429, 457, 515, 657. Real sqlite temp DBs via `DurableRiskGate(tmp_path / …)`; no mocks (grep for mock/patch = only "dispatch" substring; no monkeypatch). Import via package path (`import bt_api_risk`, `from bt_api_risk import …`, l.21-35); cross-process race test spawns real subprocesses (l.657-729) with PYTHONPATH injection from `bt_api_risk.__file__`/`bt_api_base.__file__` (source-tree imports in children).
- Each asserts load-bearing behavior: atomic competition + zero-write counts + exact-fill boundary + release-frees-credit (t1); whole-vector rollback + version revision + refill denial (t2); cross-pool isolation (t3); UNCONFIGURED both paths + keyless unaffected (t4); singleton+group share one credit (t5); keyless R77 regression with pool schema present, zero pool rows (t6); two-process race exactly-one-grant + durable (1,0,0,0) (t7).
- Coverage gaps (non-vacuous but incomplete, disclosed): pool-level NOTIONAL_UNIT_MISMATCH and MARGIN_POOL_UNIT_USAGE_UNAVAILABLE branches untested; no keyed reservation ever reaches SETTLED in tests, so the `status IN (ACTIVE, SETTLED)` retention of settled keyed rows is untested (code mirrors established strategy-allocation accounting l.8004-8022 exactly); old-format DB-row round-trip static-only. None of these would pass-if-feature-broken for the tested core claims.

### 6. Fixed-source QA r4 — PASS
- D:\temp\it10dim03-fixed-source-20261016-r4: exit.txt=0; stdout "114 passed in 43.61s"; stderr empty (sha e3b0c442… = empty). result.json: pytest_exit 0, source_count 144, sources_unchanged true, stdout/stderr sha256 match live bytes, heads_unchanged true, heads before==after=={root d85ec636, base b4aabd1e, exec dc8bd7a3, risk c7fc809b, transport a397fdb4} — all five live HEADs re-verified identical, all branch dev. junit.xml: tests=114 failures=0 errors=0 skipped=0; per-module margin-pool 7 / group-vector 15 / integer-clock 16 / dispatch-exposure 10 / import-boundary 3 / durable-risk-gate 63 = 114.
- Candidate faithfulness: all 144 spec `source_sha256` entries re-hashed live → 144/144 OK, 0 missing/mismatch. Structural equality: candidate risk/src 22 .py ↔ live risk worktree 22 .py byte-equal (all files); candidate base/src 111 .py ↔ live base worktree 111 .py byte-equal; candidate tests = exactly the 6 spec'd modules, byte-equal to live; pyproject.toml byte-equal. Sample: admission.py candidate==live (28b9918d…), new test candidate==live (9b92ac63…), service_component.py candidate==live (384243756948aabd…). PYTHONPATH = candidate\risk\src;candidate\base\src only, cwd/rootdir candidate\risk, --noconftest, -p no:cacheprovider; PYTHONPATH/PYTHONHOME/etc. cleared before set → candidate-only imports.
- r1 run-logs (D:\temp\it10dim03-20261016-r1\run-logs, dirs 00-20): every dir has command.txt/env.txt/stdout.txt/stderr.txt/exit_code.txt (+junit.xml on 00-10, 17-20). 00-03 baselines 15/16/10/3 passed; 04 first focused 1F/6P (failure = test bug: settle without dispatch claim → correct product refusal PermitInvalidError at admission.py:8231); 05 focused 7 passed; 06-10 regressions 15/16/10/3/63; 11 ruff fix → 12 "All checks passed!"; 13 2 files → 14 pristine HEAD copy also needs reformat (pre-existing) → 15 new test clean → 16 `ruff format --diff` on admission.py: 21 hunks, grep for margin_pool/AccountResourceKey/resource_* = 0 (zero new-code hunks; deliberate non-reformat avoids foreign lines); 17-20 finals 7/15/16/10 green. Fixed-source r1 (…-fixed-source-20261016-r1): pytest_exit 1, 113P/1F = test_public_version_matches_project_metadata FileNotFoundError (candidate pyproject.toml absent; source_count 143) — candidate-build defect, retained as process evidence. r2/r3: pytest 114/114 exit 0 but pin-stale (root f308c8d8; r2 risk 089b5efd) — formally invalid, retained as process evidence. Only r4 carries the current pin and is the cited evidence. Correct handling per instructions.

### 7. Pyproject floor adjudication (commit instruction) — change REQUIRED, exact text below
Determined from git history (no guessing):
- bt_api_base `src/bt_api_base/account_protocol.py` does not exist at v0.15.5 tag b3726c8 (= live tag max) nor at 801dabf (version 0.15.6).
- Commit 962c0f5 ("feat(base): define bounded provider account observations") introduces `account_protocol.py` with `class AccountResourceKey` (product_type/margin_pool, to_dict, from_dict) and `_version.py`/pyproject = 0.15.7. The class body at 962c0f5 is byte-identical to live 0.15.8. So the API risk uses (constructor kw, attributes, from_dict) first exists in 0.15.7; effective floor = 0.15.7.
- Live base HEAD version = 0.15.8. Root pyproject.toml already requires `bt_api_base>=0.15.8` (line 16) plus extras `>=0.15.8` (lines 266, 269) — root needs no change. There is no uv workspace/member mechanism in the root pyproject; nothing else forces a floor for standalone risk installs.
- risk pyproject.toml line 29 currently: `    "bt_api_base>=0.15.5,<0.16",` — admits 0.15.5/0.15.6 which lack the module → module-level import `from bt_api_base.account_protocol import AccountResourceKey` (admission.py l.36) would raise ImportError at import time.
EXACT MINIMAL CHANGE SET for the commit round (single line, one file):
- File: D:\bt_api_py\bt_api\bt_api_risk\pyproject.toml
- Old: `    "bt_api_base>=0.15.5,<0.16",`
- New: `    "bt_api_base>=0.15.7,<0.16",`
Conservatism note: base repo tags stop at v0.15.5 (0.15.6-0.15.8 untagged in-repo; offline cannot confirm PyPI publication of 0.15.7). `>=0.15.7` is the provable minimal floor from git; if publication evidence for 0.15.7 is unavailable at commit time, the fully-safe alternative is `>=0.15.8` (matches root floor and the only locally verified artifact). Either way the same one line changes. The metadata test (`tests/test_durable_risk_gate.py:850`) reads only the `version` field, so this edit is test-neutral; pytest runs (QA harness) use `-c qa-harness/pytest.ini` + PYTHONPATH and never consult pyproject, so r4 evidence validity is unaffected.

### 8. Honesty — PASS
- dim03-status.md explicitly: "No formal PASS is claimed" (l.58-59); increment limited to the margin-pool sub-item.
- Remaining-gap list present and accurate (l.50-56): (1) concentration absent from durable admission; (2) FX/Greeks freshness not validated inside the pool path, mapper-notional reliance, non-zero quote-currency fee conversion unproven; (3) filtered-snapshot vs full-account distinction unverified for this path; (4) NO collateral-sharing detection and NO single-admission-owner arbitration — untouched (two AccountKeys can declare the same pool; only a shared configured limit competes); (5) mapper does not populate `resource` (synthetic-only, confirmed by grep: no other producer); (6) pyproject floor caveat. All corroborated by my independent reading. Status narrative numbers all matched my recomputation (diffstat, hashes, run-log tallies, r1-r4 outcomes, ruff runs).

## D-register (disclosures the commit message must carry)

- D1 Scope: only two DIM03 files are in this round — `src/bt_api_risk/core/admission.py` (sha 28b9918d…) and `tests/test_margin_pool_reservations.py` (sha 9b92ac63…). FOREIGN files to keep unstaged/untracked: risk repo — `tests/test_trade_identity_claims.py` (DIM01, c0d977ce…), `bt_api_risk/.codex-tmp/` scratch (DIM04 areas); parent repo — modified `bt_api_py/service_registry.py`, `bt_api_py/service_runtime.py`, `tests/unit/test_load01_hundred_strategy_30min.py`; untracked `.codex-tmp/`, `%SystemDrive%/`, `0`, `bt_api_py/service_group_monitor.py`, `docs/迭代计划/…risk-order-exposure-sdk-round-r20.md`, `tests/integration/_arb04_group_reply_loss_support.py`, `tests/integration/_group_policy_host_fixtures.py`, `tests/integration/test_arb04_group_reply_loss_service.py`, `tests/integration/test_arb06_group_monitor_relay_service.py`, `tests/unit/test_service_risk_order_journal_decimal_match.py` (foreign files were still appearing during this review — concurrent workstream). Parent gitlink update to the new risk commit is the ONLY parent change in this round.
- D2 Packaging: include the pyproject floor line change (checklist 7) in the same round; note it is packaging-metadata-only and not exercised by the 114-test QA (version field unchanged → metadata test unaffected).
- D3 Evidence class: synthetic SQLite only; mapper does not populate `resource`; no real-account evidence for this increment; synthetic cannot substitute for real-account acceptance (per project rules).
- D4 No formal PASS; remaining gaps 1-4 of clause DIM03 stay open (concentration, FX/Greeks freshness in pool path, filtered snapshots, collateral-sharing/single-owner arbitration).
- D5 Defensive-only branches: pool-level NOTIONAL_UNIT_MISMATCH and MARGIN_POOL_UNIT_USAGE_UNAVAILABLE are unreachable via the normal API sequence (guarded by earlier unit assertions / install-time invariants); they guard foreign/tampered DB rows. Untested; test coverage gaps also include keyed SETTLED-row retention and DB-level old-format round-trip.
- D6 Version semantics are replay-protection, not monotonic ordering; same-facts idempotence is scale-sensitive (300 vs 300.0 refused) — fail-closed. Pool accounting treats ACTIVE+SETTLED as occupying and frees credit on release/expiry, mirroring `_assert_increase_limit` semantics.
- D7 QA provenance: r4 (spec 085587e8…/result 7826dd8f…/junit 56d92231…) is the cited fixed-source evidence; fixed-source r1 (candidate pyproject omission, 113P/1F), r2/r3 (pin-stale: root f308c8d8, r2 risk 089b5efd) and live-run 04 (test bug) are retained process evidence, not final-state failures. Candidate tree contains unpinned `__pycache__` copies (44 base + 8 risk .pyc) — all 144 pinned sources verified byte-identical; harness hygiene note only.

## Concurrency observations
- Another workstream modified the parent worktree and the risk `service_component.py` history mid-QA earlier today (r2 rejection); during this review additional foreign untracked files appeared in the parent tree. Re-verify `git status` at commit time and stage ONLY the two submodule files (plus the pyproject line) before committing; then update and commit the parent gitlink alone.

## Verdict

All 8 checklist items PASS; disclosures are non-blocking; the round claims no formal PASS and the gap list is accurate.

Verdict: GO
