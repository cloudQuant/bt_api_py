# CFG06b phase-2 review status (READ-ONLY review agent, 2026-10-16)

Scope: static review of risk `service_component.py` (phase-1 machinery) + SDK `service_components.py` +
`service_execution_group.py` + 2 new test files + QA evidence `D:/temp/it10cfg06b-qa-20261016-r1` +
run logs `D:/temp/it10cfg06b-20261016-r2`. Verdict gates a commit round. No repo edits except this file.

## T1 Hash integrity — PASS (with foreign-drift disclosure)

Worktree sha256 recomputed live and compared with (a) author numbers in `.codex-tmp/cfg06b-impl-status.md:276-281`,
(b) QA spec pins in `D:/temp/it10cfg06b-qa-20261016-r1/spec.json` `source_sha256`. All CFG06b-owned files: exact match:

| file | sha256 (live == author == paper pins) |
|---|---|
| bt_api_py/service_components.py | 2af780d16feb59603e88015e9ff54f8afc97e7476049e02513b4becb272885ac |
| bt_api_py/service_execution_group.py | 975c2c6ba466a0d4fca3e34907e85c0c95581e0e6573ba328b3d65546ccdf4a5 |
| tests/unit/test_service_group_policy_admission_gate.py | 2152617eff8eca2d74f8e2ba399c5d8924d92082681d0e95388c29675b84234c |
| tests/unit/test_service_group_policy_control_plane.py | 33c76f13fc6388b538054dab3ff6f8740ff72b0fe877d7d4a5e8f061433777b4 |
| bt_api/bt_api_risk/src/bt_api_risk/service_component.py | 384243756948aabd6ce7dcb6b43333afdbd2fc2f009eb5fd00c5278377dd9ecd |

- QA evidence internal consistency: `exit.txt`=`0`; stdout `54 passed`; `stdout_sha256` in result.json
  (38769d74352f…) matches live stdout.log byte hash; stderr sha = empty-string sha (e3b0c442…); `source_count`=139
  = len(pins); `sources_unchanged`/`heads_unchanged` true; `source-before.json == source-after.json`
  (both ea51a3fa7388284b1ac62d351804badd7a217d57c650daaa120aa8c15814e228).
- spec repositories == result heads_before == heads_after: parent f308c8d853aff7e6ef67fda449b6e2214eb00219
  (== live HEAD), risk 089b5efd71ebd0398c73e9aab370d674d88b41ba (== live HEAD == parent gitlink),
  base b4aabd1e…, execution dc8bd7a3…, zmq a397fdb4… (all re-verified live).
- junit.xml: 54 testcases / 0 failures / 0 errors / 0 skipped; per-module 13+18+5+1+11+6 = 54 exactly;
  the two retry-bypass nodes present by name.
- Runner `.codex-tmp/iteration10-focused-qa.py` (pin 8b7f470b…) reads pinned bytes before AND after the run and
  asserts heads/branch — archived `runner.py` == live file.
