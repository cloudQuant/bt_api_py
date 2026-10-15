"""Approved-target SDK wiring without AFI, Risk, or provider side effects."""

from __future__ import annotations

from decimal import Decimal

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    ApprovedTargetAuthorityV1,
    ApprovedTargetOwnerBindingsV1,
    ApprovedTargetOwnerBindingV1,
    CancelObservation,
    ExecutionAdmissionValidatorRegistry,
    ExecutionBasicAdmissionGate,
    ExecutionProviderValidatorV1,
    ExecutionScope,
    OrderIntent,
    OrderType,
    PositionEffect,
    ProviderObservation,
    Side,
    SqliteExecutionStore,
    TimeInForce,
)
from bt_api_execution.approved_target import ProtectedApprovedTargetScopesV1

from bt_api_py.service_components import (
    ComponentResources,
    ServiceComponentError,
    build_component,
    component_specs,
    validate_components,
)
from bt_api_py.service_registry import ServiceCallContext

_WIRE_SCOPE = WireScope(
    "paper-domain",
    account_ref="account-a",
    strategy_ref="strategy-a",
    portfolio_ref="portfolio-a",
)
_EXECUTION_SCOPE = ExecutionScope("SIM", "simulation", "account-a", "strategy-a")
_PRINCIPAL = "strategy-principal"
_IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, "actor-a")


class _Provider:
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.authorized_submitted: list[str] = []
        self.cancelled: list[str] = []

    def submit(self, intent: OrderIntent) -> ProviderObservation:
        self.submitted.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, "provider-order-1")

    def submit_authorized(self, intent: OrderIntent, _authorization) -> ProviderObservation:
        self.authorized_submitted.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, "provider-order-1")

    def cancel(self, intent) -> CancelObservation:
        self.cancelled.append(intent.cancel_id)
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


class _RiskGate:
    def reserve(self, *_args, **_kwargs):
        raise AssertionError("protected ordinary submit must stop before Risk reserve")

    def claim_for_dispatch(self, *_args, **_kwargs):
        raise AssertionError("protected ordinary submit must stop before Risk claim")

    def release(self, *_args, **_kwargs):
        raise AssertionError("protected ordinary submit must not release an unmade reservation")


class _AuthorityPort:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def reserve_approved_target_operation(self, **_arguments):
        self.calls.append("reserve")
        raise AssertionError("unapproved ordinary submit must not call authority")

    def resolve_approved_target_v1(self, **_arguments):
        self.calls.append("resolve")
        raise AssertionError("unapproved ordinary submit must not call authority")

    def consume_approved_target_v1(self, **_arguments):
        self.calls.append("consume")
        raise AssertionError("unapproved ordinary submit must not call authority")


def _prepared(
    *,
    ports: tuple[str, ...] = ("service.order_entry", "service.order_query"),
    approved_target_required: bool | None = None,
):
    options: dict[str, object] = {"role": "account_owner"}
    if approved_target_required is not None:
        options["approved_target_required"] = approved_target_required
    specs = component_specs(
        {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "execution-owner",
                    "bind_ports": list(ports),
                    "options": options,
                },
            }
        }
    )
    return validate_components(specs)[0]


def _resources(
    store: SqliteExecutionStore,
    provider: _Provider,
    *,
    admission: dict[str, object],
    authority: object = None,
    include_authority: bool = False,
) -> ComponentResources:
    values: dict[str, object] = {
        "store": store,
        "targets": {_WIRE_SCOPE: _EXECUTION_SCOPE},
        "dispatch": provider,
        "admission": admission,
    }
    if include_authority:
        values["approved_target_authority"] = authority
    return ComponentResources.borrowed(values, ready=lambda: True)


def _approved_authority() -> tuple[ApprovedTargetAuthorityV1, _AuthorityPort]:
    port = _AuthorityPort()
    binding = ApprovedTargetOwnerBindingV1(
        principal_ref=_PRINCIPAL,
        identity=_IDENTITY,
        wire_scope=_WIRE_SCOPE,
        owner_subject="owner-subject-a",
        expected_scope_ref="approved-scope-a-v1",
    )
    authority = ApprovedTargetAuthorityV1(
        port,
        ApprovedTargetOwnerBindingsV1((binding,)),
        ProtectedApprovedTargetScopesV1((_WIRE_SCOPE,)),
    )
    return authority, port


def _basic_gate(store: SqliteExecutionStore, provider: _Provider) -> ExecutionBasicAdmissionGate:
    validator = ExecutionProviderValidatorV1(
        provider_id="SIM",
        validator_id="sdk-approved-target-test",
        version="1",
        dispatch_port=provider,
        instrument_map={"BTC-USDT": "BTC-USDT"},
        quantity_steps={"BTC-USDT": Decimal("0.1")},
        max_quantities={"BTC-USDT": Decimal("10")},
        price_ticks={"BTC-USDT": Decimal("0.01")},
        supported_order_types=(OrderType.LIMIT,),
        supported_time_in_force=(TimeInForce.GTC,),
        supported_actions=((Side.BUY, PositionEffect.OPEN),),
        supports_reduce_only=False,
    )
    return ExecutionBasicAdmissionGate(
        store, ExecutionAdmissionValidatorRegistry((validator,))
    )


