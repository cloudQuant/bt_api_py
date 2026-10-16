# Iteration 10 — Backtrader V2 recovery delta independent static review

Scope: read-only review of commit b002afa2da66ca6d14f1623280109be8e653a1b6
("Add restart-safe recovery quote journal and session callback projection for managed paper V2")
in D:/source_code/backtrader (branch dev), parent 48bde5d76bd666ce876763a613f27b92e9c62074,
10 files, +4115/-42. No repo files were modified; no test suite was re-run.

## Verdict: GO

The committed delta is internally consistent, fail-closed, and the delivery claims that were
checkable are accurate. GO is scoped to this static, pinned review; it does not certify runtime
acceptance (see item 6). Non-blocking observations are listed at the end.

## Findings

### 1. Blob integrity — PASS
- `git show --stat b002afa2`: exactly 10 files (8 modified, 2 added), +4115/-42.
- Parent recorded as 48bde5d76bd666ce876763a613f27b92e9c62074 (`git log -1 --format='%H %P'`).
- `git rev-parse b002afa2:<path>` for all 10 paths matches the reported prefixes exactly:
  btapibroker.py 40400d8760992fbb813f95764433226dd1763da3, btapifeed.py 78af714ec98647839946e70b7f35a29747e27529,
  service_recovery_quotes.py ee702ab8a46a7355475b8b8cb9fa0235a7ad0e92, service_execution_adapter.py 3f41527d0b6afa0b008914abf404b826d2b33a9d,
  service_execution_recovery.py 7825ed1cec1b7758679599545efa1b6d48d09c0b, service_execution_session.py a585d3bc90c5440e9f855879039417d2027c8d21,
  service_strategy_account_book.py 8dade01bc5b00183f7dd5cf483ce37cd0a5decde, strategy.py ab1bf0a5813014d3208050bd12b8ecf28e845bbc,
  trade.py 2f59ca3ec705b9497734408c392384036600611d, test_service_execution_recovery_v2.py 4cf4663ddaee5b621b3b3da76819495f3d6b192d.
- No other paths entered the commit (`git show --name-status` = the 10 files only). Working tree is clean
  for all 10 files, so HEAD blobs == worktree == r7-tested content.
- `.codex-tmp/...-r7/run.json` source_before and result.json source_after sha256 pins are equal to each
  other and (independently re-hashed) equal to the current files for all 10 paths.

### 2. Excluded paths — PASS
- `git diff --name-only 48bde5d7..b002afa2 -- <excluded>` is empty for
  backtrader_runtime/ctp_configured_front_check.py, backtrader_runtime/ctp_simnow_operator.py,
  tests/unit/runtime/test_ctp_simnow_config_operator_route.py, `0`, docs/_internal. Those working-tree
  modifications exist but stayed out of the commit (git status shows them separate).
- `0` is still tracked in both trees with identical blob bd87d6ce4d5ac6d73c17e899675a2903c82ab4ac; the
  staged deletion of `0` in the index/worktree is outside the commit.

### 3. Core design review — PASS (fail-closed; no blocking safety hole found)
- service_recovery_quotes.py (new): in-memory quote-to-bar binding mixin. Malformed observations are
  dropped (lines 72-93), callers asserting stale/duplicate revisions raise (137-144), only the latest bar
  record is retained (206-212, bounded growth), and the reader fails closed on any bar mismatch
  (214-245). Durability is not in this module: the trusted quote revision (source_ref/generation/cursor/
  instrument/as_of_ns) is persisted with the intent (service_execution_recovery.py:255-380) and re-checked
  at fill validation (`_validate_paper_fill_v2` 2625-2680, `_validate_recovery_event_context`); restart
  never re-derives it. The commit-message phrase "quote journal" is broader than this module alone.
- Schema/version handling: `_SCHEMA_VERSION = 3` (recovery.py:83); stored schema values 1/2/3 are accepted
  and upgraded, anything else raises RECOVERY_SCHEMA_VERSION_UNSUPPORTED (3210) — no silent fallback.
  Event protocol is pinned per logical run via `bind_event_version` (506) and page application raises
  RECOVERY_EVENT_VERSION_CHANGED on mismatch (1010) — a v1 run cannot be silently resumed as v2, and page
  version 1 carrying `paper_fill_v2` is rejected (invalid details).
- Consumer lease: all V2 journal mutations require an unexpired owner-token lease
  (`_assert_consumer_lease_cursor` 1351-1374; acquire/renew/release 1376-1448); acquisition refuses an
  active foreign token (RECOVERY_CONSUMER_ALREADY_ACTIVE), renewal/deletion are token-scoped. Session
  acquires before `initialize_recovery` (session.py:488-501) and releases in cleanup (session.py:528).
- ACK / exactly-once projection: broker queues exactly one notification per receipt per process
  (btapibroker.py:5112, 5200) and fences in-process re-application of an uncheckpointed fill
  (5144-5146); the strategy persists the Trade checkpoint BEFORE user callback/ACK
  (strategy.py:2751 `persist_recovery_trade_projection`, then append at 2767), and ACK happens only after
  `_notify_inner` succeeds (strategy.py:2951-2990). Crash mid-callback: receipt stays unacknowledged, the
  durable projection is READY, and the restart path hydrates the single Trade from the checkpoint
  (2256-2435) and verifies equality instead of re-executing (2670-2690, 2770-2790) — at-least-once user
  notification, no duplicated Trade/order execution.
