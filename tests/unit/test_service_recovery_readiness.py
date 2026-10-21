from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py.service_config import parse_client_config
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_components import (
    ComponentResources,
    ComponentSpec,
    PreparedComponent,
    ServiceComponent,
    ServiceComponentError,
    build_component,
)
from bt_api_py.service_host import ServiceHost
from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError


_CLIENT = WireIdentity(WireRole.STRATEGY_CLIENT, "curve-recovery", "strategy-client")
_PEER = WireIdentity(WireRole.SERVICE, "execution-service-principal", "execution-service")
_SCOPE_TEMPLATE = WireScope(
    "paper:venue-a", account_ref="account-a", strategy_ref="strategy-a"
)
_RECOVERY = "orders.submit_recovery_close"
_SUBMIT = "orders.submit"
_BASE_ACCOUNT_OWNER_OPERATIONS = frozenset(
    {"orders.submit", "orders.cancel", "orders.query", "owner.cancel"}
)


def _component(
    instance_id: str,
    operation: str,
    *,
    ready,
    operation_readiness=None,
    handler=None,
) -> ServiceComponent:
    capability = ServiceCapability(
        instance_id,
        "service.order_entry" if operation.startswith("orders.") else "service.account_read",
        frozenset({operation}),
        frozenset({operation}) if operation.startswith("orders.") else frozenset(),
    )
    return ServiceComponent(
        component="execution" if operation.startswith("orders.") else "gateway",
        instance_id=instance_id,
        capabilities=(capability,),
        handlers={operation: handler or (lambda _context, _args: {})},
        internal_ports={},
        readiness=ready,
        close=lambda: None,
        operation_readiness=operation_readiness or {},
    )


def test_host_recovery_gate_requires_degraded_owner_and_global_unready() -> None:
    state = {"target": False, "other": True, "recovery": True}
    readiness = lambda: state["recovery"]
    target = _component(
        "execution-account-a",
        _RECOVERY,
        ready=lambda: state["target"],
        operation_readiness={_RECOVERY: readiness},
    )
    other = _component("account-reader", "account.read", ready=lambda: state["other"])
    host = object.__new__(ServiceHost)
    host._components = [target, other]
    host._component_maintenance_faults = set()
    host._component_delay_faults = set()
    host._paper_monitor_faults = set()
    host._upstream = None
    host._upstream_faulted = False
    host._state = "started"
    host._registry = SimpleNamespace(ready=False)

    assert host._restricted_operation_ready(target, readiness) is True
    for host_state in ("new", "failed", "closed"):
        host._state = host_state
        assert host._restricted_operation_ready(target, readiness) is False
    host._state = "started"
    state["target"] = True
    assert host._restricted_operation_ready(target, readiness) is False
    state["target"] = False
    host._registry.ready = True
    assert host._restricted_operation_ready(target, readiness) is False
    host._registry.ready = False
    state["other"] = False
    assert host._restricted_operation_ready(target, readiness) is False
    state["other"] = True
    state["recovery"] = False
    assert host._restricted_operation_ready(target, readiness) is False
    state["recovery"] = True
    host._component_maintenance_faults.add("execution-account-a")
    assert host._restricted_operation_ready(target, readiness) is False


def _build_synthetic_execution_owner(admission, *, recovery_readiness):
    constructor_calls = []

    class FakeAdapter:
        supported_operations = _BASE_ACCOUNT_OWNER_OPERATIONS | {_RECOVERY}

        def __init__(self, _store, _targets, _dispatch, **kwargs):
            constructor_calls.append(kwargs)

        def recover_interrupted_dispatches(self):
            return ()

        def handle(self, _operation, **_kwargs):
            return {}

        def close(self):
            return None

    class FakeExecutionState:
        UNKNOWN = object()

    module = SimpleNamespace(
        SUPPORTED_OPERATIONS=FakeAdapter.supported_operations,
        ExecutionOrderEntryService=FakeAdapter,
        ExecutionRecord=type("FakeExecutionRecord", (), {}),
        ExecutionState=FakeExecutionState,
    )
    spec = ComponentSpec(
        "execution",
        "execution-account-a",
        ("service.order_entry",),
        {"role": "account_owner"},
    )
    operations = frozenset({_SUBMIT, "orders.cancel", _RECOVERY})
    prepared = PreparedComponent(
        spec=spec,
        options={},
        module=module,
        operations_by_port={"service.order_entry": operations},
        write_operations_by_port={"service.order_entry": operations},
    )
    resources = ComponentResources.borrowed(
        {
            "store": SimpleNamespace(path=":memory:"),
            "targets": {},
            "dispatch": object(),
            "admission": admission,
        },
        ready=lambda: True,
        operation_readiness=(
            {_RECOVERY: recovery_readiness}
            if recovery_readiness is not None
            else {}
        ),
    )
    component = build_component(
        prepared,
        scope=_SCOPE_TEMPLATE,
        resource_provider=lambda *_args: resources,
    )
    return component, constructor_calls


