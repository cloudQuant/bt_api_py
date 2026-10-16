"""CFG06b phase-3/4: fixed ``group_policy`` role and coordinator admission gate.

Proves the phase-2 patch path live in three layers, all aligned with the
phase-1 design contract (`tests/unit/test_service_group_policy_control_plane.py`):

1. ``service_components`` role table: the risk ``group_policy`` role now has a
   fixed builtin whose only surface is the internal ``service.group_admission``
   port. The build validates the provider-supplied gate's exact type, the
   configured versioned reference, and readiness, while never exposing a public
   capability.
2. ``GroupCoordinatorService`` gate contract: the optional ``group_admission``
   constructor resource is validated (callable ``evaluate`` + text
   ``group_policy_ref``), evaluated exactly once for a new ``groups.submit``
   before any run binding or upstream RPC, and mapped to the fixed phase-1
   rejection codes. The exact-replay retry path deliberately bypasses the gate.
3. Component-build integration: a coordinator component built through
   ``build_component`` receives the gate from the trusted resource values and
   the public ``groups.submit`` handler enforces it end to end.

Every refusal asserted here is a constant code from the phase-1 contract, and
each is verified to leave no run binding, no accepted group, and no RPC behind.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from decimal import Decimal
from functools import partial
from pathlib import Path

import pytest

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    ExecutionScope,
    GroupOrderSnapshot,
    GroupOrderState,
    OrderIntent,
    OrderType,
    PortfolioGroupBudgetPolicy,
    Side,
    SqliteOrderGroupStore,
    TimeInForce,
    build_instrument_risk_group_budget_quote,
    group_leg_intent_id,
)
from bt_api_risk import (
    INSTRUMENT_METADATA_DIGEST_TAG,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
)
from bt_api_risk.service_component import (
    GroupAdmissionDecision,
    GroupPolicyService,
    GroupPolicyStore,
)
from bt_api_py.service_components import (
    BorrowedServiceClient,
    ComponentResources,
    ServiceComponentError,
    build_component,
    component_specs,
    validate_components,
)
from bt_api_py.service_execution_group import (
    GROUP_COORDINATOR_OPERATIONS,
    GroupAccountRoute,
    GroupCoordinatorService,
    GroupCoordinatorTarget,
    GroupInstrumentRule,
    ServiceExecutionGroupError,
)
from bt_api_py.service_execution_group_client import GroupOriginRunBindingStore
from bt_api_py.service_registry import ActiveRegistrationContext, ServiceCallContext

_DOMAIN = "paper:cfg06b-admission"
_STRATEGY = "strategy-a"
_RUN = "run-a"
_PORTFOLIO = "portfolio-a"
_COORDINATOR_INSTANCE = "group-coordinator-a"
_COORDINATOR_SCOPE_REF = "group-scope-v1"
_COORDINATOR_PRINCIPAL = "group-coordinator-principal"
_STRATEGY_PRINCIPAL = "strategy-principal"
_GROUP_ID = "group-a"
_ACCOUNTS = ("account-a", "account-b")
_POLICY_REF = "cfg06b-group-policy-v1"
_POLICY_INSTANCE = "group-policy-risk"
_RECORDING_REF = "cfg06b-recording-policy-v1"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(document: object) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _version(document: object) -> str:
    return hashlib.sha256(_canonical_json(document).encode("utf-8")).hexdigest()


def _pinned_gate(
    path: Path,
    *,
    ref: str = _POLICY_REF,
    admitted: tuple[str, ...] = (_PORTFOLIO,),
    denied: tuple[str, ...] = (),
) -> tuple[GroupPolicyStore, GroupPolicyService]:
    document = {"admitted_portfolios": list(admitted), "denied_group_ids": list(denied)}
    store = GroupPolicyStore(str(path))
    version = _version(document)
    store.pin(ref, version, document)
    return store, GroupPolicyService(store, ref, version)


# ---------------------------------------------------------------------------
# Harness: real coordinator over a fail-closed runtime and real risk gate.
# ---------------------------------------------------------------------------


class _NoRpcRuntime:
    """Fail-closed borrowed runtime: every tested path must stay pre-RPC."""

    state = "registered"
    healthy = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def call(self, mount: str, operation: str, arguments: object, **kwargs: object) -> object:
        del arguments, kwargs
        self.calls.append((mount, operation))
        raise AssertionError(f"unexpected group RPC: {mount}/{operation}")


class _RecordingGate:
    """Exact optional-resource contract double: callable evaluate + text ref."""

    group_policy_ref = _RECORDING_REF

    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []
        self.admit = True

    def evaluate(self, *, portfolio_ref: str, group_id: str) -> GroupAdmissionDecision:
        self.calls.append((portfolio_ref, group_id))
        return GroupAdmissionDecision(
            admitted=self.admit,
            code="OK" if self.admit else "GROUP_POLICY_DENIED",
            group_policy_ref=self.group_policy_ref,
            policy_version="d" * 64,
        )


class _NonTextRefGate:
    """Callable evaluate but a non-text reference: refused by the ctor."""

    group_policy_ref = 7

    def evaluate(self, *, portfolio_ref: str, group_id: str) -> object:
        raise AssertionError("a refused gate must never be evaluated")


def _metadata(now_ns: int) -> InstrumentRiskMetadata:
    return InstrumentRiskMetadata(
        instrument="BTC-USDT",
        metadata_version="metadata-v1",
        as_of_ns=now_ns - 1_000_000,
        expires_at_ns=now_ns + 3_600_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("1000000"),
        quantity_unit="BTC",
        valuation_unit="USD",
    )


def _strategy_scope(*, run_id: str | None = _RUN) -> WireScope:
    return WireScope(
        execution_domain_ref=_DOMAIN,
        account_refs=_ACCOUNTS,
        strategy_ref=_STRATEGY,
        run_id=run_id,
        portfolio_ref=_PORTFOLIO,
    )


def _registration(now_ns: int) -> ActiveRegistrationContext:
    identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        _STRATEGY_PRINCIPAL,
        actor_ref=_STRATEGY,
    )
    context = ServiceCallContext(
        identity=identity,
        scope=_strategy_scope(),
        registration_id="registration-a",
        principal_ref=_STRATEGY_PRINCIPAL,
    )
    return ActiveRegistrationContext(
        context=context,
        instance_id=_COORDINATOR_INSTANCE,
        port="service.order_group_entry",
        scope_ref=_COORDINATOR_SCOPE_REF,
        operations=GROUP_COORDINATOR_OPERATIONS,
        write_operations=frozenset({"groups.submit"}),
        service_epoch="epoch-a",
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        expires_at=now_ns / 1_000_000_000 + 3_600,
        strategy_write_run_id=_RUN,
    )


def _intent(
    *,
    account_ref: str,
    leg_id: str,
    deadline_ns: int,
    metadata: InstrumentRiskMetadata,
    side: Side,
    group_id: str = _GROUP_ID,
) -> OrderIntent:
    execution_scope = ExecutionScope("PAPER", "simulation", account_ref, _STRATEGY, "20261015")
    return OrderIntent.limit(
        intent_id=group_leg_intent_id(group_id, leg_id),
        scope=execution_scope,
        signal_id=f"signal-{leg_id}",
        instrument=metadata.instrument,
        side=side,
        quantity=Decimal("1"),
        price=Decimal("100"),
        time_in_force=TimeInForce.IOC,
        metadata_version=metadata.metadata_version,
        deadline_ns=deadline_ns,
        tags={INSTRUMENT_METADATA_DIGEST_TAG: metadata.digest},
    )


def _arguments(now_ns: int, metadata: InstrumentRiskMetadata, *, group_id: str = _GROUP_ID):
    deadline_ns = now_ns + 120_000_000_000
    return {
        "group_id": group_id,
        "deadline_ns": deadline_ns,
        "first_leg_id": "leg-a",
        "legs": [
            {
                "leg_id": "leg-a",
                "account_ref": "account-a",
                "intent": _intent(
                    account_ref="account-a",
                    leg_id="leg-a",
                    deadline_ns=deadline_ns,
                    metadata=metadata,
                    side=Side.BUY,
                    group_id=group_id,
                ).to_payload(),
            },
            {
                "leg_id": "leg-b",
                "account_ref": "account-b",
                "intent": _intent(
                    account_ref="account-b",
                    leg_id="leg-b",
                    deadline_ns=deadline_ns,
                    metadata=metadata,
                    side=Side.SELL,
                    group_id=group_id,
                ).to_payload(),
            },
        ],
    }


def _budget_binding(
    metadata: InstrumentRiskMetadata,
    *,
    max_group_gross_notional: Decimal = Decimal("1000000"),
    max_active_gross_notional: Decimal = Decimal("10000000"),
):
    policy = PortfolioGroupBudgetPolicy(
        portfolio_ref=_PORTFOLIO,
        policy_id="portfolio-budget-v1",
        policy_version=1,
        valuation_unit="USD",
        max_group_gross_notional=max_group_gross_notional,
        max_active_gross_notional=max_active_gross_notional,
        max_active_groups=100,
    )
    builder = partial(
        build_instrument_risk_group_budget_quote,
        registry=InstrumentRiskRegistry((metadata,)),
    )
    return policy, builder


def _target(now_ns: int, *, budget_limit: Decimal | None = None):
    stable_scope = _strategy_scope(run_id=None)
    metadata = _metadata(now_ns)
    fingerprints = {account_ref: _digest(f"risk:{account_ref}") for account_ref in _ACCOUNTS}
    accounts = {
        account_ref: GroupAccountRoute(
            execution_scope=ExecutionScope(
                "PAPER", "simulation", account_ref, _STRATEGY, "20261015"
            ),
            risk_scope_fingerprint=fingerprints[account_ref],
            reservation_scope_ref=f"reservation-{account_ref}-v1",
            order_scope_ref=f"order-{account_ref}-v1",
            query_scope_ref=f"query-{account_ref}-v1",
            instruments={
                metadata.instrument: GroupInstrumentRule(
                    metadata=metadata,
                    base_units_per_order_unit=Decimal("1"),
                    allowed_order_types=frozenset({OrderType.LIMIT}),
                )
            },
        )
        for account_ref in _ACCOUNTS
    }
    policy, builder = _budget_binding(
        metadata,
        max_group_gross_notional=(
            Decimal("1000000") if budget_limit is None else budget_limit
        ),
    )
    return (
        stable_scope,
        GroupCoordinatorTarget(
            scope_ref=_COORDINATOR_SCOPE_REF,
            accounts=accounts,
            max_group_lifetime_ns=300_000_000_000,
            portfolio_budget_policy=policy,
            budget_quote_builder=builder,
        ),
        metadata,
    )


def _registered_gated_service(
    tmp_path: Path,
    gate: object | None,
    *,
    now_ns: list[int],
    runtime: _NoRpcRuntime,
    budget_limit: Decimal | None = None,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    stable_scope, target, metadata = _target(now_ns[0], budget_limit=budget_limit)
    group_store = SqliteOrderGroupStore(tmp_path / "groups.sqlite3")
    run_bindings = GroupOriginRunBindingStore(tmp_path / "group-runs.sqlite3")
    service = GroupCoordinatorService(
        group_store,
        {stable_scope: target},
        run_bindings,
        BorrowedServiceClient(runtime),
        WireIdentity(WireRole.SERVICE, _COORDINATOR_PRINCIPAL, "coordinator-actor"),
        instance_id=_COORDINATOR_INSTANCE,
        group_admission=gate,
        clock_ns=lambda: now_ns[0],
    )
    registration = _registration(now_ns[0])
    service.maintain_registrations((registration,))
    return service, group_store, run_bindings, registration, metadata


def _submit(service: GroupCoordinatorService, registration, metadata, now_ns, *, group_id=_GROUP_ID):
    return service.handle(
        "groups.submit",
        context=registration.context,
        arguments=_arguments(now_ns[0], metadata, group_id=group_id),
    )


def _assert_gate_failure(
    tmp_path: Path,
    gate: object,
    runtime: _NoRpcRuntime,
    now_ns: list[int],
    expected_code: str,
) -> None:
    service, group_store, run_bindings, registration, metadata = _registered_gated_service(
        tmp_path, gate, now_ns=now_ns, runtime=runtime
    )
    try:
        result = _submit(service, registration, metadata, now_ns)
        assert result == {"ok": False, "code": expected_code, "result": {}}
        assert run_bindings.list_all() == ()
        assert group_store.get(_PORTFOLIO, _GROUP_ID) is None
        assert runtime.calls == []
    finally:
        service.close()
        group_store.close()
        run_bindings.close()


# ---------------------------------------------------------------------------
# Layer 1: the fixed role table and the internal-port build contract.
# ---------------------------------------------------------------------------


def _risk_group_policy_prepared(ref: str = _POLICY_REF):
    components = {
        "risk": {
            "config_schema_version": 2,
            "settings": {
                "instance_id": _POLICY_INSTANCE,
                "bind_ports": ["service.group_admission"],
                "options": {"role": "group_policy", "group_policy_ref": ref},
            },
        }
    }
    return validate_components(component_specs(components))


def _gate_provider(internal_ports: dict[str, object], calls: list[tuple[str, str, dict]]):
    def provider(component: str, instance_id: str, options, scope) -> ComponentResources:
        del scope
        calls.append((component, instance_id, dict(options)))
        return ComponentResources.borrowed(
            {}, ready=lambda: True, internal_ports=dict(internal_ports)
        )

    return provider


def test_group_policy_role_validates_with_no_public_operation_surface() -> None:
    prepared = _risk_group_policy_prepared()
    assert len(prepared) == 1
    assert prepared[0].spec.component == "risk"
    assert prepared[0].spec.options.get("role") == "group_policy"
    # Internal-only surface: the fixed role yields no public capability.
    assert dict(prepared[0].operations_by_port) == {}
    assert dict(prepared[0].write_operations_by_port) == {}


def test_group_policy_role_build_wires_the_internal_gate(tmp_path: Path) -> None:
    store, gate = _pinned_gate(tmp_path / "policy.sqlite")
    calls: list[tuple[str, str, dict]] = []
    prepared = _risk_group_policy_prepared()
    try:
        component = build_component(
            prepared[0],
            scope=_strategy_scope(run_id=None),
            resource_provider=_gate_provider({"service.group_admission": gate}, calls),
        )
        assert calls == [
            ("risk", _POLICY_INSTANCE, {"role": "group_policy", "group_policy_ref": _POLICY_REF})
        ]
        assert component.component == "risk"
        assert component.instance_id == _POLICY_INSTANCE
        assert component.capabilities == ()
        assert component.handlers == {}
        assert dict(component.internal_ports) == {"service.group_admission": gate}
        assert component.readiness() is True
        assert gate.ready is True
        component.close()
    finally:
        store.close()


def test_group_policy_role_build_refuses_missing_invalid_or_unready_gate(tmp_path: Path) -> None:
    prepared = _risk_group_policy_prepared()
    opened: list[GroupPolicyStore] = []
    try:
        # Declared bind port without a provider object: the documented
        # INTERNAL_PORT_UNAVAILABLE refusal (the build check runs after the
        # generic missing-internal-port check by design).
        with pytest.raises(ServiceComponentError) as missing:
            build_component(
                prepared[0],
                scope=_strategy_scope(run_id=None),
                resource_provider=_gate_provider({}, []),
            )
        assert missing.value.code == "INTERNAL_PORT_UNAVAILABLE"

        # A non-GroupPolicyService object on the internal port.
        with pytest.raises(ServiceComponentError) as invalid:
            build_component(
                prepared[0],
                scope=_strategy_scope(run_id=None),
                resource_provider=_gate_provider({"service.group_admission": object()}, []),
            )
        assert invalid.value.code == "GROUP_POLICY_RESOURCE_INVALID"

        # A gate activated for a different versioned reference than configured.
        other = tmp_path / "other"
        other.mkdir()
        other_store, other_gate = _pinned_gate(other / "policy.sqlite", ref="cfg06b-other-policy-v1")
        opened.append(other_store)
        with pytest.raises(ServiceComponentError) as mismatch:
            build_component(
                prepared[0],
                scope=_strategy_scope(run_id=None),
                resource_provider=_gate_provider({"service.group_admission": other_gate}, []),
            )
        assert mismatch.value.code == "GROUP_POLICY_REF_MISMATCH"

        # A correct-type gate whose pinned row is missing: fails closed.
        unready = tmp_path / "unready"
        unready.mkdir()
        unready_store = GroupPolicyStore(str(unready / "policy.sqlite"))
        opened.append(unready_store)
        unready_gate = GroupPolicyService(unready_store, _POLICY_REF, "c" * 64)
        assert unready_gate.ready is False
        with pytest.raises(ServiceComponentError) as unavailable:
            build_component(
                prepared[0],
                scope=_strategy_scope(run_id=None),
                resource_provider=_gate_provider({"service.group_admission": unready_gate}, []),
            )
        assert unavailable.value.code == "GROUP_POLICY_UNAVAILABLE"
    finally:
        for store in opened:
            store.close()


# ---------------------------------------------------------------------------
# Layer 2: GroupCoordinatorService gate contract (direct construction).
# ---------------------------------------------------------------------------


def test_coordinator_ctor_validates_only_a_gate_shaped_resource(tmp_path: Path) -> None:
    now_ns = [time.time_ns()]
    runtime = _NoRpcRuntime()
    baseline = tmp_path / "baseline"
    service, group_store, run_bindings, _, _ = _registered_gated_service(
        baseline, None, now_ns=now_ns, runtime=runtime
    )
    service.close()
    group_store.close()
    run_bindings.close()

    stable_scope, target, _ = _target(now_ns[0])
    for index, bad in enumerate((object(), _NonTextRefGate())):
        case = tmp_path / f"bad-{index}"
        case.mkdir()
        case_store = SqliteOrderGroupStore(case / "groups.sqlite3")
        case_bindings = GroupOriginRunBindingStore(case / "group-runs.sqlite3")
        try:
            with pytest.raises(ServiceExecutionGroupError) as exc_info:
                GroupCoordinatorService(
                    case_store,
                    {stable_scope: target},
                    case_bindings,
                    BorrowedServiceClient(runtime),
                    WireIdentity(WireRole.SERVICE, _COORDINATOR_PRINCIPAL, "coordinator-actor"),
                    instance_id=_COORDINATOR_INSTANCE,
                    group_admission=bad,
                    clock_ns=lambda: now_ns[0],
                )
            assert exc_info.value.code == "GROUP_POLICY_RESOURCE_INVALID"
        finally:
            case_store.close()
            case_bindings.close()


def test_new_submit_gate_scope_denial_is_a_stable_failure_without_side_effects(
    tmp_path: Path,
) -> None:
    now_ns = [time.time_ns()]
    runtime = _NoRpcRuntime()
    store, gate = _pinned_gate(tmp_path / "policy.sqlite", admitted=("portfolio-other",))
    try:
        _assert_gate_failure(
            tmp_path / "denied", gate, runtime, now_ns, "GROUP_POLICY_SCOPE_DENIED"
        )
    finally:
        store.close()


def test_new_submit_gate_denied_group_is_a_stable_failure(tmp_path: Path) -> None:
    now_ns = [time.time_ns()]
    runtime = _NoRpcRuntime()
    store, gate = _pinned_gate(tmp_path / "policy.sqlite", denied=(_GROUP_ID,))
    try:
        _assert_gate_failure(tmp_path / "denied", gate, runtime, now_ns, "GROUP_POLICY_DENIED")
    finally:
        store.close()


def test_new_submit_gate_version_drift_rejects_before_any_scope_decision(tmp_path: Path) -> None:
    now_ns = [time.time_ns()]
    runtime = _NoRpcRuntime()
    # The pinned row and document would admit this group; the gate activation
    # itself is stale, and version drift must win over scope.
    store, _ = _pinned_gate(tmp_path / "policy.sqlite")
    stale = GroupPolicyService(store, _POLICY_REF, "c" * 64)
    try:
        _assert_gate_failure(
            tmp_path / "drift", stale, runtime, now_ns, "GROUP_POLICY_VERSION_REJECTED"
        )
    finally:
        store.close()


def test_new_submit_gate_raised_codes_are_mapped_one_to_one(tmp_path: Path) -> None:
    now_ns = [time.time_ns()]

    # CLOSED: the gate was closed after activation.
    closed_dir = tmp_path / "closed"
    closed_dir.mkdir()
    closed_store, closed_gate = _pinned_gate(closed_dir / "policy.sqlite")
    closed_gate.close()
    try:
        _assert_gate_failure(closed_dir, closed_gate, _NoRpcRuntime(), now_ns, "CLOSED")
    finally:
        closed_store.close()

    # GROUP_POLICY_NOT_FOUND: the pinned row disappeared under the gate.
    missing_dir = tmp_path / "missing"
    missing_dir.mkdir()
    missing_store, missing_gate = _pinned_gate(missing_dir / "policy.sqlite")
    try:
        with sqlite3.connect(str(missing_dir / "policy.sqlite")) as probe:
            probe.execute(
                "DELETE FROM group_policy_versions WHERE group_policy_ref=?", (_POLICY_REF,)
            )
        _assert_gate_failure(
            missing_dir / "case", missing_gate, _NoRpcRuntime(), now_ns, "GROUP_POLICY_NOT_FOUND"
        )
    finally:
        missing_store.close()

    # GROUP_POLICY_STORAGE_UNAVAILABLE: the borrowed store is gone.
    gone_dir = tmp_path / "gone"
    gone_dir.mkdir()
    gone_store, gone_gate = _pinned_gate(gone_dir / "policy.sqlite")
    gone_store.close()
    _assert_gate_failure(
        gone_dir / "case", gone_gate, _NoRpcRuntime(), now_ns, "GROUP_POLICY_STORAGE_UNAVAILABLE"
    )


def test_new_submit_admitted_gate_continues_to_binding_and_budget_decision(
    tmp_path: Path,
) -> None:
    now_ns = [time.time_ns()]
    runtime = _NoRpcRuntime()
    store, gate = _pinned_gate(tmp_path / "policy.sqlite")
    service, group_store, run_bindings, registration, metadata = _registered_gated_service(
        tmp_path / "admitted",
        gate,
        now_ns=now_ns,
        runtime=runtime,
        budget_limit=Decimal("1"),
    )
    try:
        result = _submit(service, registration, metadata, now_ns)
        # Admission passed and the durable pre-dispatch budget rejection (the
        # first durable boundary after the gate) is the observed outcome.
        assert result["ok"] is True
        snapshot = GroupOrderSnapshot.from_payload(result["result"]["snapshot"])
        assert snapshot.state is GroupOrderState.REJECTED_BEFORE_DISPATCH
        assert run_bindings.get(_PORTFOLIO, _GROUP_ID) is not None
        assert runtime.calls == []
    finally:
        service.close()
        group_store.close()
        run_bindings.close()
        store.close()


def test_retry_replay_bypasses_the_gate_after_new_submit_consulted_it(tmp_path: Path) -> None:
    now_ns = [time.time_ns()]
    runtime = _NoRpcRuntime()
    recorder = _RecordingGate()
    service, group_store, run_bindings, registration, metadata = _registered_gated_service(
        tmp_path / "recorder",
        recorder,
        now_ns=now_ns,
        runtime=runtime,
        budget_limit=Decimal("1"),
    )
    try:
        first = _submit(service, registration, metadata, now_ns)
        assert first["ok"] is True
        assert recorder.calls == [(_PORTFOLIO, _GROUP_ID)]

        # The retry of the exact same payload must recover the persisted run
        # without re-evaluating group policy, even though the gate now denies.
        recorder.admit = False
        replay = _submit(service, registration, metadata, now_ns)
        assert replay["ok"] is True
        assert recorder.calls == [(_PORTFOLIO, _GROUP_ID)]

        # The same denying gate is still consulted for a different new group:
        # the bypass belongs to the retry path only.
        fresh = _submit(service, registration, metadata, now_ns, group_id="group-b")
        assert fresh == {"ok": False, "code": "GROUP_POLICY_DENIED", "result": {}}
        assert recorder.calls == [(_PORTFOLIO, _GROUP_ID), (_PORTFOLIO, "group-b")]
        assert run_bindings.get(_PORTFOLIO, "group-b") is None
    finally:
        service.close()
        group_store.close()
        run_bindings.close()


def test_retry_replay_bypasses_a_tampered_reference_gate(tmp_path: Path) -> None:
    now_ns = [time.time_ns()]
    runtime = _NoRpcRuntime()
    store, gate = _pinned_gate(tmp_path / "policy.sqlite")
    service, group_store, run_bindings, registration, metadata = _registered_gated_service(
        tmp_path / "tampered",
        gate,
        now_ns=now_ns,
        runtime=runtime,
        budget_limit=Decimal("1"),
    )
    try:
        first = _submit(service, registration, metadata, now_ns)
        assert first["ok"] is True
        assert run_bindings.get(_PORTFOLIO, _GROUP_ID) is not None

        # The stored document is now invalid: any evaluation would fail with
        # INVALID_DOCUMENT. The retry must not evaluate it at all.
        with sqlite3.connect(str(tmp_path / "policy.sqlite")) as probe:
            probe.execute(
                "UPDATE group_policy_versions SET document_json=? WHERE group_policy_ref=?",
                ('{"admitted_portfolios":7,"denied_group_ids":[]}', _POLICY_REF),
            )
        replay = _submit(service, registration, metadata, now_ns)
        assert replay["ok"] is True

        # Proof the tamper is live: a new group on the same service does hit
        # the gate and now fails closed with the phase-1 code.
        fresh = _submit(service, registration, metadata, now_ns, group_id="group-b")
        assert fresh == {"ok": False, "code": "INVALID_DOCUMENT", "result": {}}
        assert run_bindings.get(_PORTFOLIO, "group-b") is None
    finally:
        service.close()
        group_store.close()
        run_bindings.close()
        store.close()


# ---------------------------------------------------------------------------
# Layer 3: coordinator component built through service_components.
# ---------------------------------------------------------------------------


def _coordinator_components() -> dict:
    return {
        "execution": {
            "config_schema_version": 2,
            "settings": {
                "instance_id": _COORDINATOR_INSTANCE,
                "bind_ports": ["service.order_group_entry"],
                "options": {"role": "group_coordinator", "portfolio_ref": _PORTFOLIO},
            },
        }
    }


def _coordinator_provider(values: dict, calls: list[tuple[str, str, dict]]):
    def provider(component: str, instance_id: str, options, scope) -> ComponentResources:
        del scope
        calls.append((component, instance_id, dict(options)))
        return ComponentResources.borrowed(values, ready=lambda: True)

    return provider


def _build_coordinator_component(
    tmp_path: Path,
    *,
    gate_value: object,
    include_gate: bool,
    runtime: _NoRpcRuntime,
    now_ns: list[int],
    budget_limit: Decimal | None = None,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    stable_scope, target, metadata = _target(now_ns[0], budget_limit=budget_limit)
    group_store = SqliteOrderGroupStore(tmp_path / "groups.sqlite3")
    run_bindings = GroupOriginRunBindingStore(tmp_path / "group-runs.sqlite3")
    values: dict[str, object] = {
        "store": group_store,
        "targets": {stable_scope: target},
        "run_bindings": run_bindings,
    }
    if include_gate:
        values["group_admission"] = gate_value
    calls: list[tuple[str, str, dict]] = []
    prepared = validate_components(component_specs(_coordinator_components()))
    component = build_component(
        prepared[0],
        scope=stable_scope,
        resource_provider=_coordinator_provider(values, calls),
        borrowed_upstream=BorrowedServiceClient(runtime),
        service_identity=WireIdentity(WireRole.SERVICE, _COORDINATOR_PRINCIPAL, "coordinator-actor"),
    )
    return component, group_store, run_bindings, calls, metadata


def test_component_build_coordinator_enforces_the_provider_gate(tmp_path: Path) -> None:
    now_ns = [time.time_ns()]
    runtime = _NoRpcRuntime()
    store, gate = _pinned_gate(tmp_path / "policy.sqlite", admitted=("portfolio-other",))
    component, group_store, run_bindings, calls, metadata = _build_coordinator_component(
        tmp_path / "component",
        gate_value=gate,
        include_gate=True,
        runtime=runtime,
        now_ns=now_ns,
    )
    registration = _registration(now_ns[0])
    try:
        assert calls == [
            (
                "execution",
                _COORDINATOR_INSTANCE,
                {"role": "group_coordinator", "portfolio_ref": _PORTFOLIO},
            )
        ]
        component.maintain_registrations((registration,))
        denied = component.handlers["groups.submit"](
            registration.context, _arguments(now_ns[0], metadata)
        )
        assert denied == {"ok": False, "code": "GROUP_POLICY_SCOPE_DENIED", "result": {}}
        assert run_bindings.list_all() == ()
        assert group_store.get(_PORTFOLIO, _GROUP_ID) is None
        assert runtime.calls == []
        queried = component.handlers["groups.query"](registration.context, {"group_id": _GROUP_ID})
        assert queried == {"ok": True, "code": "OK", "result": {"found": False, "snapshot": None}}
    finally:
        component.close()
        group_store.close()
        run_bindings.close()
        store.close()


def test_component_build_refuses_null_gate_and_keeps_gate_free_builds_working(
    tmp_path: Path,
) -> None:
    now_ns = [time.time_ns()]
    runtime = _NoRpcRuntime()
    stable_scope, target, metadata = _target(now_ns[0], budget_limit=Decimal("1"))
    prepared = validate_components(component_specs(_coordinator_components()))

    def _attempt(values: dict):
        return build_component(
            prepared[0],
            scope=stable_scope,
            resource_provider=_coordinator_provider(values, []),
            borrowed_upstream=BorrowedServiceClient(runtime),
            service_identity=WireIdentity(
                WireRole.SERVICE, _COORDINATOR_PRINCIPAL, "coordinator-actor"
            ),
        )

    def _values(directory: Path, gate_marker: object) -> tuple[dict, object, object]:
        directory.mkdir()
        case_store = SqliteOrderGroupStore(directory / "groups.sqlite3")
        case_bindings = GroupOriginRunBindingStore(directory / "group-runs.sqlite3")
        values: dict[str, object] = {
            "store": case_store,
            "targets": {stable_scope: target},
            "run_bindings": case_bindings,
            "group_admission": gate_marker,
        }
        return values, case_store, case_bindings

    values, case_store, case_bindings = _values(tmp_path / "null-gate", None)
    try:
        with pytest.raises(ServiceComponentError) as null_gate:
            _attempt(values)
        assert null_gate.value.code == "GROUP_POLICY_RESOURCE_INVALID"
    finally:
        case_store.close()
        case_bindings.close()

    values, case_store, case_bindings = _values(tmp_path / "wrong-gate", object())
    try:
        with pytest.raises(ServiceExecutionGroupError) as wrong_gate:
            _attempt(values)
        assert wrong_gate.value.code == "GROUP_POLICY_RESOURCE_INVALID"
    finally:
        case_store.close()
        case_bindings.close()

    # Without the optional resource key the coordinator keeps its pre-phase-2
    # behavior: no gate object, and submission proceeds to the durable
    # pre-dispatch budget decision.
    component, group_store, run_bindings, calls, metadata = _build_coordinator_component(
        tmp_path / "gate-free",
        gate_value=None,
        include_gate=False,
        runtime=runtime,
        now_ns=now_ns,
        budget_limit=Decimal("1"),
    )
    registration = _registration(now_ns[0])
    try:
        component.maintain_registrations((registration,))
        result = component.handlers["groups.submit"](
            registration.context, _arguments(now_ns[0], metadata)
        )
        assert result["ok"] is True
        snapshot = GroupOrderSnapshot.from_payload(result["result"]["snapshot"])
        assert snapshot.state is GroupOrderState.REJECTED_BEFORE_DISPATCH
        assert run_bindings.get(_PORTFOLIO, _GROUP_ID) is not None
        assert runtime.calls == []
    finally:
        component.close()
        group_store.close()
        run_bindings.close()
