"""Strategy-only, portfolio-scoped read access to execution-group monitoring."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py.service_config import ServiceConfigError, parse_client_config, parse_service_config
from bt_api_py.service_deployment import MOUNT_PORTS, OPTIONAL_MOUNT_PORTS, validate_deployment
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError

_SERVICE_ID = "monitor-service"
_INSTANCE_ID = "monitor-worker"
_SCOPE_REF = "portfolio-grant-a"
_PORTFOLIO_REF = "portfolio-a"
_CLIENT = WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-principal-a", "strategy-a")
_PEER = WireIdentity(WireRole.SERVICE, "monitor-service-principal", _SERVICE_ID)
_TRUSTED_SCOPE = WireScope(
    "paper-domain", strategy_ref="strategy-a", portfolio_ref=_PORTFOLIO_REF
)
_MONITOR_CAPABILITIES = frozenset(
    {"monitor.query", "monitor.read_after", "telemetry.snapshot"}
)
_READ_PAGE = {
    "requested_cursor": 0,
    "next_cursor": 1,
    "has_more": False,
    "events": [
        {
            "event_id": "event-a",
            "sequence": 1,
            "event_type": "execution.group.residual",
            "data": {"portfolio_ref": _PORTFOLIO_REF},
        }
    ],
}


def _client_config(
    *,
    mount: str = "broker.group_monitor",
    alias: str = "monitor",
    process_role: str = "strategy_client",
    include_portfolio: bool = True,
):
    binding = {
        "mount": mount,
        "service_id": _SERVICE_ID,
        "instance_id": _INSTANCE_ID,
        "scope_ref": _SCOPE_REF,
        "transport_ref": "monitor-connect",
    }
    if include_portfolio:
        binding["portfolio_ref"] = _PORTFOLIO_REF
    registration = (
        {"strategy_ref": "strategy-a"}
        if process_role == "strategy_client"
        else {"application_ref": "application-a"}
    )
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": process_role,
            "registration": registration,
            "clients": {
                alias: {
                    "config_schema_version": 1,
                    "settings": {"bindings": [binding]},
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "monitor-connect": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7101"},
                        "security_profile_ref": "strategy-monitor-keys",
                    }
                },
            },
        }
    )


def _monitor_service():
    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": _SERVICE_ID,
            "scope_ref": "monitor-root-scope",
            "transport_ref": "monitor-listener",
            "registration_policy_ref": "monitor-access-v1",
            "service_requirements_ref": "monitor-readiness-v1",
            "components": {
                "monitor": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": _INSTANCE_ID,
                        "bind_ports": ["service.monitor_query"],
                        "options": {},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "monitor-listener": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": "tcp://127.0.0.1:7101"},
                        "security_profile_ref": "monitor-server-keys",
                    }
                },
            },
        }
    )


class _RegistryTransport:
    """Synthetic transport leaf which still calls the real ServiceRegistry."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry
        self.requests = []
        self.closed = False

    def request(self, request, *, timeout):
        self.requests.append(request)
        response = self.registry.handle(_CLIENT.principal_ref, request)
        reply_header = replace(
            request.header,
            kind=WireMessageKind.REPLY,
            message_id="reply-" + request.header.message_id,
            correlation_id=request.header.message_id,
            identity=_PEER,
        )
        return WireEnvelope(reply_header, response)

    def close(self) -> None:
        self.closed = True


