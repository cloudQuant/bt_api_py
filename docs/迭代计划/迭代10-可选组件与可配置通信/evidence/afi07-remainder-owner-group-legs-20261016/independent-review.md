# AFI07 remainder — independent review, iteration-10 acceptance round (r9 + independent r10fresh re-run)

Scope: verify the claimed AFI07 remainder acceptance (r9) and independently re-execute the frozen spec.
Read-only on repos; reviewer artifacts only under D:\bt_api_py\.codex-tmp\ and D:\temp\. No repo file modified, no staging/commits.

## Verdict: GO

## Verified evidence
1. Static diff (`git -C D:/source_code/ai-for-investor diff -- src/backend/tests/test_service_read_authorized_http_integration.py`):
   purely additive — numstat 1627 insertions / 0 deletions; single hunk `@@ -7051,3 +7051,1630 @@` appended after the r8
   node; file now 8680 lines (block = lines 7054-8680); working-tree sha256 6612a7bb9acc5e4d0b4d025600194bba2a9a7dda9a85e55534b8c4369cba5cc3 (matches claim).
   No skip/xfail/pytest.mark/TODO/FIXME in the added block. Monkeypatch use confined to harness hygiene (dotenv file probes,
   JWT settings, startup/shutdown noops, deployment JSON, RAG/quote service) — no bypass of owner/host/journal paths.
2. Substantive assertions present: exactly-once dispatch (`approved_calls` == 3 authorized tuples, plain `submitted == ()`,
   `unauthorized == ()`); replay returns same intent_id/provider_order_id; single owner thread/journal (SqliteExecutionStore
   ACKED records; 1 manual + 1 approved operation row each with owner_subject + operation_key sha256 -> intent_id; owner
   `thread_ident != main`); group-leg namespace rejection of ordinary cancel/submit with INVALID_REQUEST and reserved ids
   absent from journal; revocation disposition: unsafe release refused (ContractValidationError exact message, state/events
   unchanged), safe phases -> REJECTED_BEFORE_DISPATCH + budget RELEASED + budget_transition payload, durable across reopen
   and raw SQLite row check (REJECTED_BEFORE_DISPATCH/RELEASED/4, 5 events); group snapshot canonical-JSON byte-identical
   across operations (pre-close == post-close == reopened).
3. Production files untouched: modified set for this workstream is only the test file; `git status --porcelain` shows other
   workstream edits (README.md, scripts/*, plus untracked scripts/windows/{README.md,app.bat,app.ps1}) preserved untouched —
   none of their diffs mention "afi07" (grep exit 1); no untracked files under src/backend/app.
4. Artifact integrity: all 10 same-named archived qa/r9 files byte-identical to D:/temp/afi_iter10_approved_target_http_root_20261016_r9;
   authored-spec-r9.json has no same-named counterpart there — it is byte-identical to the output spec.json (sha256 b5e699fce5e51c9cefc75f3efeee6a97ff5c903c0d520dad61341bdb3331bcac).
   r9 sha256: result.json b433c7e8c4caf4cfd263f1b85de82773b20709e22d9eae92b3a3348fdf468372; stdout.log 27501f428f456745671601498c434706bc7f3e3c7db5d85b2145448184e64709 (== result.json stdout_sha256);
   junit.xml 575a82224f079fa39f589ec31285bf39b58534a1220eedde21c22caf8a44889d; exit.txt 13bf7b3039c63bf5a50491fa3cfd8eb4e699d1ba1436315aef9cbe5711530354 ("0").
   result.json: pytest_exit 0, sources_unchanged true (2147), heads_unchanged true 34f59117ac9a2e73f00b6908cfeeb11fa53f5610, stderr sha == empty-file sha; junit 5/0/0/0.
5. Pin re-hash: mechanical loop re-hashed all 2147 source_sha256 pins against live bytes — 2147 ok, 0 mismatch, 0 missing (REHASH_EXIT=0). Live ai-for-investor head 34f59117 on branch dev unchanged.
6. Runner provenance: qa/r9/runner.py byte-identical (cmp) to qa/r8/runner.py; argv = <runner.py> <spec path>.
7. Independent re-run: cloned spec at .codex-tmp/afi07-r10-review-fresh-spec-r10fresh.json (byte-identical except single `output`
   occurrence -> D:/temp/afi_iter10_approved_target_http_root_20261016_r10fresh, which did not exist). Command: `C:/anaconda3/python.exe
   "<qa/r9/runner.py>" "<clone>"` from D:/bt_api_py. Result: RUNNER_EXIT=0, pytest_exit=0, "5 passed, 2 warnings in 100.19s",
   junit 5/0/0/0 (same 5 node names), stdout sha256 bb0e849c762077ae9a61a5dae0dc05c78d619fbd41f236e930a5e51832ad1b71,
   stderr empty, sources_unchanged true (2147 pins), heads_before/after 34f59117 unchanged.

## AFI07 clause mapping (正式迭代计划.md line 799)
- "手工单、策略单及已批准目标经同一 owner": node 3 — HTTP manual (APP/SUBJECT_B) + HTTP approved-target (APP/SUBJECT_A) +
  direct SDK strategy (STRATEGY_CLIENT B) all served by one spawned owner child; all ACKED in one journal; account_ref shared.
- "两个 AFI worker 并发/重启重放同操作只派发一次；同键异内容拒绝": r8 regression nodes 1-2 (concurrent/restart replay; conflict
  assertions present file lines ~4080/4639/6269/6879) + new exactly-once counts in node 3.
- "审批摘要/版本/过期/撤销校验": r8 nodes (approval summary/version/expiry) + node 5 revocation checks.
- "已接受组撤销有处置结果": node 5 — REJECTED_BEFORE_DISPATCH + budget RELEASED, durable; unsafe release refused.
- "真实 HTTP/SDK/订单 journal 和合成 provider 计数": real ZMQ host subprocess + real ASGI HTTP + real ServiceExecutionClient;
  synthetic provider counts exactly 3 authorized dispatches.
- "持久操作键到 intent_id 映射、归属和最终事实": operation-row checks (key sha256, owner, actor/delegate, intent_id, ACKED).
- "普通操作不修改组腿": node 4 + node 3 — group-leg ids rejected for ordinary cancel/submit; snapshot byte-identical.
- Honest gaps (confirmed real, accepted as mission contingency, not regressions): revocation sub-item runs on
  SqliteOrderGroupStore/coordinator machinery directly (no HTTP->SDK->host path); two-leg same-owner group shows a single
  reservation phase (NOT_STARTED) because reservations dedup per account_ref — asserted with explicit test comment.

## Findings
1. Minor documentation nit only: status doc says block "~lines 7054-8663"; actual appended block is lines 7054-8680
   (1635-line hunk = 3 context + 1627 added). sha256 and content claims otherwise consistent.
2. No blockers, no weakened asserts, no collateral repo edits, no pin/head drift observed in either run.
