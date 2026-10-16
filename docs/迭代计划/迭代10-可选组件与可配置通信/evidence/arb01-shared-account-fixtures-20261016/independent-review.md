# ARB01 iteration-10 review status

- Status: complete — r2 artifacts re-verified, r3 commit-time re-run executed at parent HEAD 46697739.
- Date: 2026-10-16 (r3 finished_utc 2026-10-16T02:43:58.928701+00:00).
- Scope: ARB01 increment only — uncommitted change to tests/unit/test_service_account_integration.py (shared-account fixture parameterized into 3 nodes). Other dirty workstreams were not touched, staged, or committed.

## Verdict: GO

### Static review (worktree vs HEAD 466977392985ef1d7fa9b282a220096c8d19dff8)
1. Diff (unstaged only; `git diff --cached` empty): blob 088f091b -> b6136b32, 150 insertions / 22 deletions; whitespace-insensitive diff identical (no hidden whitespace edits).
2. All 22 deletions are parameter substitution or test-body extraction; only 2 removed assert lines, both re-added parameterized (working lines 927-928). Body `_exercise_shared_account_variant` (lines 809-1077) preserves baseline asserts verbatim and adds stronger ones.
3. Three nodes (lines 1080, 1092, 1107) cover: default two-account sharing; cross-exchange (exchange-alpha/beta, lines 946-948); same-venue shared instrument identity (lines 951-954). Retained: registration set/uniqueness (915-917), two strategies share one account-a snapshot with per-host connect/read counts (920-936, 999-1003), wrong-account DENIED registration (974), unauthorized route-b denial (956-957), unknown CURVE key REQUEST_NOT_SENT (976-997).
4. New assertions substantive: ambiguous unqualified call on two same-mount bindings rejected `AMBIGUOUS_OR_MISSING_ROUTE` with runtime healthy (client 618-624; body 959-962); one active provider upstream per account key (1005-1014); connect-close balanced after close (1073). 12 added assert lines net.
5. No skip/xfail/monkeypatch in the file (grep: zero matches). Test-only, in-scope.

### R2 integrity (`D:\temp\it10arb01-20261016-r2`, spec `.codex-tmp/it10arb01-20261016-r2-qa/focused-qa.json`)
6. result.json matches artifacts: stdout_sha256 d1c68d9aebc257c481e72b71b05180756ab58eeb695264c040e136255cfaf135 == recomputed sha256 of stdout.log; stderr sha == empty-file hash; exit.txt = "0\r\n".
7. junit.xml: tests="3" errors="0" failures="0" skipped="0"; testcases are exactly the 3 acceptance nodes.
8. All 37 source_sha256 pins match live bytes (0 mismatches); r2 output spec.json byte-identical to R2SPEC (sha256 5fd58f735a2416227db144006c2d8cf9f0c121d7a8a2d4b5b37260ff7f72f12e).
9. R2 head anchor 5ff3da11... stale vs live 46697739... by design; heads_before==heads_after internally consistent.

### Fresh r3 commit-time re-run
10. Spec `.codex-tmp/it10arb01-20261016-r3-qa/focused-qa.json` (sha256 a0cafbf4198c0f77a82fdd4d4348aae5de71345d9a2929b033985bbfdeb5c8b5); output `D:\temp\it10arb01-20261016-r3` (did not exist before run); parent repo pin 466977392985ef1d7fa9b282a220096c8d19dff8; subrepos b4aabd1e / 59b164ab / a397fdb4 as in R2SPEC; 37 pins unchanged. Only metadata change beyond output/repos: artifact_id r2->r3.
11. Runner exit 0; result.json pytest_exit 0, 3 passed, sources_unchanged=true, heads_unchanged=true, stdout_sha256 aa9329cd2bb278e9905328ad6b11e0427d67dbfb70a30ed841068e2e79ad62fb (recomputed match); result.json sha256 7215f4d6bca6e1a9251a0ca0d78d52758fc8a79512f2adbe9b1b2c1c7fa29a29.
12. Fresh junit.xml: tests="3" errors="0" failures="0" skipped="0" (sha256 e72d31a0d17b583a44b892f01db44a1c6de1ade084337a0a38d96b4f6191bd18); stdout.log "3 passed, 1 warning in 21.12s"; exit.txt "0\r\n"; source-before.json == source-after.json (87ad8820b9d5b6824c5da40260b277ee992b6cb243c4e8f21d43d0298e4c1d4a).
13. Heads after run unchanged (parent 46697739..., subrepos as above); all four repos on branch dev.

### Findings / caveats (non-blocking)
14. Fixture rework is substantive, not weakened: default variant reproduces the baseline fixture exactly (instrument-account-a/SYNTH-ACCOUNT-A etc.).
15. "行情订阅不因账户数重复" is not literally asserted in this file (no market-subscription surface; grep zero matches); the non-duplication surface here is one active account upstream session per account key with exact provider read counts regardless of reader count.
16. The "before any wire or provider activity" claim for the ambiguous route is supported by unchanged provider read-count assertions (1001-1002, 1052, 1061), not a dedicated counter.
17. Pre-existing pytest warning (unknown config option asyncio_default_fixture_loop_scope) appears in all runs; unrelated to the change.
