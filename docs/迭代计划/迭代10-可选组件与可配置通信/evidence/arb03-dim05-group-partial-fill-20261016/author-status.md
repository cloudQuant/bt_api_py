# ARB03/DIM05 gap-closure status (milestone 4: FIXED-SOURCE QA GREEN — runner exit 0)

Owner resume point: this file alone must let a successor continue. NO commits were made by this workstream. QA is complete; remaining work = parent/root adjudication of the recorded gaps.

## FINAL RESULT (fixed-source QA, official run)
- Runner: `C:/anaconda3/python.exe .codex-tmp/iteration10-focused-qa.py .codex-tmp/iteration10-arb03-dim05-fixed-source-r1/focused-qa.json`
- Exit code 0. Output dir: `D:\temp\it10arb03dim05-20261016-r2` (predecessor `-r1` untouched; `-r2` was fresh).
- result.json: pytest_exit=0, sources_unchanged=true, heads_unchanged=true, source_count=2336, stdout_sha256=`b0cfab732f22291ee6bf5d0a7ebc8f83cd1bd6e042873787272755301d84ff87`, stderr_sha256=`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (empty). result.json sha256=`0484337f216b2cd12c0d3dd1628502e98945158701989ddd994f7ad29b515b31`.
- JUnit: tests=2 failures=0 errors=0 time=29.514s; stdout tail "2 passed in 29.85s".
  - tests/integration/test_arb03_dim05_group_partial_fill_service.py::test_arb03_partial_cancel_first_leg_and_rejected_hedge_recovery (17.583s)
  - tests/integration/test_arb03_dim05_group_partial_fill_service.py::test_dim05_single_account_dual_contract_partial_fill_hedge_paired (9.724s)
- evidence_level = FIXED_SOURCE_SYNTHETIC_PROVIDER_ARB03_PARTIAL_CANCEL_REJECTED_HEDGE_RECOVERY_AND_DIM05_SINGLE_ACCOUNT_DUAL_CONTRACT_PAIRED_SCENARIO_ONLY
- Spec: `.codex-tmp/iteration10-arb03-dim05-fixed-source-r1/focused-qa.json` sha256=`425af923ca6494793bb2453e860b6015ac4cda893ee1d6a8d21d3901ae92fafe`. Candidate: sdk 2163 files, base 111, execution 27, risk 22, transport 7; qa-harness/pytest.ini = byte copy of r9 harness.

## Pins used by the QA spec (must match at run; verified before AND after, unchanged)
- D:/bt_api_py = `abf1cd562dcb5413924931b02f3318abc6e46a38` (dev). PIN MOVEMENT DOCUMENTED: task declared 466977392985ef1d7fa9b282a220096c8d19dff8; other workstreams committed during this session: 46697739 -> bb762465 ("feat(sdk): enforce trade-id domain isolation with fixed-source QA", touched service_risk_order_entry.py / service_risk_order_journal.py) -> 5702cdeb (docs MIG04 archive) -> abf1cd56 (docs SCN02 archive). The declared "must stay at 46697739" became physically impossible; no git write commands were run by this workstream. AFTER the QA run another docs-only commit landed (7766895300f929779a36855b6028290687f7fe12, "archive DIM06 CTP adapter-contract fixed-source QA evidence"); gitlinks unchanged; post-QA live re-check at 77668953: both nodes 2 passed (14.29s).
- bt_api_execution = dc8bd7a3682d00cfaef4f15261607043c6d24fca; bt_api_base = b4aabd1e17b9c7252fa7b4d266932087d99b5517; bt_api_transport_zmq = a397fdb460d2deb22e67ceb4963992ef63dbbb0e (all dev, clean).
- bt_api_risk = 089b5efd71ebd0398c73e9aab370d674d88b41ba (dev; HEAD moved from declared 131175a4 by another workstream). Its src carries an UNCOMMITTED +266-line edit in src/bt_api_risk/service_component.py from another workstream; the candidate captures that worktree state byte-for-byte (documented in the spec's source_resolution_policy). Runner pins only the HEAD commit.

## Live verification at this session's end
Command: `PYTHONPATH="D:/bt_api_py;...base/src;...execution/src;...risk/src;...transport_zmq/src" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 C:/anaconda3/python.exe -m pytest <nodes> -q -p no:cacheprovider --basetemp=<fresh /d/temp dir>`
- Both new nodes live: 2 passed (recheck 18.25s; QA dry-run against candidate 2 passed 23.74s).
- Existing greens (final): 6 tracked group unit files 55 passed + R121 tests/integration/test_service_execution_group_local_process.py = 56 passed combined (17.36s); tests/unit/test_service_group_policy_control_plane.py (untracked, other workstream) 18 passed.
- One transient note: an intermediate combined run showed R121 failing once; it passed when run alone immediately (10.52s) and on the combined re-run — attributed to concurrent system load/other agents' in-flight suite runs, not a reproducible regression. No action taken.

## Production edits (this workstream, owned files only; semantics verified against non-editable contracts)
1. bt_api_py/service_execution_group_proofs.py `_status_from_facts`: `reason = "provider_rejected"` (was uppercase "PROVIDER_REJECTED"). Frozen GroupChildOrderStatus/ProviderObservation contracts require `^[a-z][a-z0-9_]{0,127}$`; uppercase died with ContractValidationError -> facade mark_unknown -> coordinator order_outcome_unknown.
2. bt_api_py/service_execution_group_proofs.py `_resolution_projection` no-fill branch: `order_revision = status.order_revision` (was None). Coordinator compares resolution.order_revision vs status.order_revision; `_validate_source_event` requires the status to echo the provider revision even for zero-fill REJECTED, so the no-fill receipt must echo it too.

## New test edits (tests/integration/test_arb03_dim05_group_partial_fill_service.py)
- reason code expectations -> "provider_rejected" (2 places).
- HEDGE_LEG_SIZED payload quantity and hedge filled_quantity -> "3.000" (coordinator formats Decimal with quantity_step 0.001 scale) (3 places).

## Files changed by this workstream (sha256, current content)
- bt_api_py/service_execution_group_proofs.py sha256=`8a508aa99273a747ac720480e34dad5c6cae5cefb8a800a60680e11d36029462` (168450 B)
- bt_api_py/service_execution_group_authority.py sha256=`49fd1fbf347cfbd6b735aa58f9227aeb4d6c33c56a1a5338de92b085c6b69d88` (91923 B)
- tests/unit/_service_group_provider_fixtures.py sha256=`9bcf021649104243a2b16a93ef159ceca8cacce6ffad9c22afd94eed4ee3b1e2` (9484 B)
- tests/integration/test_arb03_dim05_group_partial_fill_service.py sha256=`a37f42a96424d663a9d5eb71d838d5c52538e0e800cc2e0f49de57c1a1b19afe` (73480 B)
- Builder: .codex-tmp/arb03dim05-build-candidate.py (artifacts above, not source).

## Semantics implemented (verified against non-editable code)
- Hedge sizing `_size_hedge` (bt_api_execution group_coordinator.py:693): qty with quantity_step 0.001 -> Decimal("3.000"); `_finish_hedge` (769) -> RECOVERY_REQUIRED + error_code hedge_residual_unresolved.
- Risk zero-fill REJECTED uses the separate no-fill terminal path (NO_FILL_TERMINAL_RESOLVED, admission.py:4440); positive path needs FILLED/CANCELLED.
- Proof chain for a terminal leg: ATTEMPT_STARTED,SOURCE_OBSERVED,PROOF_READY[,RISK_RESOLVED].

## Remaining gaps (for parent/root adjudication)
- Old-contract exit/roll path is NOT expressible through the coordinator contract (group legs are OPEN-effect IOC only) — documented gap, not simulated.
- bt_api_risk/base/transport are non-editable and were not modified; synthetic-provider evidence only; this is 2-scenario partial evidence, not full formal ARB03/DIM05.
- DIM05 evidence: one-account two-contract pairing only (no cross-account dual-contract variant).
- Live pins continued moving under other workstreams during this session; the frozen QA spec pins the exact verified HEADs above.

## Independent review (r2) — GO; full diff scope (added 2026-10-16 pre-commit)

Review file: `.codex-tmp/arb03-dim05-r2-review-status.md` sha256=`aba5ab74ba4bb2e760a560e4dc66d39e8c478d5268ac3796737df7dd2667c196`. Verdict: GO for the commit round; formal PASS for ARB03/DIM05 must NOT be flipped (both stay 部分).

Full diff scope (review D1 correction): the "Production edits" section above names only the two last-mile fixes, but the uncommitted diff is the complete IOC CANCELLED partial-fill support path:
- `bt_api_py/service_execution_group_proofs.py` +125/-25: source acceptance gate 0<filled<quantity; `_source_proof_kind` FILLED/CANCELLED_PARTIAL/REJECTED_NO_FILL; CANCELLED exposure proof; PARTIALLY_FILLED intermediate observation commit; `_status_from_facts` average price; terminal CANCELLED accepted with `status.filled_quantity` as expected filled; last-mile: reason-code lowercase + no-fill `order_revision` echo.
- `bt_api_py/service_execution_group_authority.py` +158/-15: `_assert_current_execution_partial` and two-provider_observation provenance chain.
- `tests/unit/_service_group_provider_fixtures.py` +13/-4; new `tests/integration/test_arb03_dim05_group_partial_fill_service.py` (2 nodes).
Reviewed as strictness-preserving (positive-partial gates; no regex edits; no removed checks).

D-register acknowledgements (all non-blocking): D1 fixed by this addendum; D2 PROOF_READY ordering is production-enforced only (no test-level assertion — do not cite as test evidence); D3 snapshot scope limited to the 4 owned files + 2 nodes; D4 risk gitlink must not be staged in this round; D5 pin movement abf1cd56->77668953 is one docs-only commit; D6 R121 transient failure author-attested only; D7 CFG06b's live edits to `service_components.py` / `service_execution_group.py` must not be swept into this commit.
