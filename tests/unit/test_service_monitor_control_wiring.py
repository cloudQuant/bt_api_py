"""Monitor control client and component wiring regressions."""

from __future__ import annotations

from dataclasses import replace

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py.service_components import (
    _ROLE_BUILTINS,
    component_specs,
    validate_components,
)
from bt_api_py.service_config import (
    ServiceConfigError,
    parse_client_config,
    parse_service_config,
)
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceOperationDeniedError,
    ServiceRegistry,
)
from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError

_SERVICE_ID = "monitor-service"
_SERVICE_INSTANCE = "monitor"
_CLIENT = WireIdentity(WireRole.APPLICATION_CLIENT, "monitor-app-principal", "monitor-app")
_PEER = WireIdentity(WireRole.SERVICE, "monitor-service-principal", _SERVICE_ID)
_SCOPE = WireScope("paper:monitor-control", portfolio_ref="portfolio-a")
_SCOPE_REF = "portfolio-a"
_PROFILE = "monitor-loopback"


def _service_config(*, role: str | None, bind_ports: list[str]):
    settings = {"instance_id": _SERVICE_INSTANCE, "bind_ports": bind_ports}
    if role is not None:
        settings["options"] = {"role": role}
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": "monitor-root",
        "transport_ref": "monitor-listener",
        "registration_policy_ref": "monitor-registration-v1",
        "service_requirements_ref": "monitor-requirements-v1",
        "components": {"monitor": {"config_schema_version": 2, "settings": settings}},
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "monitor-listener": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:7610"},
                    "security_profile_ref": "monitor-server-key",
                }
            },
        },
    }


def _client_config():
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "application_client",
            "registration": {"application_ref": _CLIENT.actor_ref},
            "clients": {
                "monitor": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "application.monitor",
                                "service_id": _SERVICE_ID,
                                "instance_id": _SERVICE_INSTANCE,
                                "scope_ref": _SCOPE_REF,
                                "transport_ref": _PROFILE,
                            }
                        ]
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    _PROFILE: {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7611"},
                        "security_profile_ref": "monitor-client-key",
                    }
                },
            },
        }
    )