- Failure handling is fail closed throughout: missing/uncertain context → fence
  (RECOVERY_INTENT_CONTEXT_REQUIRED 305, broker `recovery_intent_context` fence, resolver returning None
  → REPLAY_UNAVAILABLE), V2 accepts only MARKET/FOK (recovery.py:300-305, adapter reject branch), and the
  journal persists projection status atomically with the trade state (1209-1290).
- Shared-path preservation: all new behavior is opt-in — feed param default False (btapifeed.py:227, 269,
  857), session/adapter `recovery_event_version` default 1 (session.py:139-151, adapter wiring),
  trade.py marker-gated (trade.py:384-406; absent marker keeps legacy baropen/dtopen), broker
  `_apply_trade_update(position_override=None)` default preserves old callers, `_order_execution_dt`
  marker-gated (btapibroker.py:10567-10580), account book `account_key` remains an optional field.

### 4. Test substance — PARTIAL (claimed evidence accurate; coverage narrower than the commit)
- tests/unit/stores/test_service_execution_recovery_v2.py (631 lines, 12 tests) uses the real SQLite
  journal (no mock-only shortcuts) and genuinely covers: protocol pinning, v1-vs-v2 adapter request
  shapes, MARKET/FOK submit + LIMIT pre-manifest rejection, GTC without recovery, durable
  READY_TO_PROJECT rows, REPLAY_UNAVAILABLE without mapper, fee-model rejection, context/quote-time
  rollback, genesis-required, and a true fresh-object reopen verifying immutable intent context
  (lines 614-631).
- Gaps (non-blocking at this evidence level): no test anywhere exercises `persist_recovery_trade_projection`,
  broker V2 fill application, strategy Trade hydration, lease renewal/expiry, or callback ACK/duplicate
  replay — a repo-wide grep finds the new APIs referenced only by this test file. Crash-mid-callback and
  fresh-process replay are asserted by design review, not by tests. The r4 assessment does not claim
  otherwise (its hunk_scope lists exactly the 12 covered invariants).

### 5. Deselect claim — PASS
- r7 (`--noconftest`, plugin autoload off): 602 passed / 18 deselected / exit 0, stderr empty; run/result
  pins stable. The 18 deselect nodes in run.json match exactly, 1:1, the failing set of the baseline
  junit at parent 48bde5d76 (assessment-r4/baseline): 14 failures + 4 errors ("fixture bt_caplog not
  found" x4), baseline stdout "14 failed, 590 passed, 4 errors".
- Spot-checked parent failure reasons: pre-existing strictness/artifacts (RECOVERY_TRADE_BINDING_MISSING
  on v1 binding-less events, direct CTP store writes disabled, legacy adapter payload drift, missing
  conftest fixture) — not new regressions introduced by this commit.
- r8 (conftest enabled, the 4 fixture-error nodes): 4 passed, exit 0.

### 6. Honesty — PASS
- Delivery report and artifacts state evidence level LOCAL_SQLITE_BROKER_COMPONENT_ONLY and list
  "no ServiceHost, real provider/account, fresh-process recovery or full formal acceptance" as
  limitations; r4 assessment marks the 10-pin delta NOT_YET_REVIEWED; r6 (exit 1) is preserved as
  honest pre-trim evidence. Commit message does not assert acceptance.

## Authorization/safety holes
None found. V2 mutation paths are gated by an unexpired single-consumer lease plus a durable event-version
pin; unknown schema versions, mismatched event versions, unknown paper-fill shapes, lease loss, and
context/quote mismatches all raise instead of degrading to legacy behavior. The only intentional
relaxation (in-process retry of a READY receipt would trip the fail-closed
`recovery_restore_order_has_local_fills` fence at btapibroker.py:4996-4997) is unreachable while callback
exceptions abort the run, and fails closed if ever reached.

## Non-blocking observations
1. The session/broker/strategy callback-projection half of the commit (the larger half) has no dedicated
   test; the 602-passed run validates unchanged suites plus the journal/adapter-level V2 tests.
2. Three deselected nodes (recovery rebate/projection-callback, adapter snapshot-pages) exercise code the
   delta heavily modified; their parent-failure classification was not re-verified post-delta (instructed
   not to re-run). They already failed at parent, so no new regression is masked, but their eventual
   rework must account for the v3 schema.
3. service_recovery_quotes.py is in-memory; operators must not read the commit title as a durable
   feed-side journal — durability is provided by the SQLite intent-context/fill-projection rows.
4. The SQLite journal (projections, checkpoints, callbacks) has no pruning; growth is bounded by fills
   per strategy/run — acceptable now, worth a compaction story later.
5. `release_consumer_lease` (recovery.py:1440-1448) deletes without checking rowcount; stale tokens
   no-op, which is the safe direction, but silent.
6. r7 ran with `--noconftest`; the 4 conftest-dependent nodes are covered by r8 — acceptable but the two
   evidence sets should be read together.

Review method: commit diff and blobs read via `git show`/`git rev-parse`; working-tree line numbers cited
above; no test suite re-run; one read-only AST syntax check of the 10 files (all parse).
