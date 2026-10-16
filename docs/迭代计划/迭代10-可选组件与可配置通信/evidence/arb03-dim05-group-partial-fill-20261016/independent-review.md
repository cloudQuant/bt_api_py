# ARB03+DIM05 R2 fixed-source QA — independent static review (READ-ONLY)

Reviewer: independent static review agent. Method: re-hash + read only; no repository edits, no build/test rerun.
Review basis: QA dir D:/temp/it10arb03dim05-20261016-r2 (untouched), spec D:/bt_api_py/.codex-tmp/iteration10-arb03-dim05-fixed-source-r1/focused-qa.json, author status D:/bt_api_py/.codex-tmp/arb03-dim05-status.md, working tree at D:/bt_api_py @ 77668953.
Result headline: all hash-integrity and candidate-faithfulness claims reproduce exactly; both code fixes are real and required; evidence supports GO for the commit round with mandatory disclosures (Section 5). Neither ARB03 nor DIM05 qualifies for a formal PASS update (Section 4).

## 1. Hash integrity

1a. Raw outputs
- `cd /d/temp/it10arb03dim05-20261016-r2 && sha256sum stdout.log stderr.log` -> stdout.log = b0cfab732f22291ee6bf5d0a7ebc8f83cd1bd6e042873787272755301d84ff87 (== result.json stdout_sha256), stderr.log = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 (== sha256 of empty bytes).
- `wc -c stderr.log exit.txt` -> 0 and 3; `cat exit.txt` -> "0". pytest_exit=0 consistent.
- stdout content = "..  [100%] ... 2 passed in 29.85s" only; no hidden failures.

1b. junit.xml: tests=2 errors=0 failures=0 skipped=0 time=29.514. Both testcase names match the spec nodes exactly:
- tests/integration/test_arb03_dim05_group_partial_fill_service.py::test_arb03_partial_cancel_first_leg_and_rejected_hedge_recovery
- tests/integration/test_arb03_dim05_group_partial_fill_service.py::test_dim05_single_account_dual_contract_partial_fill_hedge_paired
Timestamps consistent: command.json started 2026-10-16T03:06:43Z, junit 11:06:44+08, result finished 03:07:15Z.

1c. `cmp source-before.json source-after.json` -> identical (both sha256 ce4414810a9c71023bb9362068ece9c1543b4b5ca691a69eef2ae0b09abb1cb6). sources_unchanged=true is therefore literally reproducible from the artifacts.

1d. Spec copy identity and heads
- `sha256sum spec.json ../../bt_api_py/.codex-tmp/iteration10-arb03-dim05-fixed-source-r1/focused-qa.json` -> both 425af923ca6494793bb2453e860b6015ac4cda893ee1d6a8d21d3901ae92fafe; QA spec.json == focused-qa.json byte-for-byte (and == status claim line 13).
- `sha256sum result.json` -> 0484337f216b2cd12c0d3dd1628502e98945158701989ddd994f7ad29b515b31 (== status claim line 8).
- spec.repositories == result.heads_before == result.heads_after (all five):
  D:\bt_api_py=abf1cd562dcb5413924931b02f3318abc6e46a38; bt_api_base=b4aabd1e17b9c7252fa7b4d266932087d99b5517; bt_api_execution=dc8bd7a3682d00cfaef4f15261607043c6d24fca; bt_api_risk=089b5efd71ebd0398c73e9aab370d674d88b41ba; bt_api_transport_zmq=a397fdb460d2deb22e67ceb4963992ef63dbbb0e.
- Runner.py (QA copy) == D:/bt_api_py/.codex-tmp/iteration10-focused-qa.py (cmp identical). The runner asserts spec sources and `heads_before == spec["repositories"]` before running (runner.py lines 26-32), so the spec-vs-heads equality is enforced, not merely claimed.

1e. Full re-verification (stronger than the requested 40+): recomputed sha256 for ALL 2336 spec source_sha256 entries against the files on disk right now -> 2336 ok / 0 missing / 0 mismatch. Area mix: sdk 2163, base 111, execution 27, risk 22, transport 7, qa-harness/pytest.ini 1, plus 5 live-worktree pins (4 owned files + the runner script). This simultaneously proves: candidate copies intact, spec self-consistent, and every pinned live worktree file (including the 4 owned files) still matches the frozen snapshot.