def _arguments(intent_id: str = "intent-a") -> dict[str, object]:
    return {
        "intent_id": intent_id,
        "signal_id": "signal-a",
        "instrument": "BTC-USDT",
        "side": "BUY",
        "position_effect": "OPEN",
        "order_type": "LIMIT",
        "quantity": "1",
        "price": "10",
        "time_in_force": "GTC",
        "reduce_only": False,
        "metadata_version": "metadata-v1",
        "deadline_ns": None,
        "tags": {},
    }


def test_legacy_account_owner_does_not_advertise_or_route_approved_operation(tmp_path):
    store = SqliteExecutionStore(tmp_path / "legacy.sqlite3")
    provider = _Provider()
    component = build_component(
        _prepared(),
        scope=_WIRE_SCOPE,
        resource_provider=lambda *_: _resources(
            store, provider, admission={"allow_unprotected": True}
        ),
    )
    try:
        entry = next(item for item in component.capabilities if item.port == "service.order_entry")
        assert entry.operations == frozenset({"orders.submit", "orders.cancel"})
        assert entry.write_operations == frozenset({"orders.submit", "orders.cancel"})
        assert "orders.submit_approved_target" not in component.handlers
        assert provider.submitted == []
    finally:
        component.close()
        store.close()


def test_configured_protected_scope_blocks_ordinary_submit_before_any_gate_or_provider(tmp_path):
    store = SqliteExecutionStore(tmp_path / "protected.sqlite3")
    provider = _Provider()
    authority, authority_port = _approved_authority()
    risk = _RiskGate()
    component = build_component(
        _prepared(approved_target_required=True),
        scope=_WIRE_SCOPE,
        resource_provider=lambda *_: _resources(
            store,
            provider,
            admission={
                "order_gate": risk,
                "basic_admission_gate": _basic_gate(store, provider),
            },
            authority=authority,
            include_authority=True,
        ),
    )
    context = ServiceCallContext(_IDENTITY, _WIRE_SCOPE, "registration-a", _PRINCIPAL)
    try:
        entry = next(item for item in component.capabilities if item.port == "service.order_entry")
        assert entry.operations == frozenset(
            {"orders.submit", "orders.cancel", "orders.submit_approved_target"}
        )
        assert "orders.submit_approved_target" in component.handlers
        reply = component.handlers["orders.submit"](context, _arguments())
        assert reply["ok"] is False and reply["code"] == "APPROVAL_REQUIRED"
        assert provider.submitted == []
        assert provider.authorized_submitted == []
        assert authority_port.calls == []
    finally:
        component.close()
        store.close()


def test_approved_target_required_fails_startup_without_authority_and_tracks_cleanup(tmp_path):
    store = SqliteExecutionStore(tmp_path / "required-authority.sqlite3")
    provider = _Provider()
    borrowed = _resources(
        store, provider, admission={"allow_unprotected": True}
    )
    cleanup: list[str] = []
    resources = ComponentResources.owned(
        borrowed.values,
        ready=lambda: True,
        close=lambda: cleanup.append("closed"),
    )
    tracked: list[ComponentResources] = []

    try:
        with pytest.raises(
            ServiceComponentError, match="APPROVED_TARGET_AUTHORITY_REQUIRED"
        ):
            build_component(
                _prepared(approved_target_required=True),
                scope=_WIRE_SCOPE,
                resource_provider=lambda *_: resources,
                on_resources=tracked.append,
            )
        assert len(tracked) == 1
        tracked[0].close()
        assert cleanup == ["closed"]
        assert provider.submitted == []
        assert provider.authorized_submitted == []
    finally:
        store.close()


def test_approved_target_required_is_invalid_without_write_port():
    with pytest.raises(ServiceComponentError, match="COMPONENT_OPTIONS_INVALID"):
        _prepared(ports=("service.order_query",), approved_target_required=True)


@pytest.mark.parametrize(
    ("ports", "authority", "include_authority", "expected_code"),
    [
        (
            ("service.order_entry", "service.order_query"),
            None,
            True,
            "INVALID_APPROVED_TARGET_AUTHORITY_RESOURCE",
        ),
        (
            ("service.order_query",),
            object(),
            True,
            "RESOURCE_KEYS_MISMATCH",
        ),
    ],
)
def test_authority_resource_is_rejected_when_null_or_misrouted(
    tmp_path, ports, authority, include_authority, expected_code
):
    store = SqliteExecutionStore(tmp_path / f"invalid-{len(ports)}.sqlite3")
    provider = _Provider()
    try:
        with pytest.raises(ServiceComponentError, match=expected_code):
            build_component(
                _prepared(ports=ports),
                scope=_WIRE_SCOPE,
                resource_provider=lambda *_: _resources(
                    store,
                    provider,
                    admission={"allow_unprotected": True},
                    authority=authority,
                    include_authority=include_authority,
                ),
            )
        assert provider.submitted == []
        assert provider.authorized_submitted == []
    finally:
        store.close()
