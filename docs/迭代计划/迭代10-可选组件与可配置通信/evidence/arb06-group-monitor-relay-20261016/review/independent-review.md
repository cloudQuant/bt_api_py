# ARB06 independent review — SDK group-monitor relay (iteration 10, gap #4)

Reviewer: independent read-only audit, 2026-10-16. Scope: `bt_api_py/service_group_monitor.py`
+ `tests/integration/test_arb06_group_monitor_relay_service.py` as untracked commit candidates,
author status `.codex-tmp/arb06-status.md`, evidence `D:/temp/it10arb06-20261016-r1/`.

## Verdict: GO-with-disclosures

The two files reproduce the claimed hashes, the raw evidence logs confirm every claimed
pass/fail count and exit code, the module's read-only / CAS / anchor / fail-closed design
matches the claimed contract, and the test drives the real wire with no mocks. Commit is safe
ONLY with the explicit stage/commit command in Finding 7. Disclosures in Findings 8-10 must be
carried into the commit message or a follow-up ticket; none is a blocker for this increment.

## 1. Hash parity (recomputed)

- `bt_api_py/service_group_monitor.py`
  sha256 = 41ee954cacf970d3acfb1889f4dba84c662a8b0b2f391c5dd28031aefb7ed27b  (matches claim)
- `tests/integration/test_arb06_group_monitor_relay_service.py`
  sha256 = f939f3e6bf820cefd355f1b263856a3060d62f13b5376bac1263d107b5691345  (matches claim)

Line counts confirmed: 1524 / 727 (status claims 1524 / 727).

## 2. Evidence integrity (raw logs checked, status file not trusted)

268 files under `D:/temp/it10arb06-20261016-r1/` (basetemp fixtures included). All were hashed;
aggregate `sha256(sum of sorted per-file sha256 lines) = 022aeb66ee022d8062fe734045ddcee664e7a50c24286de41be5c9aa20e1b327`.
Top-level control files (cmd.txt / stdout.log / exit.txt), recomputed sha256:

| file | sha256 |
|---|---|
| run1-baseline-monitor/cmd.txt | fba385375becfe58927f0aedbc482f8ceb5e1a852637a84c1cca3a1bbfde7c2e |
| run1-baseline-monitor/stdout.log | c91af4c1c6114754ee6a14188378cf2c780e9b80c4a3fbb8d45dd242a1d83a7f |
| run1-baseline-monitor/exit.txt | 9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa |
| run2a-collection-error/stdout.log | 63318419d3b191f89adcabfbd4437bccaf9f80dc6da01e80de029ae946927c01 |
| run2a-collection-error/exit.txt | 5dd689a3eb3a56c97ce8bc45648b5805c1946fa684e1c6396bc7e9e1e790f5a3 |
| run2-arb06-node/cmd.txt | 6efdbeb75f65855185a198063ccb4f9b3c5b59841cfd14c86550696aabc45797 |
| run2-arb06-node/stdout.log | 263f36cc56ad23ea1bee8edb9f7fe108fae820ea46e53ae8eaf9587f50da8251 |
| run2-arb06-node/exit.txt | 418a5c17f33c70e99b0cc0a07fce69191489cfedc94164bfa903785777c5bd4b |
| run3-arb06-node/cmd.txt | 9e3785f7448890a9781135901728056f3ed9597114f6381137f1c21e7235eacf |
| run3-arb06-node/stdout.log | 50e2e059c1d34e54bbe5cd9d277c6b181ffae00fdf6d9543045ea921a8d10489 |
| run3-arb06-node/exit.txt | 418a5c17f33c70e99b0cc0a07fce69191489cfedc94164bfa903785777c5bd4b |
| run4-regression-monitor/cmd.txt | 1f571b5b9820f9ff54c22d6a7fd8f52cc1ca7b0bb1dbcc1195bd5fa905c06324 |
| run4-regression-monitor/stdout.log | ea0180f5846476f2442dfb2e38d42f3320e99aa198315e3db51ed19125b95d25 |
| run4-regression-monitor/exit.txt | 418a5c17f33c70e99b0cc0a07fce69191489cfedc94164bfa903785777c5bd4b |

(`run2a` intentionally has no cmd.txt; only stdout/exit exist — cosmetic.)

Claims vs raw logs — ALL CONFIRMED:
- run1 baseline: `25 passed`, exit 0 (2-byte "0") — pre-change canary.
- run2a: collection ERROR `TypeError: unsupported operand type(s) for |: 'tuple' and 'set'`
  at `bt_api_py\service_group_monitor.py:85`, exit 2 — failure honestly preserved.
