from __future__ import annotations
from bt_api_base.service_protocol import WIRE_PROTOCOL_VERSION as _WIRE_PROTOCOL_VERSION

from pathlib import Path
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py.service_components import (
    ComponentResources,
    ServiceCapability,
    ServiceComponent,
    ServiceComponentError,
)
from bt_api_py.service_host import ServiceHost
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceRegistry,
)


_INSTANCE = "execution-account-a"
_STRATEGY = WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-principal", "strategy-actor")
_OWNER = WireIdentity(WireRole.SERVICE, "owner-principal", "owner-service")
_SCOPE = WireScope(
    "paper:venue-a",
    account_ref="account-a",
    strategy_ref="strategy-a",
    run_id="run-a",
)
_STRATEGY_GRANT_SCOPE = WireScope(
    "paper:venue-a",
    account_ref="account-a",
    strategy_ref="strategy-a",
)
_ORDER_OPERATIONS = frozenset({"orders.submit", "orders.cancel"})
_OWNER_OPERATIONS = frozenset({"owner.cancel"})


def _request(operation, payload, *, identity, scope):
    _request.serial += 1
    control = operation.startswith("service.")
    return WireEnvelope(
        WireHeader(
            protocol_version=_WIRE_PROTOCOL_VERSION,
            kind=WireMessageKind.REQUEST,
            message_id=f"owner-cancel-{_request.serial}",
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation=operation,
            schema_id="sdk.service.control" if control else "sdk.service.call",
            schema_version=1,
            sent_at_utc="2026-10-21T00:00:00Z",
        ),
        payload,
    )


_request.serial = 0


def _register(registry, principal, identity, scope, *, port, scope_ref, operations, key):
    return registry.handle(
        principal,
        _request(
            "service.register",
            {
                "registration_key": key,
                "client_session_id": f"session-{key}",
                "instance_id": _INSTANCE,
                "port": port,
                "scope_ref": scope_ref,
                "operations": tuple(sorted(operations)),
            },
            identity=identity,
            scope=scope,
        ),
    )


def _call(registry, principal, identity, scope, registration, operation):
    return registry.handle(
        principal,
        _request(
            operation,
            {
                "registration_id": registration["registration_id"],
                "service_epoch": registration["service_epoch"],
                "instance_id": _INSTANCE,
                "arguments": {"command_id": "cancel-run-a"},
            },
            identity=identity,
            scope=scope,
        ),
    )


def _control(registry, principal, identity, scope, operation, registration):
    return registry.handle(
        principal,
        _request(
            operation,
            {
                "registration_id": registration["registration_id"],
                "service_epoch": registration["service_epoch"],
            },
            identity=identity,
            scope=scope,
        ),
    )


def _execution_component(*, readiness, cancel_readiness):
    order_capability = ServiceCapability(
        _INSTANCE,
        "service.order_entry",
        _ORDER_OPERATIONS,
        _ORDER_OPERATIONS,
    )
    owner_capability = ServiceCapability(
        _INSTANCE,
        "service.owner_control",
        _OWNER_OPERATIONS,
        _OWNER_OPERATIONS,
    )
    return ServiceComponent(
        component="execution",
        instance_id=_INSTANCE,
        capabilities=(order_capability, owner_capability),
        handlers={
            "orders.submit": lambda _context, _arguments: {"submitted": True},
            "orders.cancel": lambda _context, _arguments: {"cancelled": True},
            "owner.cancel": lambda _context, _arguments: {"cancelled": True},
        },
        internal_ports={},
        readiness=readiness,
        close=lambda: None,
        operation_readiness={"owner.cancel": cancel_readiness},
    )


def _owner_cancel_host(component, registry, *, other_readiness):
    host = object.__new__(ServiceHost)
    host._components = [component, SimpleNamespace(readiness=other_readiness)]
    host._component_maintenance_faults = set()
    host._component_delay_faults = set()
    host._paper_monitor_faults = set()
    host._atomic_intake_services = ()
    host._gateway_lease_faults = set()
    host._execution_parent_active = {}
    host._upstream = None
    host._upstream_faulted = False
    host._state = "started"
    host._registry = registry
    return host


