"""Fixed configuration and handler wiring for the execution account-owner role."""

from __future__ import annotations

from decimal import Decimal

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    CancelObservation,
    ContractValidationError,
    ExecutionScope,
    ExecutionState,
    OrderIntent,
    ProviderObservation,
    SqliteExecutionStore,
)

from bt_api_py.service_components import (
    ComponentResources,
    ServiceComponentError,
    build_component,
    component_specs,
    validate_components,
)
from bt_api_py.service_registry import ServiceCallContext
from tests.unit.test_service_host import _service_config

_DOMAIN = "paper:demo"
_ACCOUNT = "account-a"
_STRATEGY = "strategy-a"
_PRINCIPAL = "curve-execution-principal"
_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)


def _execution_scope() -> ExecutionScope:
    return ExecutionScope("PAPER", "simulation", _ACCOUNT, _STRATEGY, "20261014")


class _Provider:
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.cancelled: list[str] = []

    def submit(self, intent: OrderIntent) -> ProviderObservation:
        self.submitted.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, "provider-order-1")

    def cancel(self, intent) -> CancelObservation:
        self.cancelled.append(intent.cancel_id)
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


def _prepared(options=None, ports=("service.order_entry", "service.order_query")):
    config = _service_config(
        {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "execution-owner",
                    "bind_ports": list(ports),
                    "options": options or {"role": "account_owner"},
                },
            }
        },
        service_id="execution-service",
    )
    return validate_components(component_specs(config.components))[0]


def _arguments(intent_id: str = "intent-1") -> dict[str, object]:
    return {
        "intent_id": intent_id,
        "signal_id": f"signal-{intent_id}",
        "instrument": "BTC-USDT",
        "side": "BUY",
        "position_effect": "OPEN",
        "order_type": "LIMIT",
        "quantity": "2",
        "price": "50000",
        "time_in_force": "GTC",
        "reduce_only": False,
        "metadata_version": "metadata-v1",
        "deadline_ns": None,
        "tags": {},
    }


def _resources(store, provider, *, admission=None):
    return ComponentResources.borrowed(
        {
            "store": store,
            "targets": {
                WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY): _execution_scope()
            },
            "dispatch": provider,
            "admission": {"allow_unprotected": True} if admission is None else admission,
        },
        ready=lambda: True,
    )


def test_account_owner_role_declares_only_the_write_and_query_operations():
    prepared = _prepared()

    assert prepared.operations_by_port == {
        "service.order_entry": frozenset(
            {"orders.submit", "orders.cancel", "orders.submit_approved_target"}
        ),
        "service.order_query": frozenset(
            {"orders.query", "execution.account_books.query"}
        ),
    }
    assert prepared.options == {"role": "account_owner"}


def test_owner_cancel_is_exposed_only_on_the_separate_owner_control_port():
    prepared = _prepared(
        ports=("service.order_entry", "service.order_query", "service.owner_control")
    )

    assert prepared.operations_by_port == {
        "service.order_entry": frozenset(
            {"orders.submit", "orders.cancel", "orders.submit_approved_target"}
        ),
        "service.order_query": frozenset(
            {"orders.query", "execution.account_books.query"}
        ),
        "service.owner_control": frozenset({"owner.cancel", "owner.reduce"}),
    }
    assert "owner.cancel" not in prepared.operations_by_port["service.order_entry"]


def test_owner_control_handler_uses_injected_quota_admission(tmp_path):
    store = SqliteExecutionStore(tmp_path / "owner-control-handler.sqlite3")
    provider = _Provider()
    quota_calls = []

    def quota_admission(*args):
        quota_calls.append(args)
        return None

    prepared = _prepared(
        ports=("service.order_entry", "service.order_query", "service.owner_control")
    )
    component = build_component(
        prepared,
        scope=_SCOPE,
        resource_provider=lambda *_: _resources(
            store,
            provider,
            admission={
                "allow_unprotected": True,
                "cancel_quota_admission": quota_admission,
            },
        ),
    )
    run_scope = WireScope(
        _DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY, run_id="run-owner-control"
    )
    try:
        strategy_context = ServiceCallContext(
            WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY),
            run_scope,
            "registration-a",
            _PRINCIPAL,
        )
        assert component.handlers["orders.submit"](
            strategy_context, _arguments("owner-control-order")
        )["ok"] is True

        owner_identity = WireIdentity(WireRole.SERVICE, "execution-owner-service", "operator-a")
        owner_context = ServiceCallContext(
            owner_identity,
            run_scope,
            "registration-owner",
            owner_identity.principal_ref,
        )
        reply = component.handlers["owner.cancel"](
            owner_context, {"command_id": "owner-control-command"}
        )
        assert reply["ok"] is True
        assert len(provider.cancelled) == 1
        assert len(quota_calls) == 1
        account, strategy, run_id, instrument, operation, deadline = quota_calls[0]
        assert (account, strategy, run_id, instrument, operation) == (
            _ACCOUNT,
            _STRATEGY,
            "run-owner-control",
            "BTC-USDT",
            "CANCEL",
        )
        assert type(deadline) is int and deadline > 0
    finally:
        component.close()
        store.close()