def test_strategy_group_monitor_runs_through_deployment_runtime_and_registry(tmp_path):
    client = _client_config()
    service = _monitor_service()
    plan = validate_deployment({_SERVICE_ID: service}, (client,))

    request = plan.client_binding_requests[0]
    assert MOUNT_PORTS["broker.group_monitor"] == ("service.monitor_query",)
    assert OPTIONAL_MOUNT_PORTS.get("broker.group_monitor", ()) == ()
    assert OPTIONAL_MOUNT_PORTS["application.monitor"] == ("service.monitor_control",)
    assert request.mount == "broker.group_monitor"
    assert request.portfolio_ref == _PORTFOLIO_REF

    registry = ServiceRegistry(
        tmp_path / "monitor-registry.sqlite",
        service_id=_SERVICE_ID,
        capabilities=(
            ServiceCapability(
                _INSTANCE_ID, "service.monitor_query", _MONITOR_CAPABILITIES
            ),
        ),
        grants={
            _CLIENT.principal_ref: (
                PrincipalGrant(
                    _CLIENT,
                    (
                        ResourceGrant(
                            _INSTANCE_ID,
                            "service.monitor_query",
                            _SCOPE_REF,
                            _TRUSTED_SCOPE,
                            frozenset({"monitor.read_after"}),
                        ),
                    ),
                ),
            )
        },
        policy_version="monitor-access-v1",
        requirements_version="monitor-readiness-v1",
    )
    observed = []

    def read_after(context, arguments):
        observed.append((context.identity, context.scope, dict(arguments)))
        return _READ_PAGE

    registry.register_handler(_INSTANCE_ID, "monitor.read_after", read_after)
    transports = []

    def transport_factory(_profile, peer):
        assert peer == _PEER
        transport = _RegistryTransport(registry)
        transports.append(transport)
        return transport

    runtime = ServiceClientRuntime(
        client,
        identity=_CLIENT,
        scope_resolver=lambda _scope_ref: _TRUSTED_SCOPE,
        peer_resolver=lambda _service_id: _PEER,
        transport_factory=transport_factory,
        client_session_id="session-a",
        run_id="run-a",
    )
    try:
        summary = runtime.start()
        assert summary.degraded_count == 0
        assert runtime.identity.role is WireRole.STRATEGY_CLIENT
        assert len(runtime.bindings) == 1
        binding = runtime.bindings[0]
        assert binding.mount == "broker.group_monitor"
        assert binding.operations == frozenset({"monitor.read_after"})
        assert binding.scope.strategy_ref == "strategy-a"
        assert binding.scope.portfolio_ref == _PORTFOLIO_REF
        assert binding.scope.run_id == "run-a"

        result = runtime.call(
            "broker.group_monitor",
            "monitor.read_after",
            {"after_sequence": 0, "limit": 20},
            portfolio_ref=_PORTFOLIO_REF,
            scope_ref=_SCOPE_REF,
        )
        # A runtime reply is the frozen JSON value carried by WireEnvelope:
        # mappings stay read-only and JSON arrays become tuples.
        assert result is not _READ_PAGE
        assert isinstance(result, Mapping)
        assert set(result) == {"requested_cursor", "next_cursor", "has_more", "events"}
        assert type(result["requested_cursor"]) is int
        assert result["requested_cursor"] == 0
        assert type(result["next_cursor"]) is int
        assert result["next_cursor"] == 1
        assert type(result["has_more"]) is bool
        assert result["has_more"] is False
        events = result["events"]
        assert type(events) is tuple
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, Mapping)
        assert set(event) == {"event_id", "sequence", "event_type", "data"}
        assert event["event_id"] == "event-a"
        assert type(event["sequence"]) is int
        assert event["sequence"] == 1
        assert event["event_type"] == "execution.group.residual"
        event_data = event["data"]
        assert isinstance(event_data, Mapping)
        assert dict(event_data) == {"portfolio_ref": _PORTFOLIO_REF}
        assert len(observed) == 1
        identity, scope, arguments = observed[0]
        assert identity == _CLIENT
        assert identity.role is WireRole.STRATEGY_CLIENT
        assert scope.strategy_ref == "strategy-a"
        assert scope.portfolio_ref == _PORTFOLIO_REF
        assert scope.run_id == "run-a"
        assert arguments == {"after_sequence": 0, "limit": 20}

        # The real Registry sees the capability's broader advertised surface,
        # but its trusted ResourceGrant does not authorize monitor.query.
        read_request = next(
            request
            for request in transports[0].requests
            if request.header.operation == "monitor.read_after"
        )
        for operation in ("monitor.query", "telemetry.snapshot"):
            denied = registry.handle(
                _CLIENT.principal_ref,
                replace(
                    read_request,
                    header=replace(read_request.header, operation=operation),
                ),
            )
            assert denied == {"ok": False, "code": "SCOPE_DENIED", "result": {}}
        assert len(observed) == 1
    finally:
        try:
            runtime.close()
        finally:
            registry.close()

    assert len(transports) == 1
    assert transports[0].closed is True


def test_strategy_group_monitor_requires_portfolio_and_keeps_role_and_alias_fences():
    with pytest.raises(ServiceConfigError) as missing_portfolio:
        _client_config(include_portfolio=False)
    assert missing_portfolio.value.code == "MISSING_FIELD"
    assert missing_portfolio.value.path.endswith(".portfolio_ref")

    with pytest.raises(ServiceConfigError) as strategy_application_mount:
        _client_config(mount="application.monitor")
    assert strategy_application_mount.value.code == "UNKNOWN_MOUNT"

    with pytest.raises(ServiceConfigError) as application_strategy_mount:
        _client_config(process_role="application_client")
    assert application_strategy_mount.value.code == "UNKNOWN_MOUNT"

    application_monitor = _client_config(
        mount="application.monitor",
        process_role="application_client",
        include_portfolio=False,
    )
    assert application_monitor.to_public_dict()["process_role"] == "application_client"

    with pytest.raises(ServiceConfigError) as wrong_alias:
        _client_config(alias="execution")
    assert wrong_alias.value.code == "CLIENT_ALIAS_MOUNT_MISMATCH"


@pytest.mark.parametrize("operation", ("monitor.query", "telemetry.snapshot"))
def test_strategy_group_monitor_rejects_non_read_after_requests(operation):
    client = _client_config()
    with pytest.raises(ServiceRuntimeError) as error:
        ServiceClientRuntime(
            client,
            identity=_CLIENT,
            scope_resolver=lambda _scope_ref: _TRUSTED_SCOPE,
            peer_resolver=lambda _service_id: _PEER,
            transport_factory=lambda _profile, _peer: pytest.fail(
                "rejected operation must fail before transport creation"
            ),
            requested_operations={"broker.group_monitor": frozenset({operation})},
        )
    assert error.value.code == "INVALID_ACTION_REQUEST"