- run2: `1 passed`, exit "EXIT=0"; run3 (fresh basetemp): `1 passed`, exit "EXIT=0".
- run4: `25 passed`, exit "EXIT=0" (same two monitor unit files as run1).

Timeline consistency (mtimes): module 11:38:38 (after run2a 11:38:32, i.e. fix #1 applied
after the recorded collection error); test file 11:41:07.7; run2 started 11:41:10, run3
11:41:25, run4 11:41:42 — both green node runs and the regression executed AFTER the last
file edit, on the exact current file contents (hashes above).

## 3. Module design vs claimed contract

Read fully (1524 lines). Contract items verified with evidence:

- Read-only source: `SqliteGroupResidualReader` opens `file:...?mode=ro` URI and asserts
  `PRAGMA query_only` == 1 (`service_group_monitor.py:283, 397-408`); every SQL statement is
  parameterized (only fixed int constants are interpolated, `:1207-1230`); no INSERT/UPDATE/
  DELETE toward the source anywhere. Checkpoint is a separate file; same-file/hardlink/symlink
  rejection via path + `lstat` dev/ino + `os.path.samefile` (`:1161-1182`).
- Own checkpoint DB with CAS cursor: `relay_checkpoints` keyed `namespace_id`, `BEGIN
  IMMEDIATE`, `UPDATE ... WHERE sequence = expected`, `total_changes == 1` check
  (`:1293-1401`); exact-duplicate ack path (`:1366-1370`); namespace = sha256(consumer_id +
  generation + stream + projection_version) (`:1129-1144`).
- Anchor re-verification: pre-delivery anchor of the stored cursor (`:739-741`, `:869-882`)
  and post-delivery re-check of the just-delivered row before the local ack (`:772`, `:884-896`).
- Identity fail-closed: reader generation + path + (st_dev, st_ino) checked at construction,
  before/after each page and around each poll (`:338-339, 370-371, 820-834`); any change =>
  `SOURCE_*_CHANGED`, never a skip.
- Opt-in `poll()` only: no thread, no `maintain` hook, no order-path touch; `limit` bounded by
  `_MAX_POLL_LIMIT=128` (`:720-725`); pages bounded at 100 (`:58, 746`); response counts only
  confirmed deliveries (`:152-170`).
- Durable event contract: `event_type="group.residual"` (`:74`, `:1036`); `data["schema"] =
  "sdk.group_monitor_projection.v1"` with exactly the claimed fields (`:1000-1039`);
  id `service-event:<sha256>` as a pure function of {schema, generation, portfolio_ref,
  group_id, source_event_sha256} (`:1109-1126`); `occurred_at = created_at_ns/1e9` (`:1038`).
- Wire scope: ingress scope validation rejects `account_refs`/`group_id`/`leg_id`/
  `manual_book_ref`/`run_id` (`:857-866`); per-event checks portfolio equality, strategy
  equality, `account_ref in account_refs`, else `SCOPE_MISMATCH` (`:986-998`).
- Bounded resources: checkpoint records <= 4096, DB <= 2048 pages, page field bytes <= 2 MiB,
  bounded browser buffer (`:57-65, 358-359, 744-749, 1335`). No wall-clock in logic.
- SQL injection: none — all where/value paths parameterized; identifiers never interpolated.

Cross-package writer contract verified independently:
- Digest formula recomputation in the module (`:481-490`) is byte-identical to the execution
  writer's own recomputation `payload_sha256({group_id, event_type, payload, created_at_ns})`
  (`bt_api/bt_api_execution/src/bt_api_execution/group_store.py:712-716`).
- Source immutability holds by schema: `sequence INTEGER PRIMARY KEY AUTOINCREMENT`,
  `UNIQUE(portfolio_ref, group_id, revision)`, UPDATE/DELETE triggers RAISE(ABORT)
  (`group_store.py:159-190`).
- Monitor ingress re-derives the durable outer `event_id` from identity+scope+outbox+
  `source_event_id` (`bt_api/bt_api_monitor/src/bt_api_monitor/service_ingress.py:171-186`);
  the relay's projected id is preserved and asserted as `data["source_event_id"]` — so the
  test's `startswith("service-event:")` on the outer id plus exact equality on
  `source_event_id` (test `:628-633`) is the correct, complete pin of the claimed identity.

## 4. Test review vs claims