def test_account_owner_binding_only_the_query_port_advertises_no_write_operation():
    prepared = _prepared(ports=("service.order_query",))

    assert prepared.operations_by_port == {
        "service.order_query": frozenset(
            {"orders.query", "execution.account_books.query"}
        )
    }


def test_order_entry_handler_keeps_trusted_attribution_and_serves_its_own_facts(tmp_path):
    store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    provider = _Provider()
    component = build_component(
        _prepared(),
        scope=_SCOPE,
        resource_provider=lambda *_: _resources(store, provider),
    )
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY)
    context = ServiceCallContext(identity, _SCOPE, "registration-a", _PRINCIPAL)
    try:
        reply = component.handlers["orders.submit"](context, _arguments())
        assert reply["ok"] is True
        assert reply["result"]["record"]["state"] == ExecutionState.ACKED.value
        assert provider.submitted == ["intent-1"]

        duplicate = component.handlers["orders.submit"](context, _arguments())
        assert duplicate == reply
        assert provider.submitted == ["intent-1"]

        queried = component.handlers["orders.query"](context, {"intent_id": "intent-1"})
        assert queried["result"]["record"]["state"] == ExecutionState.ACKED.value

        forged = dict(_arguments("intent-2"))
        forged["scope"] = _execution_scope().to_payload()
        assert component.handlers["orders.submit"](context, forged)["code"] == "INVALID_REQUEST"
        assert provider.submitted == ["intent-1"]

        cancel = component.handlers["orders.cancel"](
            context,
            {
                "cancel_id": "cancel-1",
                "target_intent_id": "intent-1",
                "provider_order_id": "provider-order-1",
                "metadata_version": "metadata-v1",
                "tags": {},
            },
        )
        assert cancel["ok"] is True
        assert provider.cancelled == ["cancel-1"]

        stored = store.get("intent-1", scope=_execution_scope())
        assert stored is not None and stored.scope_key == _execution_scope().key
        intent = store.get_intent("intent-1", scope=_execution_scope())
        assert intent is not None and intent.quantity == Decimal("2")
    finally:
        component.close()
        store.close()


def test_admission_policy_is_required_for_a_provider_capable_write_route(tmp_path):
    store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    provider = _Provider()
    try:
        with pytest.raises(ServiceComponentError, match="ADMISSION_POLICY_REQUIRED"):
            build_component(
                _prepared(),
                scope=_SCOPE,
                resource_provider=lambda *_: _resources(
                    store, provider, admission={"order_gate": None, "cancel_gate": None}
                ),
            )
        with pytest.raises(ServiceComponentError, match="INVALID_ADMISSION_RESOURCES"):
            build_component(
                _prepared(),
                scope=_SCOPE,
                resource_provider=lambda *_: _resources(
                    store, provider, admission={"allow_unprotected": "yes"}
                ),
            )
        with pytest.raises(ServiceComponentError, match="INVALID_ADMISSION_RESOURCES"):
            build_component(
                _prepared(),
                scope=_SCOPE,
                resource_provider=lambda *_: _resources(
                    store, provider, admission={"unknown_key": True}
                ),
            )
    finally:
        store.close()


def test_owned_resources_are_closed_when_the_dispatch_port_is_invalid(tmp_path):
    store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    closed: list[bool] = []
    resources = ComponentResources.owned(
        {
            "store": store,
            "targets": {_SCOPE: _execution_scope()},
            "dispatch": object(),
            "admission": {"allow_unprotected": True},
        },
        ready=lambda: True,
        close=lambda: closed.append(True),
    )
    try:
        with pytest.raises(ContractValidationError, match="dispatch port"):
            build_component(_prepared(), scope=_SCOPE, resource_provider=lambda *_: resources)
        assert closed == [True]
    finally:
        store.close()


def test_order_entry_requires_every_declared_resource_key(tmp_path):
    store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    try:
        with pytest.raises(ServiceComponentError, match="RESOURCE_KEYS_MISMATCH"):
            build_component(
                _prepared(),
                scope=_SCOPE,
                resource_provider=lambda *_: ComponentResources.borrowed(
                    {"store": store, "targets": {}}, ready=lambda: True
                ),
            )
    finally:
        store.close()