def test_account_owner_injects_only_paired_trusted_recovery_authorizers() -> None:
    baseline_authorizer = lambda *_args: True
    recovery_authorizer = lambda *_args: True
    ordinary_component, ordinary_calls = _build_synthetic_execution_owner(
        {
            "order_gate": object(),
            "basic_admission_gate": object(),
            "position_baseline_authorizer": baseline_authorizer,
        },
        recovery_readiness=None,
    )
    try:
        assert _RECOVERY not in ordinary_component.handlers
        assert ordinary_calls[0]["position_baseline_authorizer"] is baseline_authorizer
        assert "recovery_close_authorizer" not in ordinary_calls[0]
    finally:
        ordinary_component.close()

    component, constructor_calls = _build_synthetic_execution_owner(
        {
            "order_gate": object(),
            "basic_admission_gate": object(),
            "position_baseline_authorizer": baseline_authorizer,
            "recovery_close_authorizer": recovery_authorizer,
        },
        recovery_readiness=lambda: True,
    )
    try:
        assert len(constructor_calls) == 1
        assert constructor_calls[0]["position_baseline_authorizer"] is baseline_authorizer
        assert constructor_calls[0]["recovery_close_authorizer"] is recovery_authorizer
        assert _RECOVERY in component.handlers
        assert component.operation_readiness[_RECOVERY]() is True
    finally:
        component.close()

    invalid_admissions = (
        {"order_gate": object(), "basic_admission_gate": object()},
        {
            "order_gate": object(),
            "basic_admission_gate": object(),
            "position_baseline_authorizer": baseline_authorizer,
            "recovery_close_authorizer": recovery_authorizer,
            "unexpected": True,
        },
        {
            "order_gate": object(),
            "basic_admission_gate": object(),
            "position_baseline_authorizer": baseline_authorizer,
        },
        {
            "order_gate": object(),
            "basic_admission_gate": object(),
            "position_baseline_authorizer": baseline_authorizer,
            "recovery_close_authorizer": recovery_authorizer,
        },
    )
    readiness_values = (lambda: True, None, lambda: True, None)
    for admission, readiness in zip(invalid_admissions, readiness_values, strict=True):
        with pytest.raises(ServiceComponentError) as error:
            _build_synthetic_execution_owner(
                admission,
                recovery_readiness=readiness,
            )
        assert error.value.code == "INVALID_ADMISSION_RESOURCES"


class _RegistryTransport:
    def __init__(self, registry: ServiceRegistry, *, events: list[str]) -> None:
        self._registry = registry
        self.events = events
        self.closed = False

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        assert timeout > 0
        self.events.append(request.header.operation)
        body = self._registry.handle(_CLIENT.principal_ref, request)
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id=f"reply-{request.header.message_id}",
                correlation_id=request.header.message_id,
                identity=_PEER,
            ),
            body,
        )

    def close(self) -> None:
        self.closed = True


def _client_config():
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": "strategy-a"},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "broker.orders",
                                "service_id": "execution-service",
                                "instance_id": "execution-account-a",
                                "scope_ref": "account-grant-a",
                                "account_ref": "account-a",
                                "transport_ref": "local-registry",
                            }
                        ],
                        "options": {"request_timeout_ms": 1000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "local-registry": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7601"},
                        "security_profile_ref": "synthetic-local-test",
                    }
                },
            },
        }
    )


def test_recovery_close_is_not_added_to_default_runtime_profile(tmp_path: Path) -> None:
    operations = frozenset({_SUBMIT, _RECOVERY})
    capability = ServiceCapability(
        "execution-account-a", "service.order_entry", operations, operations
    )
    resource = ResourceGrant(
        "execution-account-a",
        "service.order_entry",
        "account-grant-a",
        _SCOPE_TEMPLATE,
        operations,
    )
    registry = ServiceRegistry(
        tmp_path / "runtime-recovery-default.sqlite",
        service_id="execution-service",
        capabilities=(capability,),
        grants={_CLIENT.principal_ref: (PrincipalGrant(_CLIENT, (resource,)),)},
        policy_version="access-v1",
        requirements_version="readiness-v1",
        lease_seconds=10,
    )
    registry.register_handler("execution-account-a", _SUBMIT, lambda _context, _args: {})
    registry.register_handler(
        "execution-account-a",
        _RECOVERY,
        lambda _context, _args: {},
        restricted_readiness=lambda: True,
    )
    runtime = ServiceClientRuntime(
        _client_config(),
        identity=_CLIENT,
        scope_resolver=lambda _scope_ref: _SCOPE_TEMPLATE,
        peer_resolver=lambda _service_id: _PEER,
        transport_factory=lambda _profile, _peer: _RegistryTransport(
            registry, events=[]
        ),
        run_id="run-default-a",
    )
    try:
        runtime.start()
        assert runtime.bindings[0].operations == frozenset({_SUBMIT})
    finally:
        if runtime.state not in {"closed", "close_failed"}:
            runtime.close()
        registry.close()