- Real three-host cluster: `_GroupCluster` spawns two account hosts + coordinator host and a
  strategy client runtime (`tests/integration/test_arb03_dim05_group_partial_fill_service.py:1237-1297`;
  submit goes through `strategy_runtime` over ZMQ, `:1478-1480`); the node adds a spawned real
  Monitor host (ServiceHost + TrustedServiceAuthority + DurableOutbox + MonitorIngressTarget,
  test `:186-255`) and a spawned relay process running the real `ServiceGroupMonitorRelay`
  over real ZMQ CURVE (`:258-413`, keys generated in-test `:468-469`).
- No mocks anywhere in the new test (grep for mock/patch: none); real `SqliteOrderGroupStore`,
  real outbox, real ingress, real relay.
- Assertions cover: durable `RECOVERY_REQUIRED`/`hedge_residual_unresolved`/residual 3 setup
  (`:509-514`); independent reader probe generation/schema 2/row digest/revision/scope
  (`:552-570`); poll counts `(1,1,1,1)` + cursor digest (`:608-617`); exactly one durable row
  with `event_type`, `scope`, `producer.principal_ref`, projected id, `occurred_at`
  (`:619-633`); full payload dict equality incl. all 18 projection fields (`:635-658`); raw
  `data_json` row pinning list-form `account_refs` (`:660-678`); idempotent second poll no-op
  (`:680-692`); cursor/fingerprint/template-scope reply (`:694-704`); read-only proof — group
  snapshot object equality and exactly one dispatch record per provider (`:706-716`);
  normal close, zero forced/fallback cleanup (`:718-727`).
- Occasional thinness: the durable row's outer `event_id` is only prefix-checked — acceptable,
  see Finding 3 cross-package explanation.

## 5. Stage safety (critical — shared worktree with other active agents)

- Commit candidates are EXACTLY: `bt_api_py/service_group_monitor.py` and
  `tests/integration/test_arb06_group_monitor_relay_service.py` (both untracked, not ignored,
  `git check-ignore` exit 1).
- `git -C bt_api/bt_api_monitor status --short` => clean (submodule HEAD 536a4f0 unaffected).
- Attributed NOT to ARB06: modified `tests/unit/test_load01_hundred_strategy_30min.py`
  (unstaged), `tests/unit/test_service_config_scoped_execution_routes.py`,
  `tests/unit/test_service_host_integration.py` (appeared later, unstaged), dirty
  `bt_api/bt_api_risk` submodule, untracked `%SystemDrive%/`, `0`, `.codex-tmp/`, docs r20,
  arb04/support/combo3/decimal test files.