1f. Root HEAD / provenance of the frozen tree
- `git -C /d/bt_api_py rev-parse HEAD` -> 7766895300f929779a36855b6028290687f7fe12 (dev). Claimed value confirmed.
- `git log --oneline abf1cd56..77668953` -> exactly ONE commit: 77668953 "docs(iteration10): archive DIM06 CTP adapter-contract fixed-source QA evidence"; `git rev-parse abf1cd56^` -> 5702cdeb.
- Docs-only proof: for abf1cd56 and 77668953, non-docs files = 0 and gitlink (mode 160000) entries in `git diff-tree` = 0 (abf1cd56: 171 files; 77668953: 28 files; all under docs/). abf1cd56 itself is also docs-only (SCN02 archive). So the QA ran on tree abf1cd56, and the only post-QA movement is one docs-only commit with no gitlink change: provenance holds, and 1e re-verifies all pinned content even after that commit.

1g. Risk submodule dirty state (CFG06b WIP, not ARB03's)
- `git -C bt_api/bt_api_risk diff --numstat` -> `266  0  src/bt_api_risk/service_component.py` (+266/-0; the "+266 lines" claim is precisely correct).
- Worktree sha256 of that file = 384243756948aabd6ce7dcb6b43333afdbd2fc2f009eb5fd00c5278377dd9ecd == spec pin for candidate/risk/src/bt_api_risk/service_component.py -> the dirty state IS captured in the candidate manifest byte-for-byte.
- Ownership: .codex-tmp/cfg06b-impl-status.md line 8 explicitly assigns "EDIT bt_api/bt_api_risk/src/bt_api_risk/service_component.py — add GroupPolicyError/GroupPolicyStore/..." to the CFG06b workstream (phase 1). Not ARB03's edit; the spec's source_resolution_policy also documents this capture policy. Note: the runner pins only the risk HEAD commit (089b5efd), as the status file states.

1h. Harness byte-copy claim
- sha256 of qa-harness/pytest.ini (iteration10-arb03-dim05-fixed-source-r1) = 2776f7735f9bd3e0... identical to the arb02 fixed-source r2-r11 harnesses (incl. r9) -> "byte copy of r9 harness" verified.

1i. QA dir completeness: command.json, exit.txt, junit.xml, pytest-tmp/, result.json, runner.py, running.json, source-after.json, source-before.json, spec.json, stderr.log, stdout.log all present.

Section 1 verdict: PASS — every numeric/hash claim in result.json and the status file has been independently reproduced; no discrepancy.

## 2. Candidate faithfulness

- All 4 owned files: current worktree hash == candidate/sdk copy == author-claimed hash, byte-identical (sha256):
  - bt_api_py/service_execution_group_proofs.py = 8a508aa99273a747ac720480e34dad5c6cae5cefb8a800a60680e11d36029462 (claim 8a508aa9...)
  - bt_api_py/service_execution_group_authority.py = 49fd1fbf347cfbd6b735aa58f9227aeb4d6c33c56a1a5338de92b085c6b69d88 (claim 49fd1fbf...)
  - tests/unit/_service_group_provider_fixtures.py = 9bcf021649104243a2b16a93ef159ceca8cacce6ffad9c22afd94eed4ee3b1e2 (claim 9bcf0216...)
  - tests/integration/test_arb03_dim05_group_partial_fill_service.py = a37f42a96424d663a9d5eb71d838d5c52538e0e800cc2e0f49de57c1a1b19afe (claim a37f42a9...)
- These files are pinned TWICE in the spec: once as candidate/sdk copies and once at their live worktree paths; both pins still match, so the QA ran on material identical to what the commit round would commit.
- The candidate additionally captures other workstreams' WIP as part of the sdk snapshot (all pinned, all still matching): risk CFG06b service_component.py (Section 1g), CFG06b untracked tests/unit/test_service_group_policy_control_plane.py (33c76f13...), and modified tests/unit/test_load01_hundred_strategy_30min.py (82916661...). Neither node imports these; harmless for the evidence, but the snapshot is NOT "only ARB03's files".

Section 2 verdict: PASS.

## 3. Semantics audit (core)

3a. Fix (1) reason code lowercase — verified real and required
- Frozen contract: bt_api_execution/src/bt_api_execution/contracts.py line 24 `_SAFE_REASON = re.compile(r"^[a-z][a-z0-9_]{0,127}$")`; ProviderObservation.__post_init__ raises `ContractValidationError("invalid reason_code")` at line 1314. Additionally GroupChildOrderStatus (group_order_contracts.py:32 `_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")`, raise "invalid child reason_code" at lines 794-797).
- Without the fix, "PROVIDER_REJECTED" (uppercase P) fails ^[a-z] at the FIRST construction site — _status_from_facts (proofs.py:2649-2667) — with ContractValidationError; even if it got past, _provider_observation -> ProviderObservation (proofs.py:2669-2679) would die at contracts.py:1314. The REJECTED path is therefore unrepresentable without lowercase.
- Fix: proofs.py:2655 `reason = "provider_rejected" if state is ExecutionState.REJECTED else None`. No regex was widened anywhere (frozen validators untouched; both owned files' diffs contain no regex/fullmatch changes). The literal still satisfies the same pattern — validation is not weakened, and the same shape is enforced on the outbox payload by the frozen execution store path.
- Propagation: reason_code -> GroupChildOrderStatus (2664) -> ProviderObservation (2676) -> outbox payload asserted by the test: line 1637 `second.reason_code == "provider_rejected"` and line 1722 `events_b[3].payload.get("reason_code") == "provider_rejected"`.

3b. Fix (2) no-fill receipt must echo order_revision — verified real and required
- Fix site: proofs.py:1565-1569 (no-fill branch of _resolution_projection): `order_revision = status.order_revision` (+ comment), field passed to GroupDispatchResolutionReceipt at 1583.
- Without it (order_revision=None), the frozen coordinator comparison fails: group_coordinator.py:654-657 `status.order_revision is not None and resolution.order_revision != status.order_revision` -> _mark_recovery(snapshot, "GROUP_RISK_RESOLUTION_FACTS_MISMATCH") (line 660); the resolution receipt is then never recorded into the leg progress (630-677), so `leg_b.resolution_receipt` stays None and the test asserts (1638-1645) fail. Claim CLOSED.
- Why status carries a revision at all: _validate_source_event (proofs.py:2796, the "frozen source validator") enforces `status.order_revision != revision` -> GROUP_SOURCE_STATUS_INVALID at lines 3105-3108 when re-validating the persisted source event; _status_from_facts already writes facts["order_revision"] (2665). So the receipt must match it.
- No None escape: the frozen contract allows order_revision=None only for TERMINAL_NO_FILL_RESOLVED (group_order_contracts.py:1113-1123) and the coordinator skips comparison only when status.order_revision is also None (654-655). Change strictly increases fidelity (None -> the exact status revision when present); nothing removed.

3c. Assert traces to production code (adversarial check of test claims)
- provider_rejected (2 places) — traced in 3a. Test lines 1637, 1722. OK.
- "3.000" (3 places) — _size_hedge group_coordinator.py:697-703 builds the quantity as Fraction steps of quantity_step (0.001 in the test's _metadata line 373; hedge step 0.001) -> Decimal("3.000"), formatted with `format(quantity, "f")` at line 723 into the HEDGE_LEG_SIZED payload; test line 1661 (ARB03) and 1819 (DIM05) assert exactly "3.000". The hedge observation payload "3.000" comes from bt_api_execution/src/bt_api_execution/store.py:3149 `"filled_quantity": format(observation.filled_quantity, "f")` over the sized intent quantity; test line 1851. OK.
- RECOVERY_REQUIRED / hedge_residual_unresolved / residual 3 — _finish_hedge group_coordinator.py:783-796: residual = first_base - second_base; state RECOVERY_REQUIRED + error_code "hedge_residual_unresolved" at 794-796. ARB03 numbers: 1.5*2 - 0*1 = 3 -> test asserts 1595-1597; DIM05: 1.5*2 - 3*1 = 0 -> GROUP_PAIRED/PAIRED (786, 794) -> test 1763-1765, 1816. OK.
- Risk zero-fill REJECTED no-fill terminal path — cited line exact: bt_api_risk/src/bt_api_risk/core/admission.py:4440 `status=DispatchTerminalResolutionReceiptStatus.NO_FILL_TERMINAL_RESOLVED,` (the DispatchTerminalResolutionReceipt construction). OK.
- DIM05 整组一次预留 — test asserts len(reservations)==1 with 2 legs and both risk legs (claimed, resolved) — lines 1767-1769, 1855-1859. OK.
- PROOF_READY ordering — NOT directly asserted in the test: grep "PROOF_READY|ATTEMPT_STARTED|SOURCE_OBSERVED|RISK_RESOLVED" in the test file returns ZERO hits. The chain is production-enforced (proofs.py allowed sequences 1646-1652; append sites 2235/2697/3437/3474) and only implicitly exercised (resolution receipts require the guard). Adversarial correction: any wording implying test-level PROOF_READY ordering evidence is wrong; it is production-level only.

3d. Diff-scope discrepancy (documentation, not semantics)
- The status file's "Production edits" section names only the two fixes, but the uncommitted diffs are much larger: proofs.py +125/-25 (317 diff lines, ~16 hunks), authority.py +158/-15 (239 diff lines), fixtures.py +13/-4. The bulk is the IOC remainder-cancel (CANCELLED with strictly positive partial fill) path: source acceptance gate 0<filled<quantity (proofs.py ~2270-2300 and 3061-3065), _source_proof_kind FILLED/CANCELLED_PARTIAL/REJECTED_NO_FILL (~1958-1972), CANCELLED exposure proof + OrderExposureState.CANCELLED (~3128-3290), PARTIALLY_FILLED intermediate observation commit (~2290-2312), _status_from_facts average-price for PARTIALLY_FILLED/CANCELLED (2634-2650); authority _assert_current_execution_partial (~373-470) and two-provider_observation provenance chain (536-620). Reviewed: additions are strictness-preserving (positive-partial gates everywhere; exact tuple equality retained for event chains; no removed checks beyond an equivalent len+tuple check collapsed into tuple equality; no regex edits). The commit and status update must describe this full scope, not only the two last-mile fixes.

Section 3 verdict: PASS on semantics (both fixes real, required, non-weakening; all asserts traced) with correction 3c (PROOF_READY) and disclosure 3d (diff scope).

## 4. Clause mapping (iteration plan, verbatim)

- Line 769: `| ARB03 | 首腿零/部分/完整成交，第二腿拒绝/部分成交；按实际 base 数量及合约步长对冲，补偿耗尽保留未解决状态 | 真实组状态机和账户入口，合成 provider 注入；同时核对订单、费用、占用与剩余敞口 |`
- Line 777: `| DIM05 | 一个账户两个合约的跨品种/跨期组，整组一次预留，部分成交及旧合约退出恢复 | 真实状态库/账户入口；两腿共用资源池且不重复扣减或超卖 |`

ARB03 sub-item mapping:
- 首腿部分成交 -> COVERED by this QA (both nodes: CANCELLED 1.5/2 with TERMINAL_EXPOSURE_RESOLVED, avg price, fee -0.05, order_revision 1 asserted).
- 第二腿拒绝 -> COVERED by this QA (REJECTED hedge -> recovery; reason provider_rejected).
- 按实际 base 数量及合约步长对冲 -> COVERED (3.000 sizing; base_units_per_order_unit 2/1; quantity_step 0.001).
- 补偿耗尽保留未解决状态 -> COVERED for this scenario (RECOVERY_REQUIRED + hedge_residual_unresolved + residual_base_units 3).
- 首腿零成交 -> NOT in this QA. Repo test tree spot-check: tests/test_runtime_fake_dispatch_journal.py:209 references NO_FILL_TERMINAL_RESOLVED; risk admission.py:4440 no-fill terminal path. No group-level fixed-source zero-fill node found. PARTIAL.
- 首腿完整成交 -> NOT in this QA. Strongest local evidence: unit test_service_execution_group.py:1235 (two filled legs from source-backed provider facts) and previously accepted R121 integration (evidence/group-three-host-service-integration-20261016, two legs FILLED/PAIRED). PARTIAL (outside this QA's fixed-source pin).
- 第二腿部分成交 -> NOT found in this QA nor in spot-checked unit/integration group tests. MISSING within current evidence set.
- Vehicle (真实组状态机+账户入口，合成 provider；核对订单/费用/占用/剩余敞口): satisfied by this QA (spawned real hosts over ZMQ Curve, real SQLite stores/journals, synthetic provider; asserts order records, fees, reservations, holds, residual exposure).

DIM05 sub-item mapping:
- 一个账户两个合约 -> COVERED (one account; spot BTC-USD + perp BTC-USD-PERP).
- 跨品种 -> COVERED; 跨期 (dated expiry leg) -> NOT covered (perp is not a dated contract). PARTIAL.
- 整组一次预留 -> COVERED (1 reservation / 2 legs / both risk legs resolved).
- 部分成交 -> COVERED (partial first leg sizes hedge).
- 旧合约退出恢复 -> MISSING — explicitly documented as not expressible through the frozen coordinator contract (group legs are OPEN-effect IOC only; test docstring lines 14-16; status gap 1).
- Vehicle (真实状态库/账户入口；两腿共用资源池且不重复扣减或超卖): satisfied for the covered sub-scope (real stores; single shared reservation; no double deduction exercised; oversell guarded by resolved risk vectors).

Formal PASS question: under the user rule "正式用例只有条款全部满足才更新 PASS", NEITHER qualifies. ARB03 lacks zero/full first-leg and hedge-partial sub-scenario coverage in the frozen fixed-source evidence; DIM05 lacks 跨期 and 旧合约退出恢复 (the latter declared non-expressible). Both must stay 部分 (partial); this R2 QA adds two accepted sub-scenario greens only. The evidence_level string itself ends in _SCENARIO_ONLY — consistent with this reading.

## 5. Verdict

GO — for the parent agent to run the ARB03/DIM05 commit round (commit of the 4 owned files + evidence archive + docs/status update), subject to the mandatory disclosures below. No blocking hash or semantics discrepancy was found; all numeric claims reproduce; candidate == worktree byte-for-byte; both fixes are required by frozen code and do not weaken validation. The formal-PASS status of ARB03/DIM05 must NOT be flipped (Section 4).

D-register (discrepancies / must-track items; all non-blocking for the commit itself):
- D1 (documentation): status "Production edits" section describes only 2 fixes while the diffs total proofs +125/-25, authority +158/-15, fixtures +13/-4 (full IOC CANCELLED partial-cancel path). Commit message + status must state the full scope (Section 3d).
- D2 (evidence wording): "PROOF_READY ordering" is production-enforced (proofs.py:1646-1652) but NOT asserted by the test (zero grep hits). Do not cite it as test-level evidence.
- D3 (snapshot scope): the frozen candidate also pins other workstreams' WIP (risk service_component.py +266 CFG06b; CFG06b untracked policy test 33c76f13...; modified load01 test 82916661...). Evidence claims must be scoped to the 4 owned files + the two nodes.
- D4 (gitlink): bt_api_risk remains dirty (+266/-0, CFG06b) and its HEAD 089b5efd moves the risk gitlink vs older parent commits; the ARB03 commit must NOT update/stage the risk submodule pointer as part of this round (and root `m bt_api/bt_api_risk` will persist).
- D5 (pin movement): root HEAD moved abf1cd56 -> 77668953 via one post-QA docs-only commit (0 code files, 0 gitlink changes; all 2336 pinned sources re-hash correctly). The archive note must record this so the pin delta is not later mistaken for tampering.
- D6 (unverifiable static claim): the transient single R121 failure attributed to system load cannot be independently re-checked without rerunning (out of scope); it is recorded as author-attested only.
- D7 (live-tree movement during review): while this review ran, another workstream (CFG06b phase 2, per .codex-tmp/cfg06b-impl-status.md) modified bt_api_py/service_components.py (+26/-0) and bt_api_py/service_execution_group.py (+18/-0). Those live paths are NOT pinned by the QA spec (only their candidate/sdk copies are: 77d61c3f... and 5606785c..., both still matching their pins), and the 4 ARB03-owned files were re-hashed and are unchanged (8a508aa9/49fd1fbf/9bcf0216/a37f42a9). The frozen evidence is unaffected, but the ARB03 commit must stage only the 4 owned files and must not sweep in service_components.py / service_execution_group.py.

Mandatory disclosure list for the commit message / adjudication note:
1. Evidence: fixed-source QA D:/temp/it10arb03dim05-20261016-r2, exit 0, 2 passed, stdout sha256 b0cfab73..., stderr empty, sources/heads unchanged, spec 425af923... (byte-identical to focused-qa.json), result 0484337f..., runner identical to iteration10-focused-qa.py.
2. Pins: root abf1cd56 at run (now 77668953 after 1 docs-only commit), base b4aabd1e, execution dc8bd7a3, risk 089b5efd (dirty +266 CFG06b), transport a397fdb4; candidate/sdk also carries CFG06b WIP and load01 WIP (harmless to the 2 nodes).
3. Scope: the commit includes the full IOC CANCELLED partial-fill support path (proofs/authority) plus 4-file evidence set; the two named last-mile fixes are only the final delta.
4. Claim level: SCENARIO_ONLY — 2 fixed-source synthetic-provider scenarios; formal PASS for ARB03/DIM05 NOT updated; residual gaps quoted (ARB03 zero/full first leg, hedge partial; DIM05 跨期, 旧合约退出恢复 non-expressible).
5. Test semantics: reason code "provider_rejected" is contractual (^[a-z]... in both frozen contracts); "3.000" derives from quantity_step 0.001 at coordinator _size_hedge:723; RECOVERY_REQUIRED/hedge_residual_unresolved at _finish_hedge:794-796.