def test_runtime_maintain_and_recovery_call_work_across_due_renewal(tmp_path: Path) -> None:
    server_time = [100.0]
    local_time = [0.0]
    recovery_allowed = [True]
    operations = frozenset({_SUBMIT, _RECOVERY})
    capability = ServiceCapability(
        "execution-account-a",
        "service.order_entry",
        operations,
        operations,
    )
    resource = ResourceGrant(
        "execution-account-a",
        "service.order_entry",
        "account-grant-a",
        _SCOPE_TEMPLATE,
        operations,
    )
    registry = ServiceRegistry(
        tmp_path / "runtime-recovery.sqlite",
        service_id="execution-service",
        capabilities=(capability,),
        grants={_CLIENT.principal_ref: (PrincipalGrant(_CLIENT, (resource,)),)},
        policy_version="access-v1",
        requirements_version="readiness-v1",
        lease_seconds=10,
        clock=lambda: server_time[0],
    )
    calls: list[str] = []
    component_readiness = [False]
    component = _component(
        "execution-account-a",
        _RECOVERY,
        ready=lambda: component_readiness[0],
        operation_readiness={_RECOVERY: lambda: recovery_allowed[0]},
        handler=lambda _context, _args: calls.append("recovery") or {"closed": True},
    )
    host = object.__new__(ServiceHost)
    host._components = [component]
    host._component_maintenance_faults = set()
    host._component_delay_faults = set()
    host._paper_monitor_faults = set()
    host._upstream = None
    host._upstream_faulted = False
    host._state = "started"
    host._registry = registry
    operation_readiness = component.operation_readiness[_RECOVERY]
    registry.register_handler(
        "execution-account-a",
        _SUBMIT,
        lambda _context, _args: calls.append("submit") or {},
    )
    registry.register_handler(
        "execution-account-a",
        _RECOVERY,
        component.handlers[_RECOVERY],
        restricted_readiness=lambda: host._restricted_operation_ready(
            component, operation_readiness
        ),
    )
    events: list[str] = []
    transport: list[_RegistryTransport] = []

    def transport_factory(_profile, peer):
        assert peer == _PEER
        item = _RegistryTransport(registry, events=events)
        transport.append(item)
        return item

    runtime = ServiceClientRuntime(
        _client_config(),
        identity=_CLIENT,
        scope_resolver=lambda _scope_ref: _SCOPE_TEMPLATE,
        peer_resolver=lambda _service_id: _PEER,
        transport_factory=transport_factory,
        run_id="run-a",
        requested_operations={"broker.orders": frozenset({_RECOVERY})},
        monotonic_clock=lambda: local_time[0],
    )
    try:
        runtime.start()
        binding = runtime.bindings[0]
        assert binding.operations == frozenset({_RECOVERY})
        original_registration_id = binding.registration_id
        recovery_arguments = {
            "target_intent_id": "target-a",
            "recovery_intent_id": "close-a",
        }
        with pytest.raises(ServiceRuntimeError) as globally_ready_error:
            runtime.call("broker.orders", _RECOVERY, recovery_arguments)
        assert globally_ready_error.value.code == "POLICY_REQUIREMENT_UNMET"

        # During the first part of the lease, maintenance is not due and the
        # already-held explicit recovery route remains usable while ordinary
        # Registry operations are fenced.
        registry.set_ready(False)
        before_due = binding.renewal_due_at - 0.01
        local_time[0] = before_due
        server_time[0] = 100.0 + before_due
        runtime.maintain()
        assert "service.renew" not in events
        assert runtime.call("broker.orders", _RECOVERY, recovery_arguments) == {
            "closed": True
        }
        assert calls == ["recovery"]

        # Once renewal is due, the same registration can renew only because
        # its sole granted restricted write still passes the owner callback.
        after_due = binding.renewal_due_at + 0.01
        local_time[0] = after_due
        server_time[0] = 100.0 + after_due
        runtime.maintain()
        renewed = runtime.bindings[0]
        assert runtime.state == "registered"
        assert renewed.registration_id == original_registration_id
        assert renewed.operations == frozenset({_RECOVERY})
        assert renewed.lease_deadline > local_time[0]
        assert events.count("service.renew") == 1
        assert runtime.call("broker.orders", _RECOVERY, recovery_arguments) == {
            "closed": True
        }
        assert calls == ["recovery", "recovery"]

        # A false callback rejects renewal. The client intentionally fails
        # closed even though its old local lease still has time remaining.
        recovery_allowed[0] = False
        next_due = renewed.renewal_due_at + 0.01
        local_time[0] = next_due
        server_time[0] = 100.0 + next_due
        with pytest.raises(ServiceRuntimeError) as exc_info:
            runtime.maintain()
        assert exc_info.value.code == "POLICY_REQUIREMENT_UNMET"
        assert runtime.state == "failed"
        assert renewed.lease_deadline > local_time[0]
        with pytest.raises(ServiceRuntimeError) as call_error:
            runtime.call("broker.orders", _RECOVERY, {})
        assert call_error.value.code == "SESSION_NOT_REGISTERED"
        assert calls == ["recovery", "recovery"]
    finally:
        if runtime.state not in {"closed", "close_failed"}:
            runtime.close()
        registry.close()
