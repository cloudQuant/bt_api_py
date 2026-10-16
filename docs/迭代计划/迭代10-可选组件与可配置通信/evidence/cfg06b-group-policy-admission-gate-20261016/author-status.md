# CFG06b group_policy implementation status (phase 1: risk-side machinery)

Date 2026-10-16. Repo D:\bt_api_py (parent HEAD 4e46ff01 + uncommitted sibling work; risk gitlink a1485ee + sibling WIP). No commits this phase (user rule / mission: "No commits").

## Phase boundary (strict)

Phase 1 (THIS phase, done): 
- EDIT bt_api/bt_api_risk/src/bt_api_risk/service_component.py — add GroupPolicyError, GroupPolicyStore, GroupAdmissionDecision, GroupPolicyService (+ GROUP_POLICY_OPERATIONS alias, __all__).
- NEW tests/unit/test_service_group_policy_control_plane.py — store/service behavior in isolation only.

FORBIDDEN this phase (owned by other live workstreams): bt_api_py/service_components.py, bt_api_py/service_execution_group.py, bt_api_py/service_host.py, bt_api_risk/src/bt_api_risk/core/**, frozen tests (test_service_policy_catalog_control_plane.py, test_service_components_group_runtime_bundle.py), tests/unit/test_service_portfolio_analytics_control_plane.py. Patches for service_components.py + service_execution_group.py are stored as TEXT below (anchored to the CURRENT tree — analytics sibling already landed ("risk","portfolio_analytics") so account_owner anchor moved from recon's ~602 to 628).

## Phase-1 design contract (what phase 2/3/4 must align with)

All codes are constant strings; errors carry `.code` only (no caller values). New codes are a strict superset of the recon rejection-code table.

- GroupPolicyError(ValueError): `.code`.
- GroupPolicyStore(path): sqlite, table `group_policy_versions` columns exactly:
  `group_policy_ref TEXT PRIMARY KEY, policy_version TEXT NOT NULL, document_json TEXT NOT NULL, pinned_utc TEXT NOT NULL`.
  - pin(group_policy_ref, policy_version, document) -> MappingProxyType record:
    ref must match `^[A-Za-z0-9][A-Za-z0-9._:-]*-v[1-9][0-9]*$` else VERSIONED_REFERENCE_REQUIRED (INVALID_REFERENCE for non-text/bad chars);
    policy_version must be 64-hex sha256 AND == sha256(canonical_json(normalized document)) else INVALID_POLICY_VERSION;
    document must be a Mapping with EXACTLY {"admitted_portfolios": [str,...], "denied_group_ids": [str,...]} (entries valid wire text, no dups, list|tuple -> normalized list) else INVALID_DOCUMENT;
    first pin final; identical re-pin idempotent (same row returned); any differing version/document -> GROUP_POLICY_VERSION_CONFLICT (row unchanged).
  - resolve(ref) -> MappingProxyType {"group_policy_ref","policy_version","document","pinned_utc"}; unknown -> GROUP_POLICY_NOT_FOUND; mutable/malformed ref -> VERSIONED_REFERENCE_REQUIRED / INVALID_REFERENCE.
  - close(); reopen same path = durable (recovery).
- GroupAdmissionDecision: frozen dataclass(slots) (admitted: bool, code: str, group_policy_ref: str, policy_version: str).
- GroupPolicyService(store, group_policy_ref, policy_version):
  - store must be exactly GroupPolicyStore else INVALID_GROUP_POLICY_STORE; ref/version validated as above.
  - SUPPORTED_OPERATIONS = frozenset({"group.admission.evaluate"}); module-level GROUP_POLICY_OPERATIONS alias.
  - properties group_policy_ref, policy_version; ready = (not closed) and store row exists and row.version == pinned version (False on missing row or sqlite error).
  - close() -> closed; evaluate() after close raises CLOSED; ready False.
  - evaluate(*, portfolio_ref, group_id) -> GroupAdmissionDecision:
    closed -> raise CLOSED; non-text/empty context -> raise INVALID_ADMISSION_CONTEXT;
    store sqlite error -> raise GROUP_POLICY_STORAGE_UNAVAILABLE; row missing -> raise GROUP_POLICY_NOT_FOUND;
    row.version != pinned version -> decision(False, GROUP_POLICY_VERSION_REJECTED);
    stored document invalid -> raise INVALID_DOCUMENT;
    portfolio_ref not in admitted_portfolios -> decision(False, GROUP_POLICY_SCOPE_DENIED);
    group_id in denied_group_ids -> decision(False, GROUP_POLICY_DENIED);
    else decision(True, "OK"). Decision always echoes the GATE's pinned ref/version.
  - Version check runs BEFORE scope check (fail-closed on drift).
- Canonical digest rule for operators/tests: json.dumps(normalized_document, ensure_ascii=False, sort_keys=True, separators=(",",":")) then sha256 hex; document bounded by existing 64KiB/depth-24 limits (INVALID_DOCUMENT on breach).
- __all__ additions: GroupAdmissionDecision, GroupPolicyError, GroupPolicyService, GroupPolicyStore (4 names; GROUP_POLICY_OPERATIONS intentionally NOT in __all__, mirroring module-level SUPPORTED_OPERATIONS).
- bt_api_risk/__init__.py NOT touched this phase; phase-3 patch imports `from bt_api_risk.service_component import GroupPolicyError`.

## PATCH TEXT 1/2 — bt_api_py/service_components.py (DO NOT APPLY THIS PHASE)

Three mechanical edits. Anchors are exact against the current working tree (analytics' portfolio_analytics builtin at 602-627 present). Context lines given verbatim.

### 1a — new role builtin; insert BEFORE `("risk", "account_owner"): _Builtin(` (currently line 628)

Context (keep, insert between):
```
                }
            ),
            validator_module_name="bt_api_risk.service_component",
        ),
>> INSERT HERE >>
        ("risk", "account_owner"): _Builtin(
            module_name="bt_api_py.service_risk_order_entry",
```
Insert text:
```python
        ("risk", "group_policy"): _Builtin(
            module_name="bt_api_risk.service_component",
            operations_attribute=("GroupPolicyService", "SUPPORTED_OPERATIONS"),
            resource_keys=frozenset(),
            public_ports=MappingProxyType({}),
            internal_ports=frozenset({"service.group_admission"}),
        ),
```

### 1b — build validation; insert AFTER the `missing_internal` raise, BEFORE the capabilities loop (currently: missing_internal block lines 1691-1697, `capabilities: list[ServiceCapability] = []` at 1699)

NOTE deliberate deviation from recon Step 2b pseudocode position (recon said before `missing_internal`): placing it AFTER preserves the recon code-table row "INTERNAL_PORT_UNAVAILABLE = bind_ports declares service.group_admission but provider supplied no port object". With the recon position, a portless build would raise GROUP_POLICY_RESOURCE_INVALID first (internal_ports.get -> None), contradicting the table.

Context (keep, insert between):
```
        missing_internal = {
            port
            for port in spec.bind_ports
            if port in builtin.internal_ports and port not in internal_ports
        }
        if missing_internal:
            raise ServiceComponentError("INTERNAL_PORT_UNAVAILABLE")
>> INSERT HERE >>
        capabilities: list[ServiceCapability] = []
```
Insert text:
```python
        if spec.component == "risk" and spec.options.get("role") == "group_policy":
            admission = internal_ports.get("service.group_admission")
            gate_type = getattr(prepared.module, "GroupPolicyService", None)
            if not isinstance(gate_type, type) or type(admission) is not gate_type:
                raise ServiceComponentError("GROUP_POLICY_RESOURCE_INVALID")
            if admission.group_policy_ref != prepared.options.get("group_policy_ref"):
                raise ServiceComponentError("GROUP_POLICY_REF_MISMATCH")
            if admission.ready is not True:
                raise ServiceComponentError("GROUP_POLICY_UNAVAILABLE")
```

### 1c1 — coordinator expected_keys extension; insert AFTER the approved_target_authority block, BEFORE `if set(resources.values) != set(expected_keys):` (currently 1271-1279 then 1280)

Context (keep, insert between):
```
        if (
            spec.component == "execution"
            and spec.options.get("role") == "account_owner"
            and "service.order_entry" in public_ports
            and "approved_target_authority" in resources.values
        ):
            if resources.values["approved_target_authority"] is None:
                raise ServiceComponentError("INVALID_APPROVED_TARGET_AUTHORITY_RESOURCE")
            expected_keys = expected_keys | {"approved_target_authority"}
>> INSERT HERE >>
        if set(resources.values) != set(expected_keys):
            raise ServiceComponentError("RESOURCE_KEYS_MISMATCH")
```
Insert text:
```python
        if (
            spec.component == "execution"
            and spec.options.get("role") == "group_coordinator"
            and "group_admission" in resources.values
        ):
            if resources.values["group_admission"] is None:
                raise ServiceComponentError("GROUP_POLICY_RESOURCE_INVALID")
            expected_keys = expected_keys | {"group_admission"}
```

### 1c2 — coordinator ctor kwarg; extend the GroupCoordinatorService constructor call (currently 1504-1511)

Context (keep, replace the call head with the insert):
```
                constructor = getattr(module, "GroupCoordinatorService", None)
                adapter = constructor(
                    values["store"],
                    values["targets"],
                    values["run_bindings"],
                    borrowed_upstream,
                    service_identity,
>> INSERT `group_admission=values.get("group_admission"),` HERE >>
                    instance_id=spec.instance_id,
                )
```

## PATCH TEXT 2/2 — bt_api_py/service_execution_group.py (DO NOT APPLY THIS PHASE; ARB03-owned)

Three mechanical edits (back-compat: default None keeps every existing harness green).

### 2a — import; extend the existing bt_api_risk import block (lines 54-58)

Context:
```
from bt_api_risk import (
    INSTRUMENT_METADATA_DIGEST_TAG,
    InstrumentRiskMetadata,
    RiskDeniedError,
)
```
Change to:
```
from bt_api_risk import (
    INSTRUMENT_METADATA_DIGEST_TAG,
    InstrumentRiskMetadata,
    RiskDeniedError,
)
from bt_api_risk.service_component import GroupPolicyError
```

### 2b — ctor gate; keyword-only param + validation + store

Extend `def __init__` (currently 398-408) keyword-only block:
```
        *,
        instance_id: str,
        group_admission: Any = None,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
```
(`Any` already imported at line 21.) Insert after the instance_id check (currently 421-422):
```
        if type(instance_id) is not str or _REFERENCE.fullmatch(instance_id) is None:
            raise ServiceExecutionGroupError("COMPONENT_INSTANCE_INVALID")
>> INSERT HERE >>
        if not callable(clock_ns) or not isinstance(targets, Mapping) or not targets:
```
Insert text:
```python
        if group_admission is not None:
            gate_evaluate = getattr(group_admission, "evaluate", None)
            gate_ref = getattr(group_admission, "group_policy_ref", None)
            if not callable(gate_evaluate) or type(gate_ref) is not str:
                raise ServiceExecutionGroupError("GROUP_POLICY_RESOURCE_INVALID")
```
Insert after `self._instance_id = instance_id` (currently 447):
```python
        self._group_admission = group_admission
```

### 2c — handle gate; new-submit path only, AFTER `request = self._parse_request(...)` (528), BEFORE `run_binding = self._run_bindings.bind(` (530); the retry/`else` path (535-542) deliberately bypasses the gate

Context (keep, insert between):
```
            run_binding = self._existing_submit_binding(registration, arguments)
            if run_binding is None:
                request = self._parse_request(registration, target, arguments)
>> INSERT HERE >>
                # Persist the Registry-derived run before package acceptance.
                run_binding = self._run_bindings.bind(
```
Insert text:
```python
                if self._group_admission is not None:
                    try:
                        admission_decision = self._group_admission.evaluate(
                            portfolio_ref=request.portfolio_ref,
                            group_id=request.group_id,
                        )
                    except GroupPolicyError as error:
                        return _failure(error.code)
                    if admission_decision.admitted is not True:
                        return _failure(admission_decision.code)
```
Notes: inner try/except is required because GroupPolicyError is a ValueError and the outer `except (TypeError, ValueError, KeyError)` (562-563) would otherwise mask its code as INVALID_REQUEST. Optional (recon says may): extend `ready` (489-492) — deferred, not required.

## Progress log

### Phase 2 (iteration 10, live implementation burst)

- Phase-2 START note (written before any edit): the two "DO NOT APPLY" notes above are STALE for this burst. ARB03 workstream COMPLETED; its uncommitted files are exactly {service_execution_group_proofs.py, service_execution_group_authority.py, tests/unit/_service_group_provider_fixtures.py, tests/integration/test_arb03_dim05_group_partial_fill_service.py}. service_components.py and service_execution_group.py are therefore free; both PATCH TEXTS are IN SCOPE now.
- Anchors re-derived mechanically at parent HEAD 7766895300f929779a36855b6028290687f7fe12: 1a account_owner builtin @628; 1b missing_internal @1691-1697 / capabilities @1699; 1c1 approved_target @1271-1279 / RESOURCE_KEYS_MISMATCH @1280; 1c2 ctor call @1503-1511; 2a bt_api_risk import @54-58; 2b ctor kw-only @405-407, instance_id check @421-422, `self._instance_id` @447; 2c handle @526-530. All match the stored texts; no design improvisation needed.
- [x] PATCH TEXT 1/2 applied verbatim (1a/1b/1c1/1c2) to bt_api_py/service_components.py; diff is exactly the stored insert texts (positions shifted mechanically: 1a after portfolio_analytics validator close; 1b after missing_internal; 1c1 after approved_target block; 1c2 `group_admission=values.get("group_admission"),` before `instance_id=`).
- [x] PATCH TEXT 2/2 applied verbatim (2a/2b/2c) to bt_api_py/service_execution_group.py; inner try/except kept so GroupPolicyError codes are not masked by the outer INVALID_REQUEST handler; retry/else path untouched (bypass by design).
- [x] Phase-3/4 focused tests: NEW tests/unit/test_service_group_policy_admission_gate.py (13 nodes) — role table validate/build (incl. INTERNAL_PORT_UNAVAILABLE / GROUP_POLICY_RESOURCE_INVALID / GROUP_POLICY_REF_MISMATCH / GROUP_POLICY_UNAVAILABLE), coordinator ctor gate validation, new-submit gate semantics (SCOPE_DENIED / DENIED / VERSION_REJECTED / CLOSED / NOT_FOUND / STORAGE_UNAVAILABLE / INVALID_DOCUMENT raised-code mapping), admitted-path continuation, retry bypass proven by call-count recorder AND by a tampered live gate, and build_component->handler integration (gate flows from resource values into the coordinator).
- [x] Verification battery A-F all EXIT 0 (evidence D:/temp/it10cfg06b-20261016-r2/, one subdir per run, stdout+exit+cmd per run).
- [x] Fixed-source QA round r1: spec .codex-tmp/iteration10-cfg06b-qa-spec-r1.json (139 pins = runtime import closure of the six node modules + runner + ini + both patched sources), runner .codex-tmp/iteration10-focused-qa.py -> EXIT 0, 54 passed, sources_unchanged true, heads_unchanged true. Evidence D:/temp/it10cfg06b-qa-20261016-r1/.
- Concurrent parent round-commit observed mid-phase: parent HEAD moved 77668953 -> f308c8d8 (ARB03 partial-fill round landed; it committed the ARB03-owned files, NOT my two patched files - they remain uncommitted M). No git writes by this phase.
- RESUME POINT for the next burst: nothing left in phases 2-4. Remaining acceptance below (host combo-3 matrix + combo-3 fixed-source round) belongs to the future CFG06 formal round.

- [x] Recon read; anchors re-read against current tree (shifted vs recon: account_owner 628; coordinator branch 1496-1511; internal_ports block 1679-1697; approved_target block 1271-1279).
- [x] Status file created with both patch texts (this file).
- [x] service_component.py classes added (docstring paragraph, dataclass import, GroupPolicyError / _group_policy_reference / _group_policy_digest / _group_policy_document / _group_policy_document_json / GroupPolicyStore / GroupAdmissionDecision / GroupPolicyService / GROUP_POLICY_OPERATIONS, __all__ +4).
- [x] NEW unit test tests/unit/test_service_group_policy_control_plane.py added (18 nodes) + PASS.
- [x] Frozen regression: tests/unit/test_service_policy_catalog_control_plane.py green (5 passed; round-154's "6 passed" = its 5 + 1 bundle node, both re-run green today as separate targets).
- [x] Risk-submodule sanity: bt_api/bt_api_risk/tests/test_service_component.py 24 passed (no regression from the module edit).

## Verification (phase 1, all with C:\anaconda3\python.exe 3.11.5 / pytest 8.0.0)

Commands run from D:\bt_api_py with PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 and `--noconftest --import-mode=importlib` (as required):

1. `python -m pytest tests/unit/test_service_group_policy_control_plane.py -v --junitxml=D:/temp/it10cfg06b-impl-20261016-r1/junit-phase1.xml` -> EXIT 0, **18 passed** (log pytest-phase1-r1.log).
2. `python -m pytest tests/unit/test_service_policy_catalog_control_plane.py -q` -> EXIT 0, 5 passed (frozen; log pytest-frozen-catalog-r1.log).
3. `python -m pytest tests/unit/test_service_components_group_runtime_bundle.py -q` -> EXIT 0, 1 passed (frozen; log pytest-frozen-bundle-r1.log).
4. `python -m pytest bt_api/bt_api_risk/tests/test_service_component.py -q` -> EXIT 0, 24 passed (log pytest-risk-service-component-r1.log).

Note: the frozen `test_group_policy_role_has_no_sdk_builtin_public_port` (still green) asserts `prepared[0].operations_by_port == {}` + host `NO_SUPPORTED_PUBLIC_PORTS` with zero resource-provider calls; patch 1a-1c keeps that true (empty public_ports on the new builtin; the internal port yields no public operations). Do NOT apply patches 1a-1c together with edits to that frozen file; it needs none.

Evidence dir: D:/temp/it10cfg06b-impl-20261016-r1/ (pytest-phase1-r1.log, junit-phase1.xml, pytest-frozen-catalog-r1.log, pytest-frozen-bundle-r1.log, pytest-risk-service-component-r1.log).

## Verification (phase 2, all with C:\anaconda3\python.exe 3.11.5 / pytest 8.0.0)

Environment for every run (from D:\bt_api_py): PYTHONDONTWRITEBYTECODE=1, PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, PYTHONPATH=D:/bt_api_py;D:/bt_api_py/bt_api/bt_api_base/src;D:/bt_api_py/bt_api/bt_api_execution/src;D:/bt_api_py/bt_api/bt_api_risk/src;D:/bt_api_py/bt_api/bt_api_transport_zmq/src, `-p no:cacheprovider`, fresh `--basetemp` per run. Evidence root D:/temp/it10cfg06b-20261016-r2/ (cmd.txt, stdout.log, exit.txt per run; junit.xml for run A):

1. NEW focused tests/unit/test_service_group_policy_admission_gate.py -> EXIT 0, **13 passed** (run-a-focused-gate).
2. tests/unit/test_service_group_policy_control_plane.py (phase-1, unchanged) -> EXIT 0, 18 passed (run-b-policy-control-plane).
3. Frozen tests/unit/test_service_policy_catalog_control_plane.py (incl. `test_group_policy_role_has_no_sdk_builtin_public_port`) -> EXIT 0, 5 passed (run-c-catalog-frozen).
4. Frozen tests/unit/test_service_components_group_runtime_bundle.py -> EXIT 0, 1 passed (run-d-bundle-frozen).
5. tests/unit/test_service_execution_group.py -> EXIT 0, 11 passed (run-e-execution-group).
6. tests/unit/test_service_execution_group_wire_reply.py -> EXIT 0, 6 passed (run-f-wire-reply).

Fixed-source QA round r1 (runner .codex-tmp/iteration10-focused-qa.py, spec .codex-tmp/iteration10-cfg06b-qa-spec-r1.json; command `python .codex-tmp/iteration10-focused-qa.py .codex-tmp/iteration10-cfg06b-qa-spec-r1.json`) -> EXIT 0, **54 passed**, source_count 139, sources_unchanged true, heads_unchanged true. Artifact: D:/temp/it10cfg06b-qa-20261016-r1/ (spec.json, runner.py, command.json, junit.xml, result.json, source-before/after.json, stdout/stderr). Heads pinned at build time: parent f308c8d853aff7e6ef67fda449b6e2214eb00219; risk 089b5efd71ebd0398c73e9aab370d674d88b41ba; base b4aabd1e17b9c7252fa7b4d266932087d99b5517; execution dc8bd7a3682d00cfaef4f15261607043c6d24fca; transport_zmq a397fdb460d2deb22e67ceb4963992ef63dbbb0e (all on branch dev).

Working-tree files changed by this phase (uncommitted by design; NO commits made):

- bt_api_py/service_components.py — sha256 2af780d16feb59603e88015e9ff54f8afc97e7476049e02513b4becb272885ac
- bt_api_py/service_execution_group.py — sha256 975c2c6ba466a0d4fca3e34907e85c0c95581e0e6573ba328b3d65546ccdf4a5
- tests/unit/test_service_group_policy_admission_gate.py (NEW, untracked) — sha256 2152617eff8eca2d74f8e2ba399c5d8924d92082681d0e95388c29675b84234c
- tests/unit/test_service_group_policy_control_plane.py — sha256 33c76f13fc6388b538054dab3ff6f8740ff72b0fe877d7d4a5e8f061433777b4 (phase-1 file, unchanged this phase)
- .codex-tmp/it10-build-cfg06b-qa-spec.py (new builder) — sha256 d2a3f8805c75eb85117e70600de8a9bb23a21f983a338c43696e457f82395536
- .codex-tmp/iteration10-cfg06b-qa-spec-r1.json (new spec) — sha256 a4351a09cb4d98c4a4e855dae222cd563846b4631f335ab6bdc35012568580a9
- (This status file is excluded from self-hashing: it changes with each entry.)

NOT touched by this phase (verified via git status): bt_api/bt_api_risk/** (phase-1 file unchanged; the risk submodule pointer advanced only via f308c8d8's parent gitlink), service_execution_group_proofs.py / service_execution_group_authority.py / tests/unit/_service_group_provider_fixtures.py / tests/integration/test_arb03_dim05_group_partial_fill_service.py (ARB03-owned; now committed in f308c8d8), tests/unit/test_load01_hundred_strategy_30min.py and tests/unit/test_service_risk_order_journal_decimal_match.py (other workstreams, still dirty in the worktree as before).

## Remaining acceptance (phases 2-4 of this file now COMPLETE; flags below belong to the CFG06 formal round)

- RESOLVED: both patch texts are applied (role builtin registration + coordinator gate) and verified by runs 1-6 + the fixed-source QA round; the frozen `test_group_policy_role_has_no_sdk_builtin_public_port` stays green (run 3) because the new builtin exposes no public port.
- STILL PENDING for the formal round: the full host matrix combo 3 (real ServiceHost + CURVE: execution group_coordinator with provider-supplied `group_admission` + risk group_policy with the internal `service.group_admission` port + minimal account delegation). The combination is now executable (patch 1 is in the tree) but no host-level test exists yet; this phase proves the same objects at validate/build/handler levels without a ServiceHost, so the combo-3 test can wire the identical provider values (ComponentResources.borrowed(values={... , "group_admission": gate}, internal_ports={"service.group_admission": gate}) for the paired components).
- STILL PENDING: public-access denial negatives at integration level (the gate is internal-only; no public port can reach `group.admission.evaluate`).
- STILL PENDING: the plan line-538 combo-3 fixed-source QA round; the r1 spec above is the units-only predecessor and should be rebuilt (new spec id + fresh non-pre-existing output dir) with the combo-3 host test added as a node once that test exists.
- Gate bypass on the retry path is asserted here by a call-count recorder and by a tampered live document; if the formal round wants evidence-level wording for "retry vs admission arbitration", reuse `test_retry_replay_bypasses_the_gate_after_new_submit_consulted_it` and `test_retry_replay_bypasses_a_tampered_reference_gate`.