def test_owner_cancel_host_gate_checks_healthy_and_exact_degraded_readiness():
    component_state = {"ready": True, "cancel_ready": True, "other_ready": True}
    resource_callback = lambda: True
    resources = ComponentResources.borrowed(
        {},
        ready=lambda: True,
        operation_readiness={"owner.cancel": resource_callback},
    )
    assert resources.operation_readiness["owner.cancel"] is resource_callback
    resources.close_resource()
    with pytest.raises(ServiceComponentError) as invalid_readiness:
        ComponentResources.borrowed(
            {},
            ready=lambda: True,
            operation_readiness={"orders.cancel": resource_callback},
        )
    assert invalid_readiness.value.code == "INVALID_OPERATION_READINESS"

    component = _execution_component(
        readiness=lambda: component_state["ready"],
        cancel_readiness=lambda: component_state["cancel_ready"],
    )
    registry = SimpleNamespace(ready=True)
    host = _owner_cancel_host(
        component, registry, other_readiness=lambda: component_state["other_ready"]
    )
    callback = component.operation_readiness["owner.cancel"]

    assert host._restricted_operation_ready(
        component, callback, operation="owner.cancel"
    ) is True
    assert host._restricted_operation_ready(
        component, callback, operation="orders.submit"
    ) is False
    component_state["cancel_ready"] = False
    assert host._restricted_operation_ready(
        component, callback, operation="owner.cancel"
    ) is False

    component_state.update(ready=False, cancel_ready=True)
    registry.ready = False
    assert host._restricted_operation_ready(
        component, callback, operation="owner.cancel"
    ) is True
    component_state["cancel_ready"] = 1
    assert host._restricted_operation_ready(
        component, callback, operation="owner.cancel"
    ) is False
    component_state["cancel_ready"] = True
    component_state["other_ready"] = False
    assert host._restricted_operation_ready(
        component, callback, operation="owner.cancel"
    ) is False
    component_state["other_ready"] = True
    host._component_maintenance_faults.add(_INSTANCE)
    assert host._restricted_operation_ready(
        component, callback, operation="owner.cancel"
    ) is False