- LIVING WORKTREE OBSERVED: during the review HEAD moved `d85ec636` (author's claimed HEAD)
  -> `ed09b3ca` ("fix(registry): adopt released strategy write runs through durable re-claim",
  committing other agents' service_registry.py/service_runtime.py changes that were briefly
  STAGED in the index). A bare `git commit` at that moment would have swept those unrelated
  staged changes into an ARB06 commit. The index has since been observed empty again, but
  another staging event can occur at any second.
- Required commit procedure (pathspec form, never bare `git commit`):
  1. `sha256sum` both candidate files again (expect Finding 1 values).
  2. `git -C D:/bt_api_py add -- bt_api_py/service_group_monitor.py tests/integration/test_arb06_group_monitor_relay_service.py`
  3. `git -C D:/bt_api_py commit -m "<arb06 msg>" -- bt_api_py/service_group_monitor.py tests/integration/test_arb06_group_monitor_relay_service.py`
  4. `git -C D:/bt_api_py show --stat HEAD` must list exactly these two files, then
     `git status --short` must still show the other agents' entries untouched.

## 6. QA sufficiency

Existing evidence suffices; no new-source QA run is required before commit.
- Both green runs post-date the final edits (Finding 2 timeline), two independent node runs
  agree, regression on adjacent monitor tests is green, and the failed collection run was kept.
- Dependency content identity is provable: `service_registry.py` (mtime 11:22:11) and
  `service_runtime.py` (11:21:48) predate all runs and are now identical to committed HEAD
  `ed09b3ca` (clean working tree, empty `git diff HEAD`), so the run-time tree matches the
  current tracked tree; only the two candidate files differ from HEAD and their hashes are
  pinned. Belt-and-braces option only: rerun the run2 command once after any future touching
  of the two files.

## 7-12. Numbered findings (severities)

1. [INFO] Hash parity exact for both files (Finding 1). No staging done by author; claims in
   `.codex-tmp/arb06-status.md` about "git index untouched" unverifiable retroactively but
   consistent with observed history.
2. [INFO] Evidence integrity confirmed; run2a honesty confirmed; no over-claiming found in
   counts/exits.
3. [INFO] Design matches claimed contract for read-only, CAS cursor, anchors, fail-closed
   identity, opt-in poll, idempotent id, per-event scope checks; SQL fully parameterized;
   cross-package digest formula verified identical to the writer.
4. [MINOR] `_event_from_row` (`service_group_monitor.py:441-560`) verifies
   `request.portfolio_ref`/fingerprint but not `request.group_id == group_id` even though
   `GroupOrderIntent` carries `group_id` (group_order_contracts.py:457) and the writer derives
   the snapshot key from `request.group_id` (group_store.py:252,288). Defense-in-depth only;
   add the equality check when touching this code.
5. [MODERATE, disclose] Permanent SCOPE_MISMATCH stall on multi-scope journals. The reader
   query is not scope-filtered (`service_group_monitor.py:90-108`) and `_read_events` fails the
   ENTIRE poll on the first out-of-scope row of any fetched page (`:898-930` via
   `_validate_event` `:986-998`), without advancing the cursor. Consequence: the
   status/docstring mitigation "one relay per matching Monitor scope" (`arb06-status.md:116-118`,
   module docstring `:23-28`) does NOT work for a journal containing residual rows from
   several portfolios/strategies/accounts — every narrowed relay fail-closes forever at the
   first foreign row, and multiple narrowed relays all do so. Behavior is fail-closed with no
   data corruption and no silent skip (acceptable safety posture), but it is an availability
   constraint and the mitigation statement is inaccurate for shared journals. Only a wire
   scope that accepts every residual row of the journal works. Must be disclosed or the
   docstring/status corrected.
6. [MINOR, disclose] No unit tests for the new module's failure paths: CAS conflict, anchor
   divergence, corrupt checkpoint, scope mismatch, wrong thread are implemented but only the
   happy path + idempotent-hold path are exercised (integration node). The execution-monitor
   precedent carries its own unit suite; this module duplicates ~500 lines of that logic
   without carrying its tests. Acceptable for this increment; schedule as follow-up.
7. [MINOR, disclose] Generation from file identity has a migration edge: a v1->v2 journal
   schema migration performed by the writer (group_store.py:198-215) changes
   `generation` -> new namespace -> the same immutable residual rows are re-delivered under
   NEW projected ids (at-least-once redelivery after migration; Monitor dedupe cannot collapse
   them). Adding a journal incarnation id was forbidden for this increment, so the trade-off
   stands; worth one line in the follow-up.
8. [MINOR, acceptable-as-stated] Remaining gaps honesty: pair-payload-only end-to-end
   coverage (code-only `_mark_recovery` implemented, untested) — TRUE as stated; GROUP_PAIRED
   not relayed by design — TRUE (`_SOURCE_EVENT_TYPE` filter `:72,105`); one-relay-per-target
   fan-out — true but incomplete per Finding 5. Gap #5 (unwind/shared-account close) correctly
   left out of scope (gapmap ARB06 line: "组平仓属 P5A 关闭路径，另立大项").
9. [INFO] Hazards clean: no hardcoded absolute paths in either candidate (grep empty); no
   secrets (CURVE keys generated at test runtime); no leaked processes (the only two
   python.exe on the box are an unrelated uvicorn app from 2026-10-13); basetemp dirs remain
   under `D:/temp` by design of `--basetemp`; timing margins adequate (2.5 s between last edit
   and run2 start, 12-16 s runtimes, 15 s pipe waits); the concurrent-agent worktree is the
   main operational hazard (Finding 5 / stage procedure in section 5).

## Exact stage list

```
sha256sum bt_api_py/service_group_monitor.py tests/integration/test_arb06_group_monitor_relay_service.py   # expect Finding 1 hashes
git add -- bt_api_py/service_group_monitor.py tests/integration/test_arb06_group_monitor_relay_service.py
git commit -m "<arb06 msg>" -- bt_api_py/service_group_monitor.py tests/integration/test_arb06_group_monitor_relay_service.py
git show --stat HEAD            # exactly 2 files
git status --short              # other agents' entries unchanged; no staged leftovers
```

Do NOT `git add -A`, do NOT bare `git commit` (other agents' staged/dirty files exist and the
index changed twice during this very review).