class _LocalTransport:
    """Pass SDK envelopes to a real registry without opening a socket."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry
        self.requests = []
        self.responses = []
        self.closed = False

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        del timeout
        self.requests.append(request)
        result = self.registry.handle(_CLIENT.principal_ref, request)
        self.responses.append(result)
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id=f"reply-{request.header.message_id}",
                correlation_id=request.header.message_id,
                identity=_PEER,
            ),
            result,
        )

    def close(self) -> None:
        self.closed = True


def _registry(
    tmp_path,
    ports: tuple[str, ...],
    *,
    handlers=None,
    filename="monitor-registry.sqlite",
) -> ServiceRegistry:
    operations_by_port = {
        "service.monitor_query": frozenset({"monitor.query"}),
        "service.monitor_control": frozenset({"monitor.control"}),
    }
    capabilities = tuple(
        ServiceCapability(_SERVICE_INSTANCE, port, operations_by_port[port]) for port in ports
    )
    resources = tuple(
        ResourceGrant(
            _SERVICE_INSTANCE,
            capability.port,
            _SCOPE_REF,
            _SCOPE,
            capability.operations,
        )
        for capability in capabilities
    )
    registry = ServiceRegistry(
        tmp_path / filename,
        service_id=_SERVICE_ID,
        capabilities=capabilities,
        grants={_CLIENT.principal_ref: (PrincipalGrant(_CLIENT, resources),)},
        policy_version="monitor-policy-v1",
        requirements_version="monitor-requirements-v1",
    )
    default_handlers = {
        "monitor.query": lambda *_args: {"status": "visible"},
        "monitor.control": lambda *_args: {"accepted": True},
    }
    for capability in capabilities:
        for operation in capability.operations:
            registry.register_handler(
                _SERVICE_INSTANCE,
                operation,
                (handlers or {}).get(operation, default_handlers[operation]),
            )
    return registry


def _runtime(registry, transport, *, requested_operations=None):
    return ServiceClientRuntime(
        _client_config(),
        identity=_CLIENT,
        scope_resolver=lambda _: _SCOPE,
        peer_resolver=lambda _: _PEER,
        transport_factory=lambda *_: transport,
        requested_operations=requested_operations,
    )


def _registered_ports(transport: _LocalTransport) -> list[str]:
    return [
        request.payload["port"]
        for request in transport.requests
        if request.header.operation == "service.register"
    ]


def test_roleless_monitor_cannot_bind_control_and_owner_operations_are_private():
    with pytest.raises(ServiceConfigError) as error:
        parse_service_config(_service_config(role=None, bind_ports=["service.monitor_control"]))
    assert error.value.code == "PORT_ROLE_MISMATCH"
    with pytest.raises(ServiceConfigError) as internal_port_error:
        parse_service_config(_service_config(role="control", bind_ports=["service.owner_control"]))
    assert internal_port_error.value.code == "PORT_ROLE_MISMATCH"

    config = parse_service_config(
        _service_config(role="control", bind_ports=["service.monitor_control"])
    )
    prepared = validate_components(component_specs(config.components))[0]
    public_operations = frozenset().union(*prepared.operations_by_port.values())
    assert dict(prepared.operations_by_port) == {
        "service.monitor_control": frozenset({"monitor.control"})
    }
    assert _ROLE_BUILTINS[("monitor", "control")].internal_ports == frozenset()
    assert {"monitor.claim", "monitor.acknowledge"}.isdisjoint(public_operations)


def test_monitor_control_only_binding_needs_no_query_capability(tmp_path):
    registry = _registry(tmp_path, ("service.monitor_control",))
    transport = _LocalTransport(registry)
    runtime = _runtime(
        registry,
        transport,
        requested_operations={"application.monitor": frozenset({"monitor.control"})},
    )
    try:
        result = runtime.start()
        assert [binding.operations for binding in result.bindings] == [
            frozenset({"monitor.control"})
        ]
        assert _registered_ports(transport) == ["service.monitor_control"]
        assert runtime.call("application.monitor", "monitor.control", {}) == {"accepted": True}
        for operation in ("monitor.query", "monitor.claim", "monitor.acknowledge"):
            with pytest.raises(ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"):
                runtime.call("application.monitor", operation, {})
    finally:
        runtime.close()
        registry.close()


def test_required_control_only_binding_fails_when_control_capability_is_missing(tmp_path):
    registry = _registry(tmp_path, ("service.monitor_query",))
    transport = _LocalTransport(registry)
    runtime = _runtime(
        registry,
        transport,
        requested_operations={"application.monitor": frozenset({"monitor.control"})},
    )
    try:
        with pytest.raises(ServiceRuntimeError) as error:
            runtime.start()
        assert error.value.code == "CAPABILITY_UNAVAILABLE"
        assert _registered_ports(transport) == []
    finally:
        runtime.close()
        registry.close()


def test_default_monitor_binding_stays_read_only_when_control_is_available(tmp_path):
    registry = _registry(tmp_path, ("service.monitor_query", "service.monitor_control"))
    transport = _LocalTransport(registry)
    runtime = _runtime(registry, transport)
    try:
        result = runtime.start()
        assert [binding.operations for binding in result.bindings] == [frozenset({"monitor.query"})]
        assert _registered_ports(transport) == ["service.monitor_query"]
        assert runtime.call("application.monitor", "monitor.query", {}) == {"status": "visible"}
        for operation in ("monitor.control", "monitor.claim", "monitor.acknowledge"):
            with pytest.raises(ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"):
                runtime.call("application.monitor", operation, {})
    finally:
        runtime.close()
        registry.close()


def test_mixed_monitor_read_control_keeps_read_binding_when_control_is_unavailable(tmp_path):
    registry = _registry(tmp_path, ("service.monitor_query",))
    transport = _LocalTransport(registry)
    runtime = _runtime(
        registry,
        transport,
        requested_operations={
            "application.monitor": frozenset({"monitor.query", "monitor.control"})
        },
    )
    try:
        result = runtime.start()
        assert len(result.bindings) == 1
        assert result.bindings[0].operations == frozenset({"monitor.query"})
        assert result.degraded_count == 1
        assert result.degradation_codes == ("CAPABILITY_UNAVAILABLE",)
        assert _registered_ports(transport) == ["service.monitor_query"]
        assert runtime.call("application.monitor", "monitor.query", {}) == {"status": "visible"}
        with pytest.raises(ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"):
            runtime.call("application.monitor", "monitor.control", {})
    finally:
        runtime.close()
        registry.close()


def test_monitor_control_operation_denial_mapping_is_exact(tmp_path):
    def denied(*_args):
        raise ServiceOperationDeniedError()

    registry = _registry(
        tmp_path,
        ("service.monitor_query", "service.monitor_control"),
        handlers={"monitor.query": denied, "monitor.control": denied},
    )
    transport = _LocalTransport(registry)
    runtime = _runtime(
        registry,
        transport,
        requested_operations={
            "application.monitor": frozenset({"monitor.query", "monitor.control"})
        },
    )
    try:
        runtime.start()
        with pytest.raises(ServiceRuntimeError) as control_error:
            runtime.call("application.monitor", "monitor.control", {})
        assert control_error.value.code == "SCOPE_DENIED"
        assert transport.responses[-1]["code"] == "SCOPE_DENIED"
        with pytest.raises(ServiceRuntimeError) as query_error:
            runtime.call("application.monitor", "monitor.query", {})
        assert query_error.value.code == "BUSINESS_OUTCOME_UNKNOWN"
        assert transport.responses[-1]["code"] == "HANDLER_FAILED"
    finally:
        runtime.close()
        registry.close()

    def unexpected(*_args):
        raise RuntimeError("synthetic handler failure")

    registry = _registry(
        tmp_path,
        ("service.monitor_control",),
        handlers={"monitor.control": unexpected},
        filename="monitor-unexpected.sqlite",
    )
    transport = _LocalTransport(registry)
    runtime = _runtime(
        registry,
        transport,
        requested_operations={"application.monitor": frozenset({"monitor.control"})},
    )
    try:
        runtime.start()
        with pytest.raises(ServiceRuntimeError) as error:
            runtime.call("application.monitor", "monitor.control", {})
        assert error.value.code == "BUSINESS_OUTCOME_UNKNOWN"
        assert transport.responses[-1]["code"] == "HANDLER_FAILED"
    finally:
        runtime.close()
        registry.close()