- 136/139 pins still match live. 3 mismatches, ALL foreign WIP (post-QA drift, none CFG06b-owned):
  - bt_api/bt_api_risk/src/bt_api_risk/core/admission.py: pin fd1296888d4b… -> live 28b9918db57e… (DIM03 WIP)
  - bt_api_py/service_registry.py: pin cea39dacbe2b… -> live 6071265922a3… (registry #32 WIP)
  - bt_api_py/service_runtime.py: pin e5b8ba128d58… -> live 3d7fde5919b6… (registry #32 WIP)
  These were unchanged DURING the QA run (runner-enforced); the r1 evidence stays valid for the pinned bytes.
- r2 per-run dirs: run-a 13 passed, run-b 18, run-c 5, run-d 1, run-e 11, run-f 6; all EXIT=0; run-a junit = 13 nodes.
  These are plain-pytest supplementary logs (no pinning); r1 is the authoritative artifact.

## T2 Candidate faithfulness — PASS (no candidate overlay exists)

This round has NO `candidate\sdk\` / `candidate\risk\` overlay: the builder
(`.codex-tmp/it10-build-cfg06b-qa-spec.py`) pins live worktree bytes of the six node modules' import closure.
Faithfulness therefore = pins vs live bytes, which I verified file-by-file: all CFG06b-owned pins match live exactly
(table above), and the runner re-hashed them at run time. No copy step exists that could diverge.

## T3 Semantics audit — PASS

(a) Role builtin registration (service_components.py:628-635, diff +26/-0): new
`("risk", "group_policy")` entry inserted exactly before `("risk", "account_owner")`, with
`public_ports=MappingProxyType({})`, `internal_ports=frozenset({"service.group_admission"})`,
`operations_attribute=("GroupPolicyService","SUPPORTED_OPERATIONS")`. Public surface stays empty:
frozen test `test_service_policy_catalog_control_plane.py:628` (unchanged, green in run-c) asserts
`operations_by_port == {}` + host `NO_SUPPORTED_PUBLIC_PORTS` (service_host.py:379) with zero resource-provider
calls; new test line 437-438 asserts the same empty dicts. Build validation at 1715-1724 (inserted AFTER the
`missing_internal` raise at 1711-1712, BEFORE `capabilities` at 1726 — design "1b" deviation preserved:
portless build still yields INTERNAL_PORT_UNAVAILABLE, test line 473-479).
Coordinator expected_keys extension at 1281-1288 (only execution/group_coordinator with `group_admission` present;
None -> GROUP_POLICY_RESOURCE_INVALID) and ctor kwarg `group_admission=values.get("group_admission")` at 1527.

(b) Gate placement (service_execution_group.py:528-552): gate block 537-546 sits INSIDE the
`if run_binding is None:` new-submit branch, after `_parse_request` (536) and before `run_bindings.bind` (548);
the retry `else:` branch 553-560 calls only `_assert_exact_submit_replay` — no gate call. Gate consulted exactly
once per new submit for the coordinator's full lifetime per test.

(c) Fail-closed: every gate refusal returns `_failure(code)` before `bind`; `_assert_gate_failure`
(test lines 379-398) asserts for each code: result == {"ok": False, "code": <code>, "result": {}},
`run_bindings.list_all() == ()`, `group_store.get(...) is None`, `runtime.calls == []`. Codes covered:
SCOPE_DENIED (562-573), DENIED (576-583), VERSION_REJECTED (586-598), CLOSED / GROUP_POLICY_NOT_FOUND /
GROUP_POLICY_STORAGE_UNAVAILABLE (601-636), INVALID_DOCUMENT (703-738 line 731-733). Inner
`except GroupPolicyError` (543-544) prevents the outer `except (TypeError, ValueError, KeyError)` (580-581)
from masking codes as INVALID_REQUEST. Risk-side store raises before/inside rollback-only transactions;
no partial writes on conflict (risk diff pin() row-unchanged path).

(d) No contract weakening: both SDK diffs are pure insertions (+26, +18); risk diff +266/-0; no deletions,
no code/operation/schema changes to existing surfaces; `group_admission` defaults to None (back-compat for all
existing ctor call sites — 3 call sites in test_service_execution_group.py unchanged; run-e green);
frozen catalog (5) + bundle (1) tests green; `GROUP_COORDINATOR_OPERATIONS` unchanged
(service_execution_group.py:78 = {groups.submit, groups.query}).

(e) Ordering claim confirmed at 536 -> 537-546 -> 548; `_parse_request` (782-861) is pure validation
(no binding/RPC); `request` is a `GroupOrderIntent` with `.portfolio_ref`/`.group_id`.

Risk module (diff +266): GroupPolicyError(.code), store schema exactly
`group_policy_ref TEXT PRIMARY KEY, policy_version TEXT NOT NULL, document_json TEXT NOT NULL, pinned_utc TEXT NOT NULL`
(lines 982-986); ref pattern `-v[1-9][0-9]*$` (44-46); digest = sha256(canonical json) (1044-1046, 1003-1005);
first pin final, identical re-pin idempotent, conflict GROUP_POLICY_VERSION_CONFLICT (1008-1015);
evaluate order closed -> context -> storage -> not-found -> version-drift -> document -> scope -> denied
(1119-1141); decisions immutable frozen slots; `GROUP_POLICY_OPERATIONS` alias module-level but NOT in `__all__`
(1148, 1150-1156; contract-consistent). All claimed codes exist; extras (INVALID_POLICY_VERSION,
INVALID_ADMISSION_CONTEXT, INVALID_GROUP_POLICY_STORE, INVALID_STORE_PATH) are a strict superset.

## T4 Clause mapping

- `正式迭代计划.md:743` CFG06 row: strategy/application/service roles, outbound clients, "R 三种执行/分析角色及
  policy_catalog 控制面; 类型/版本错误政策拒绝" with acceptance "第 5.6 节正反配置及真实注册" + extras/no-arbitrary-load.
- `正式迭代计划.md:538` combo-3 wording (P0 sample-set, must be verified P1/P2, YAML parse alone insufficient):
  "group_coordinator + group_policy 和最小账户委托". This commit round covers the positive machinery for that
  combo at validate/build/handler level only; a real-ServiceHost+CURVE combo-3 run is STILL PENDING.
- `迭代10验收状态汇总.md:53` + round-156 record: CFG06 = 部分/本机进程, formal PASS 25/68; "group_policy 真实注册
  仍缺" was the open note. This round closes the units/build/handler slice of that gap; combo-3 host matrix,
  public-access denial negatives, and the line-538 combo-3 fixed-source QA round (new spec id + fresh output dir,
  host test as node) remain. **CFG06 formal PASS must NOT be flipped; keep 25/68, record as a partial slice round.**

## T5 Retry-bypass + admitted-path test bodies — PASS

- `test_retry_replay_bypasses_the_gate_after_new_submit_consulted_it` (668-701): real call-count recorder —
  first submit asserts `recorder.calls == [(portfolio, group)]` (682), flips `admit=False` (686), replay
  asserts ok + calls still length 1 (688-689), then a fresh group-b asserts GROUP_POLICY_DENIED and
  calls == 2 entries (694-695) proving the bypass is retry-only; binding for group-b absent (696).
- `test_retry_replay_bypasses_a_tampered_reference_gate` (703-738): tampers the live sqlite document to an
  invalid shape (721-725), replay ok (727), fresh group-b -> INVALID_DOCUMENT + no binding (731-733) proving
  the tamper is live and unevaluated on retry.
- `test_new_submit_admitted_gate_continues_to_binding_and_budget_decision` (639-665): admitted gate,
  run_bindings.get(...) is not None (659, durable binding), snapshot REJECTED_BEFORE_DISPATCH (658),
  runtime.calls == [] (660).
- No `skip`/`xfail`/`pytest.mark`/monkeypatch/mock anywhere in either new test file (grep clean;
  control-plane file has one `parametrize` only). junit confirms 0 skipped.

## T6 D-register (discrepancies + mandatory commit disclosures)

- D1 (scope): commit ONLY the 5 CFG06b files + evidence archive + risk gitlink. Parent worktree also has
  `M service_registry.py`, `M service_runtime.py` (registry #32), `M tests/unit/test_load01_hundred_strategy_30min.py`,
  `?? tests/unit/test_service_risk_order_journal_decimal_match.py`, `?? risk-order-exposure-sdk-round-r20.md`,
  `?? %SystemDrive%/`, `?? 0`, `?? .codex-tmp/` — NONE may be staged. Risk submodule: stage ONLY
  `src/bt_api_risk/service_component.py`; leave `M core/admission.py` + `?? tests/test_margin_pool_reservations.py`
  + `?? tests/test_trade_identity_claims.py` (DIM03/other). Submodule commit first, then parent gitlink update.
- D2 (foreign pin drift): admission.py (DIM03), service_registry.py/service_runtime.py (registry #32) no longer
  match r1 pins; r1 evidence remains valid for its pinned bytes; future CFG06 formal round MUST rebuild the spec
  (new id, fresh output dir) after those workstreams land, and re-run the two new test files against the new registry bytes.
- D3 (stale frozen docstring): `test_service_policy_catalog_control_plane.py:629-637` docstring still says the role
  table has no group_policy entry; the assertions remain correct and green. Do NOT edit the frozen test; note the
  docstring drift for the formal round.
- D4 (evidence plumbing): r1 runner/pytest ini live outside the repo (`D:/temp/it10cfg06-scratch/pytest-risk-fx.ini`,
  pin f9aada43…); archive the r1 output tree; r2 logs are supplementary (unpinned) and should be labeled as such.
- D5 (evidence_level honesty): spec says "..._UNITS_WITH_CFG06_REGRESSION_UNITS_ONLY" — accurate; no host claim.
- D6 (round record): per rounds 154-164 pattern, add a new round entry (next number; latest is 第164轮) recording
  CFG06b slice, both commit hashes (risk submodule + parent), exact evidence shas, and "CFG06 不新增 PASS，正式 25/68".
- D7 (commit message must cite): evidence spec.json a4351a09cb4d98c4a4e855dae222cd563846b4631f335ab6bdc35012568580a9,
  result.json 15ce1500a1b1ca9aa77ec84cf4427ed344bdb403cd50d4cdbec1dcc8a50d75e3,
  junit.xml fcc9dd6a62b1b2ed947ad20966b55875d1f2675699567c4bdda45edf10c6fc4d,
  stdout sha 38769d74352ffbfef2791376ee381365cbf93a86145fc3d3909c3b0ec03525b3, exit 0, 54 passed, 139 pins,
  heads parent f308c8d8 / risk 089b5efd unchanged.

## Verdict

GO (commit round may proceed, with D1-D7 disclosures). Formal CFG06 PASS NOT flipped — remains 部分, 25/68.

Evidence to cite: r1 dir D:/temp/it10cfg06b-qa-20261016-r1 (spec  a4351a09…, result 15ce1500…, junit fcc9dd6a…,
stdout 38769d74…, exit 0, 54 passed, 139 pins, sources/heads unchanged) + worktree hashes in T1.