def test_registry_degraded_owner_cancel_is_exact_and_keeps_other_writes_fenced(
    tmp_path: Path,
):
    now = [100.0]
    component_state = {"ready": True, "cancel_ready": True, "other_ready": True}
    component = _execution_component(
        readiness=lambda: component_state["ready"],
        cancel_readiness=lambda: component_state["cancel_ready"],
    )
    capabilities = component.capabilities
    strategy_resource = ResourceGrant(
        _INSTANCE,
        "service.order_entry",
        "strategy-grant",
        _STRATEGY_GRANT_SCOPE,
        _ORDER_OPERATIONS,
    )
    owner_resource = ResourceGrant(
        _INSTANCE,
        "service.owner_control",
        "owner-grant",
        _SCOPE,
        _OWNER_OPERATIONS,
    )
    registry = ServiceRegistry(
        tmp_path / "owner-cancel-readiness.sqlite",
        service_id="execution-service",
        capabilities=capabilities,
        grants={
            _STRATEGY.principal_ref: (PrincipalGrant(_STRATEGY, (strategy_resource,)),),
            _OWNER.principal_ref: (PrincipalGrant(_OWNER, (owner_resource,)),),
        },
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        lease_seconds=10,
        clock=lambda: now[0],
    )
    host = _owner_cancel_host(
        component,
        registry,
        other_readiness=lambda: component_state["other_ready"],
    )
    callback = component.operation_readiness["owner.cancel"]
    registry.register_handler(
        _INSTANCE,
        "orders.submit",
        component.handlers["orders.submit"],
    )
    registry.register_handler(
        _INSTANCE,
        "orders.cancel",
        component.handlers["orders.cancel"],
    )
    registry.register_handler(
        _INSTANCE,
        "owner.cancel",
        component.handlers["owner.cancel"],
        restricted_readiness=lambda: host._restricted_operation_ready(
            component, callback, operation="owner.cancel"
        ),
    )
    with pytest.raises(ValueError, match="restricted readiness requires"):
        registry.register_handler(
            _INSTANCE,
            "orders.cancel",
            lambda _context, _arguments: {},
            restricted_readiness=lambda: True,
        )

    try:
        strategy_reply = _register(
            registry,
            _STRATEGY.principal_ref,
            _STRATEGY,
            _SCOPE,
            port="service.order_entry",
            scope_ref="strategy-grant",
            operations=frozenset({"orders.submit"}),
            key="strategy-run",
        )
        assert strategy_reply["code"] == "OK"
        strategy_registration = strategy_reply["result"]
        owner_reply = _register(
            registry,
            _OWNER.principal_ref,
            _OWNER,
            _SCOPE,
            port="service.owner_control",
            scope_ref="owner-grant",
            operations=frozenset({"owner.cancel"}),
            key="owner-cancel",
        )
        assert owner_reply["code"] == "OK"
        owner_registration = owner_reply["result"]
        assert _call(
            registry,
            _OWNER.principal_ref,
            _OWNER,
            _SCOPE,
            owner_registration,
            "owner.cancel",
        )["code"] == "OK"
        component_state["cancel_ready"] = False
        assert _call(
            registry,
            _OWNER.principal_ref,
            _OWNER,
            _SCOPE,
            owner_registration,
            "owner.cancel",
        )["code"] == "POLICY_REQUIREMENT_UNMET"
        component_state["cancel_ready"] = True

        registry.set_ready(False)
        component_state["ready"] = False
        assert _call(
            registry,
            _OWNER.principal_ref,
            _OWNER,
            _SCOPE,
            owner_registration,
            "owner.cancel",
        )["code"] == "OK"
        for operation in ("orders.submit", "orders.cancel"):
            assert _call(
                registry,
                _STRATEGY.principal_ref,
                _STRATEGY,
                _SCOPE,
                strategy_registration,
                operation,
            )["code"] == "POLICY_REQUIREMENT_UNMET"
        component_state["other_ready"] = False
        assert _call(
            registry,
            _OWNER.principal_ref,
            _OWNER,
            _SCOPE,
            owner_registration,
            "owner.cancel",
        )["code"] == "POLICY_REQUIREMENT_UNMET"
        component_state["other_ready"] = True
        host._component_maintenance_faults.add(_INSTANCE)
        assert _call(
            registry,
            _OWNER.principal_ref,
            _OWNER,
            _SCOPE,
            owner_registration,
            "owner.cancel",
        )["code"] == "POLICY_REQUIREMENT_UNMET"
        host._component_maintenance_faults.clear()

        now[0] = 104.0
        renewed = _control(
            registry,
            _OWNER.principal_ref,
            _OWNER,
            _SCOPE,
            "service.renew",
            owner_registration,
        )
        assert renewed["code"] == "OK"
        assert renewed["result"]["registration_id"] == owner_registration["registration_id"]
        component_state["cancel_ready"] = False
        denied_readiness_renewal = _control(
            registry,
            _OWNER.principal_ref,
            _OWNER,
            _SCOPE,
            "service.renew",
            owner_registration,
        )
        assert denied_readiness_renewal["code"] == "POLICY_REQUIREMENT_UNMET"
        component_state["cancel_ready"] = True
        strategy_unregistered = _control(
            registry,
            _STRATEGY.principal_ref,
            _STRATEGY,
            _SCOPE,
            "service.unregister",
            strategy_registration,
        )
        assert strategy_unregistered["code"] == "OK"
        denied_unclaimed_renewal = _control(
            registry,
            _OWNER.principal_ref,
            _OWNER,
            _SCOPE,
            "service.renew",
            owner_registration,
        )
        assert denied_unclaimed_renewal["code"] == "POLICY_REQUIREMENT_UNMET"
        new_registration = _register(
            registry,
            _OWNER.principal_ref,
            _OWNER,
            _SCOPE,
            port="service.owner_control",
            scope_ref="owner-grant",
            operations=frozenset({"owner.cancel"}),
            key="new-while-unready",
        )
        assert new_registration["code"] == "POLICY_REQUIREMENT_UNMET"
    finally:
        registry.close()
        component.close()
