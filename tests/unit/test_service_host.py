"""Focused lifecycle tests for the fixed SDK service host."""

from __future__ import annotations

import importlib
import socket
from dataclasses import replace
from pathlib import Path
from typing import Any

import bt_api_monitor.service_component as monitor_service_component
import pytest
import zmq
from bt_api_base.service_protocol import (
    WIRE_PROTOCOL_VERSION,
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_monitor.durable import DurableOutbox, OutboxEvent
from bt_api_monitor.service_component import MONITOR_READ_OPERATIONS, MonitorReadTarget

import bt_api_py.service_registry as service_registry_module
from bt_api_py import service_components
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import (
    ServiceHost,
    ServiceHostError,
    TrustedServiceAuthority,
    TrustedServiceClients,
)
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCallContext,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import RegistrationSummary

_CLIENT_IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, "curve-principal", "strategy-client-a")
_REQUEST_SCOPE = WireScope(
    "paper:demo",
    account_ref="account-a",
    strategy_ref="strategy-a",
    run_id="run-a",
)
_SCOPE_TEMPLATE = WireScope(
    "paper:demo",
    account_ref="account-a",
    strategy_ref="strategy-a",
)
_SENT_AT = "2026-10-14T00:00:00Z"


def _request(operation: str, payload: dict[str, Any], *, schema_id: str) -> WireEnvelope:
    _request.next_id += 1
    return WireEnvelope(
        header=WireHeader(
            protocol_version=WIRE_PROTOCOL_VERSION,
            kind=WireMessageKind.REQUEST,
            message_id=f"host-test-{_request.next_id}",
            correlation_id=None,
            identity=_CLIENT_IDENTITY,
            scope=_REQUEST_SCOPE,
            operation=operation,
            schema_id=schema_id,
            schema_version=1,
            sent_at_utc=_SENT_AT,
        ),
        payload=payload,
    )


_request.next_id = 0


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    return f"tcp://127.0.0.1:{port}"


def _service_config(
    components: dict[str, dict[str, Any]],
    *,
    service_id: str = "monitor-service",
    scope_ref: str = "root-scope-v1",
    security_ref: str = "listener-security-v1",
    clients: dict[str, Any] | None = None,
    client_endpoint: str = "tcp://127.0.0.1:7999",
    listener_endpoint: str | None = None,
) -> Any:
    raw: dict[str, Any] = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": service_id,
        "scope_ref": scope_ref,
        "transport_ref": "listener-v1",
        "registration_policy_ref": "registration-policy-v1",
        "service_requirements_ref": "service-requirements-v1",
        "components": components,
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": listener_endpoint or _endpoint()},
                    "security_profile_ref": security_ref,
                    "options": {"high_water_mark": 32, "max_message_bytes": 65_536},
                },
            },
        },
    }
    if clients is not None:
        raw["clients"] = clients
        raw["communication"]["profiles"]["upstream-connect"] = {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": client_endpoint},
            "security_profile_ref": "upstream-security-v1",
            "options": {
                "request_timeout_ms": 2000,
                "high_water_mark": 1000,
                "max_message_bytes": 1_048_576,
            },
        }
    return parse_service_config(raw)


def _monitor_component(*, instance_id: str = "monitor-read") -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": ["service.monitor_query"],
            "options": {},
        },
    }


def _gateway_component(*, instance_id: str = "gateway-session") -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": ["service.provider_session"],
            "options": {
                "role": "account_session_worker",
                "account_session_ref": "trusted-session-v1",
            },
        },
    }


def _account_read_component(*, instance_id: str = "account-reader") -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": ["service.account_read"],
            "options": {
                "role": "account_session_worker",
                "account_session_ref": "trusted-session-v1",
            },
        },
    }


def _outbound_client(service_id: str, endpoint: str, *, required: bool = True) -> dict[str, Any]:
    return {
        "gateway": {
            "config_schema_version": 1,
            "settings": {
                "bindings": [
                    {
                        "mount": "upstream.account",
                        "service_id": service_id,
                        "instance_id": "account-reader",
                        "scope_ref": "account-grant-v1",
                        "account_ref": "account-a",
                        "transport_ref": "upstream-connect",
                        "required": required,
                    }
                ]
            },
        }
    }


class _RegistryTransport:
    def __init__(self, registry, principal_ref: str, peer: WireIdentity, events: list[str]):
        self.registry = registry
        self.principal_ref = principal_ref
        self.peer = peer
        self.events = events
        self.closed = False
        self.fail_unregister = False

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        assert timeout > 0
        if self.fail_unregister and request.header.operation == "service.unregister":
            result = {"ok": False, "code": "SERVICE_UNAVAILABLE", "result": {}}
        else:
            result = self.registry.handle(self.principal_ref, request)
        self.events.append(request.header.operation)
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id=f"reply-{request.header.message_id}",
                correlation_id=request.header.message_id,
                identity=self.peer,
            ),
            result,
        )

    def close(self) -> None:
        self.events.append("transport.close")
        self.closed = True


def _outbound_setup(
    tmp_path,
    *,
    ready: bool = True,
    component_close=None,
    client_required: bool = True,
    remote_ready: bool = True,
):
    remote_endpoint = _endpoint()
    downstream = _service_config(
        {"monitor": _monitor_component()},
        clients=_outbound_client("account-service", remote_endpoint, required=client_required),
        client_endpoint=remote_endpoint,
    )
    upstream_config = _service_config(
        {"gateway": _account_read_component()},
        service_id="account-service",
        listener_endpoint=remote_endpoint,
    )
    authority = _authority(downstream)
    peer = WireIdentity(WireRole.SERVICE, "upstream-principal", "account-service")
    account_capability = ServiceCapability(
        "account-reader", "service.account_read", frozenset({"account.snapshot"})
    )
    remote_registry = ServiceRegistry(
        tmp_path / "upstream-registry.sqlite3",
        service_id="account-service",
        capabilities=(account_capability,),
        grants={
            authority.identity.principal_ref: (
                PrincipalGrant(
                    authority.identity,
                    (
                        ResourceGrant(
                            "account-reader",
                            "service.account_read",
                            "account-grant-v1",
                            _SCOPE_TEMPLATE,
                            account_capability.operations,
                        ),
                    ),
                ),
            )
        },
        policy_version="upstream-policy-v1",
        requirements_version="upstream-requirements-v1",
        ready=remote_ready,
    )
    account_reads: list[tuple[WireScope, dict[str, Any]]] = []

    def account_snapshot(context, arguments):
        account_reads.append((context.scope, dict(arguments)))
        return {"account_ref": context.scope.account_ref, "balance": 125.0}

    remote_registry.register_handler("account-reader", "account.snapshot", account_snapshot)
    events: list[str] = []

    def transport_factory(_profile, resolved_peer):
        assert resolved_peer == peer
        events.append("transport.open")
        return _RegistryTransport(
            remote_registry, authority.identity.principal_ref, resolved_peer, events
        )

    bundle = TrustedServiceClients(
        scope_resolver=lambda scope_ref: (
            _SCOPE_TEMPLATE
            if scope_ref == "account-grant-v1"
            else pytest.fail("unexpected scope reference")
        ),
        peer_resolver=lambda service_id: (
            peer if service_id == "account-service" else pytest.fail("unexpected service reference")
        ),
        transport_factory=transport_factory,
    )
    base_resources, outbox = _monitor_resources(tmp_path / "downstream-outbox.sqlite3")
    if not ready or component_close is not None:
        resources = ComponentResources.owned(
            base_resources.values,
            ready=lambda: ready,
            close=component_close or base_resources.close,
        )
    else:
        resources = base_resources
    calls: list[str] = []
    host_ref: list[ServiceHost] = []

    def resource_provider(component, instance_id, options, scope):
        calls.append(component)
        events.append("provider.call")
        assert host_ref[0].upstream is not None
        assert host_ref[0].upstream.healthy is True
        assert scope is authority.scope
        assert dict(options) == {}
        return resources

    host = ServiceHost(
        downstream,
        authority,
        tmp_path / "downstream-registry.sqlite3",
        resource_provider,
        service_directory={
            downstream.service_id: downstream,
            upstream_config.service_id: upstream_config,
        },
        trusted_clients=bundle,
    )
    host_ref.append(host)
    return (
        host,
        downstream,
        upstream_config,
        authority,
        peer,
        remote_registry,
        resources,
        outbox,
        calls,
        events,
        account_reads,
    )


def _authority(config: Any) -> TrustedServiceAuthority:
    client_public, _client_secret = zmq.curve_keypair()
    server_public, server_secret = zmq.curve_keypair()
    scope = _SCOPE_TEMPLATE
    monitor_settings = config.components.get("monitor", {}).get("settings", {})
    monitor_instance = monitor_settings.get("instance_id", "monitor-read")
    monitor_ports = monitor_settings.get("bind_ports", ())
    grants = ()
    if "service.monitor_query" in monitor_ports:
        grants = (
            ResourceGrant(
                instance_id=monitor_instance,
                port="service.monitor_query",
                scope_ref="monitor-grant-v1",
                scope=scope,
                operations=MONITOR_READ_OPERATIONS,
            ),
        )
    return TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, "monitor-service", config.service_id),
        scope=scope,
        scope_ref=config.scope_ref,
        scope_version="scope-v1",
        security_ref="listener-security-v1",
        security_version="security-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: "curve-principal"},
        grants={"curve-principal": (PrincipalGrant(_CLIENT_IDENTITY, grants),)},
    )


def _monitor_resources(
    path: Path, *, ready: bool = True, ready_check=None
) -> tuple[ComponentResources, DurableOutbox]:
    writer = DurableOutbox(path)
    writer.append(
        OutboxEvent(
            event_id="event-1",
            scope="monitor:strategy-a",
            event_type="risk.alert",
            data={"severity": "low"},
            occurred_at=1_800_000_000.0,
        )
    )
    outbox = DurableOutbox(path, read_only=True)
    target = MonitorReadTarget("monitor:strategy-a", ("risk.alert",))
    return (
        ComponentResources.borrowed(
            {"outbox": outbox, "targets": {_SCOPE_TEMPLATE: target}},
            ready=ready_check or (lambda: ready),
        ),
        outbox,
    )


def test_selected_monitor_adapter_starts_with_exact_capabilities_and_closes(tmp_path, monkeypatch):
    config = _service_config({"monitor": _monitor_component()})
    authority = _authority(config)
    resources, _outbox = _monitor_resources(tmp_path / "monitor.db")
    provider_calls: list[tuple[str, str]] = []

    def provider(component, instance_id, options, scope):
        provider_calls.append((component, instance_id))
        assert scope is authority.scope
        assert dict(options) == {}
        return resources

    imported: list[str] = []
    original_import = importlib.import_module

    def tracked_import(name: str, package: str | None = None):
        if name.startswith("bt_api_"):
            imported.append(name)
        return original_import(name, package)

    monkeypatch.setattr(service_components.importlib, "import_module", tracked_import)
    host = ServiceHost(config, authority, tmp_path / "registry.sqlite3", provider)
    assert host.summary["components"][0]["instance_id"] == "monitor-read"
    host.start()

    assert host.state == "started"
    assert host.endpoint == config.communication["profiles"]["listener-v1"]["endpoints"]["rpc_bind"]
    assert provider_calls == [("monitor", "monitor-read")]
    assert imported == ["bt_api_monitor.service_component"]
    component = host._components[0]
    assert [(item.port, item.operations) for item in component.capabilities] == [
        ("service.monitor_query", MONITOR_READ_OPERATIONS)
    ]
    assert set(component.handlers) == set(MONITOR_READ_OPERATIONS)
    assert set(host.summary["components"][0]["capabilities"][0]["operations"]) == set(
        MONITOR_READ_OPERATIONS
    )

    result = component.handlers["monitor.query"](
        ServiceCallContext(
            identity=_CLIENT_IDENTITY,
            scope=_REQUEST_SCOPE,
            registration_id="registration-a",
            principal_ref="curve-principal",
        ),
        {},
    )
    assert result["events"][0]["event_id"] == "event-1"
    assert host._registry is not None and host._registry.ready is True
    host.close()
    host.close()
    assert host.state == "closed"
    assert host._registry is None


def test_unsupported_public_port_fails_before_any_resource_resolution(tmp_path):
    config = _service_config(
        {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "monitor-ingress",
                    "bind_ports": ["service.telemetry_ingress"],
                    "options": {},
                },
            }
        }
    )
    calls: list[str] = []
    host = ServiceHost(
        config,
        _authority(config),
        tmp_path / "registry.sqlite3",
        lambda component, instance_id, options, scope: calls.append(component),
    )

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "UNSUPPORTED_PUBLIC_PORT"
    assert calls == []
    assert not (tmp_path / "registry.sqlite3").exists()
    assert host.state == "failed"


def test_declared_order_entry_is_not_advertised_without_a_real_write_adapter(tmp_path):
    config = _service_config(
        {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "execution-owner",
                    "bind_ports": ["service.order_entry"],
                    "options": {"role": "account_owner"},
                },
            }
        },
        service_id="execution-service",
    )
    calls: list[str] = []
    host = ServiceHost(
        config,
        _authority(config),
        tmp_path / "registry.sqlite3",
        lambda component, *_args: calls.append(component),
    )

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "UNSUPPORTED_PUBLIC_PORT"
    assert calls == []
    assert not (tmp_path / "registry.sqlite3").exists()


def test_risk_factory_advertises_only_the_adapter_class_read_operation():
    config = _service_config(
        {
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "risk-reader",
                    "bind_ports": ["service.risk_query"],
                    "options": {
                        "role": "account_admission",
                        "account_policy_ref": "account-policy-v1",
                    },
                },
            }
        },
        service_id="risk-service",
    )

    prepared = service_components.validate_components(
        service_components.component_specs(config.components)
    )

    assert len(prepared) == 1
    assert dict(prepared[0].operations_by_port) == {
        "service.risk_query": frozenset({"risk.snapshot"})
    }


def test_authority_reference_mismatch_fails_before_selected_package_import(tmp_path, monkeypatch):
    config = _service_config({"monitor": _monitor_component()})
    authority = _authority(config)
    wrong_authority = TrustedServiceAuthority(
        identity=authority.identity,
        scope=authority.scope,
        scope_ref="other-root-scope-v1",
        scope_version=authority.scope_version,
        security_ref=authority.security_ref,
        security_version=authority.security_version,
        registration_policy_ref=authority.registration_policy_ref,
        registration_policy_version=authority.registration_policy_version,
        service_requirements_ref=authority.service_requirements_ref,
        service_requirements_version=authority.service_requirements_version,
        curve_public_key=authority.curve_public_key,
        curve_secret_key=authority.curve_secret_key,
        principals=authority.principals,
        grants=authority.grants,
    )
    imported: list[str] = []
    original_import = importlib.import_module

    def tracked_import(name: str, package: str | None = None):
        imported.append(name)
        return original_import(name, package)

    monkeypatch.setattr(service_components.importlib, "import_module", tracked_import)
    host = ServiceHost(
        config,
        wrong_authority,
        tmp_path / "registry.sqlite3",
        lambda *args: pytest.fail("resources must not be resolved before authority checks"),
    )

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "SCOPE_REFERENCE_MISMATCH"
    assert imported == []
    assert not (tmp_path / "registry.sqlite3").exists()


def test_invalid_grant_capability_pair_fails_before_resource_resolution(tmp_path):
    config = _service_config({"monitor": _monitor_component()})
    authority = _authority(config)
    wrong_resource = ResourceGrant(
        instance_id="unselected-monitor-instance",
        port="service.monitor_query",
        scope_ref="monitor-grant-v1",
        scope=_SCOPE_TEMPLATE,
        operations=MONITOR_READ_OPERATIONS,
    )
    authority = TrustedServiceAuthority(
        identity=authority.identity,
        scope=authority.scope,
        scope_ref=authority.scope_ref,
        scope_version=authority.scope_version,
        security_ref=authority.security_ref,
        security_version=authority.security_version,
        registration_policy_ref=authority.registration_policy_ref,
        registration_policy_version=authority.registration_policy_version,
        service_requirements_ref=authority.service_requirements_ref,
        service_requirements_version=authority.service_requirements_version,
        curve_public_key=authority.curve_public_key,
        curve_secret_key=authority.curve_secret_key,
        principals=authority.principals,
        grants={"curve-principal": (PrincipalGrant(_CLIENT_IDENTITY, (wrong_resource,)),)},
    )
    calls: list[str] = []
    host = ServiceHost(
        config,
        authority,
        tmp_path / "registry.sqlite3",
        lambda component, *_args: calls.append(component),
    )

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "AUTHORITY_GRANTS_INVALID"
    assert calls == []
    assert not (tmp_path / "registry.sqlite3").exists()


def test_invalid_curve_z85_key_fails_before_resource_resolution(tmp_path):
    config = _service_config({"monitor": _monitor_component()})
    authority = replace(_authority(config), curve_public_key=b"_" * 40)
    calls: list[str] = []
    host = ServiceHost(
        config,
        authority,
        tmp_path / "registry.sqlite3",
        lambda component, *_args: calls.append(component),
    )

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "TRANSPORT_CONFIGURATION_INVALID"
    assert calls == []
    assert not (tmp_path / "registry.sqlite3").exists()
    assert host.state == "failed"


def test_registry_startup_cleanup_failure_is_retained_and_retried_by_host(tmp_path, monkeypatch):
    config = _service_config({"monitor": _monitor_component()})
    authority = _authority(config)
    resources, _outbox = _monitor_resources(tmp_path / "monitor.db")
    real_connect = service_registry_module.sqlite3.connect
    connections = []

    class FailTwiceClose:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.close_attempts = 0

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

        def __setattr__(self, name, value):
            if name in {"wrapped", "close_attempts"}:
                object.__setattr__(self, name, value)
            else:
                setattr(self.wrapped, name, value)

        def close(self):
            self.close_attempts += 1
            if self.close_attempts <= 2:
                raise OSError("private database close detail")
            self.wrapped.close()

    def connect(*args, **kwargs):
        connection = FailTwiceClose(real_connect(*args, **kwargs))
        connections.append(connection)
        return connection

    def fail_schema(_registry):
        raise RuntimeError("private schema startup detail")

    monkeypatch.setattr(service_registry_module.sqlite3, "connect", connect)
    monkeypatch.setattr(service_registry_module.ServiceRegistry, "_create_schema", fail_schema)
    host = ServiceHost(
        config,
        authority,
        tmp_path / "registry.sqlite3",
        lambda *_args: resources,
    )

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "START_CLEANUP_FAILED"
    assert host.state == "close_failed"
    assert host.summary["start_failure"] == "REGISTRY_STARTUP_CLEANUP_FAILED"
    assert host.summary["cleanup_failures"] == ("registry_close",)
    assert host._registry is not None
    assert host._registry._closed is False
    assert host._registry._db is connections[0]
    assert host._registry._owner_file is not None
    assert host._registry._owner_key in service_registry_module.ServiceRegistry._owned_paths
    assert connections[0].close_attempts == 2

    host.close()

    assert host.state == "closed"
    assert host._registry is None
    assert connections[0].close_attempts == 3


def test_transport_startup_cleanup_failure_is_retained_and_retried_by_host(tmp_path, monkeypatch):
    service_v2 = importlib.import_module("bt_api_transport_zmq.service_v2")
    config = _service_config({"monitor": _monitor_component()})
    authority = _authority(config)
    resources, _outbox = _monitor_resources(tmp_path / "monitor.db")
    close_attempts: list[int] = []

    class PartialServer:
        endpoint = "tcp://127.0.0.1:12345"

        def close(self):
            close_attempts.append(1)
            if len(close_attempts) == 1:
                raise OSError("private transport close detail")

    partial_server = PartialServer()

    def fail_server_start(*_args, **_kwargs):
        raise service_v2.ServiceTransportStartupCleanupError(partial_server)

    monkeypatch.setattr(service_v2, "ZmqServiceServer", fail_server_start)
    host = ServiceHost(
        config,
        authority,
        tmp_path / "registry.sqlite3",
        lambda *_args: resources,
    )

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "START_CLEANUP_FAILED"
    assert host.state == "close_failed"
    assert host.summary["start_failure"] == "TRANSPORT_STARTUP_CLEANUP_FAILED"
    assert host.summary["cleanup_failures"] == ("listener_close",)
    assert host._server is partial_server
    assert host._registry is not None and host._registry.ready is False
    assert close_attempts == [1]

    host.close()

    assert host.state == "closed"
    assert host._server is None
    assert close_attempts == [1, 1]


def test_unready_component_rolls_back_listener_and_closes_components_in_reverse_order(tmp_path):
    config = _service_config(
        {
            "gateway": _gateway_component(instance_id="a-gateway"),
            "monitor": _monitor_component(instance_id="b-monitor"),
        }
    )
    authority = _authority(config)
    monitor_resources, _outbox = _monitor_resources(tmp_path / "monitor.db", ready=False)
    closed: list[str] = []

    def provider(component, instance_id, options, scope):
        if component == "gateway":
            return ComponentResources.owned(
                {},
                internal_ports={"service.provider_session": object()},
                ready=lambda: True,
                close=lambda: closed.append("gateway"),
            )
        return ComponentResources.owned(
            monitor_resources.values,
            ready=monitor_resources.ready,
            close=lambda: (closed.append("monitor"), monitor_resources.close()),
        )

    host = ServiceHost(config, authority, tmp_path / "registry.sqlite3", provider)

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "COMPONENT_NOT_READY"
    assert closed == ["monitor", "gateway"]
    assert host.endpoint is not None
    assert host.state == "failed"
    assert host._registry is None
    host.close()
    assert host.state == "closed"


def test_service_outbound_bindings_fail_explicitly_before_resource_resolution(tmp_path):
    config = _service_config(
        {"monitor": _monitor_component()},
        clients={
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "upstream.account",
                            "service_id": "upstream-service",
                            "instance_id": "upstream-reader",
                            "scope_ref": "upstream-grant",
                            "account_ref": "account-a",
                            "transport_ref": "upstream-connect",
                        }
                    ]
                },
            }
        },
    )
    provider_calls: list[str] = []
    host = ServiceHost(
        config,
        _authority(config),
        tmp_path / "registry.sqlite3",
        lambda component, instance_id, options, scope: provider_calls.append(component),
    )

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "OUTBOUND_DIRECTORY_REQUIRED"
    assert provider_calls == []
    assert not (tmp_path / "registry.sqlite3").exists()


def test_readiness_is_rechecked_before_serve_and_preserves_control_operations(tmp_path):
    readiness = {"ready": True}
    config = _service_config({"monitor": _monitor_component()})
    authority = _authority(config)
    resources, _outbox = _monitor_resources(
        tmp_path / "monitor.db", ready_check=lambda: readiness["ready"]
    )
    host = ServiceHost(
        config,
        authority,
        tmp_path / "registry.sqlite3",
        lambda *_args: resources,
    ).start()
    registry = host._registry
    assert registry is not None and registry.ready is True

    registration = registry.handle(
        "curve-principal",
        _request(
            "service.register",
            {
                "registration_key": "registration-a",
                "client_session_id": "client-session-a",
                "instance_id": "monitor-read",
                "port": "service.monitor_query",
                "scope_ref": "monitor-grant-v1",
                "operations": tuple(MONITOR_READ_OPERATIONS),
            },
            schema_id="sdk.service.control",
        ),
    )
    assert registration["code"] == "OK"
    registered = registration["result"]

    readiness["ready"] = False
    assert host.serve_once(0) is False
    assert registry.ready is False
    discovered = registry.handle(
        "curve-principal",
        _request("service.discover", {}, schema_id="sdk.service.control"),
    )
    assert discovered["code"] == "OK"
    assert discovered["result"]["ready"] is False
    blocked = registry.handle(
        "curve-principal",
        _request(
            "monitor.query",
            {
                "registration_id": registered["registration_id"],
                "service_epoch": registered["service_epoch"],
                "instance_id": "monitor-read",
                "arguments": {},
            },
            schema_id="sdk.service.call",
        ),
    )
    assert blocked["code"] == "POLICY_REQUIREMENT_UNMET"
    unregistered = registry.handle(
        "curve-principal",
        _request(
            "service.unregister",
            {
                "registration_id": registered["registration_id"],
                "service_epoch": registered["service_epoch"],
            },
            schema_id="sdk.service.control",
        ),
    )
    assert unregistered["code"] == "OK"
    host.close()


def test_readiness_change_during_wait_is_rechecked_at_dispatch(tmp_path, monkeypatch):
    service_v2 = importlib.import_module("bt_api_transport_zmq.service_v2")
    readiness = {"ready": True}
    config = _service_config({"monitor": _monitor_component()})
    authority = _authority(config)
    resources, _outbox = _monitor_resources(
        tmp_path / "monitor.db", ready_check=lambda: readiness["ready"]
    )

    class PollingServer:
        def __init__(self, endpoint, _identity, _principals, handler, **_kwargs):
            self.endpoint = endpoint
            self._handler = handler
            self.closed = False
            self.handler_result = None

        def serve_once(self, _timeout_ms):
            # Model readiness changing after host.serve_once's pre-poll check,
            # but before the received request reaches the handler.
            readiness["ready"] = False
            self.handler_result = self._handler(
                "curve-principal",
                _request(
                    "monitor.query",
                    {
                        "registration_id": registered["registration_id"],
                        "service_epoch": registered["service_epoch"],
                        "instance_id": "monitor-read",
                        "arguments": {},
                    },
                    schema_id="sdk.service.call",
                ),
            )
            return True

        def close(self):
            self.closed = True

    monkeypatch.setattr(service_v2, "ZmqServiceServer", PollingServer)
    host = ServiceHost(
        config,
        authority,
        tmp_path / "registry.sqlite3",
        lambda *_args: resources,
    ).start()
    registry = host._registry
    assert registry is not None and registry.ready is True
    response = registry.handle(
        "curve-principal",
        _request(
            "service.register",
            {
                "registration_key": "race-registration",
                "client_session_id": "race-client-session",
                "instance_id": "monitor-read",
                "port": "service.monitor_query",
                "scope_ref": "monitor-grant-v1",
                "operations": tuple(MONITOR_READ_OPERATIONS),
            },
            schema_id="sdk.service.control",
        ),
    )
    assert response["code"] == "OK"
    registered = response["result"]

    assert host.serve_once(1000) is True

    assert host._server is not None
    assert host._server.handler_result["code"] == "POLICY_REQUIREMENT_UNMET"
    assert registry.ready is False
    host.close()


def test_failed_owned_close_is_retained_reported_and_retryable(tmp_path):
    config = _service_config({"monitor": _monitor_component()})
    authority = _authority(config)
    resources, _outbox = _monitor_resources(tmp_path / "monitor.db", ready=False)
    close_attempts: list[int] = []

    def close_owned() -> None:
        close_attempts.append(1)
        if len(close_attempts) == 1:
            raise OSError("private close detail must not escape")
        resources.close()

    owned = ComponentResources.owned(
        resources.values,
        ready=resources.ready,
        close=close_owned,
    )
    host = ServiceHost(
        config,
        authority,
        tmp_path / "registry.sqlite3",
        lambda *_args: owned,
    )

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "START_CLEANUP_FAILED"
    assert host.state == "close_failed"
    assert host.summary["start_failure"] == "COMPONENT_NOT_READY"
    assert host.summary["cleanup_failures"] == ("component_close:monitor:monitor-read",)
    assert len(host._components) == 1
    assert close_attempts == [1]

    host.close()
    assert host.state == "closed"
    assert host._components == []
    assert close_attempts == [1, 1]
    assert host.summary["cleanup_history"] == ("component_close:monitor:monitor-read",)


def test_adapter_build_failure_retains_unattached_resource_for_cleanup_retry(tmp_path, monkeypatch):
    config = _service_config({"monitor": _monitor_component()})
    authority = _authority(config)
    resources, _outbox = _monitor_resources(tmp_path / "monitor.db")
    close_attempts: list[int] = []

    def close_owned() -> None:
        close_attempts.append(1)
        if len(close_attempts) == 1:
            raise OSError("private close detail must not escape")

    owned = ComponentResources.owned(
        resources.values,
        ready=resources.ready,
        close=close_owned,
    )

    def fail_adapter_construction(*_args, **_kwargs):
        raise RuntimeError("private adapter detail must not escape")

    monkeypatch.setattr(monitor_service_component, "MonitorReadService", fail_adapter_construction)
    host = ServiceHost(
        config,
        authority,
        tmp_path / "registry.sqlite3",
        lambda *_args: owned,
    )

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "START_CLEANUP_FAILED"
    assert host.state == "close_failed"
    assert host.summary["start_failure"] == "START_FAILED"
    assert host.summary["cleanup_failures"] == ("resource_close:monitor:monitor-read",)
    assert len(host._pending_resources) == 1
    assert host._components == []
    assert close_attempts == [1]

    host.close()
    assert host.state == "closed"
    assert host._pending_resources == []
    assert close_attempts == [1, 1]


def test_outbound_runtime_starts_before_resources_and_uses_trusted_transport(tmp_path):
    (
        host,
        _downstream,
        _upstream_config,
        _authority_value,
        _peer,
        remote_registry,
        _resources,
        outbox,
        provider_calls,
        events,
        account_reads,
    ) = _outbound_setup(tmp_path)
    try:
        host.start()

        assert host.upstream is not None
        assert host.upstream.healthy is True
        assert host.healthy is True
        assert provider_calls == ["monitor"]
        assert events.index("transport.open") < events.index("provider.call")
        assert {"service.discover", "service.register"} <= set(events)

        result = host.upstream.call(
            "upstream.account",
            "account.snapshot",
            {},
            account_ref="account-a",
            scope_ref="account-grant-v1",
        )
        assert result == {"account_ref": "account-a", "balance": 125.0}
        assert account_reads == [(_SCOPE_TEMPLATE, {})]
        transport = next(iter(host.upstream._transports.values()))
        transport.fail_unregister = True
        host.close()
        assert host.state == "closed"
        assert host.upstream is None
        assert host.summary["last_upstream_cleanup"] == {
            "local_state": "closed",
            "cleanup_failures": 1,
            "unregister_uncertainties": 1,
            "unknown_registration_count": 0,
        }
    finally:
        host.close()
        remote_registry.close()


def test_invalid_outbound_directory_fails_before_client_or_component_io(tmp_path):
    endpoint = _endpoint()
    config = _service_config(
        {"monitor": _monitor_component()},
        clients=_outbound_client("missing-account-service", endpoint),
        client_endpoint=endpoint,
    )
    provider_calls: list[str] = []
    transport_calls: list[str] = []
    authority = _authority(config)
    bundle = TrustedServiceClients(
        scope_resolver=lambda _reference: pytest.fail("scope resolution is runtime I/O"),
        peer_resolver=lambda _reference: pytest.fail("peer resolution is runtime I/O"),
        transport_factory=lambda *_args: transport_calls.append("open"),
    )
    host = ServiceHost(
        config,
        authority,
        tmp_path / "registry.sqlite3",
        lambda component, *_args: provider_calls.append(component),
        service_directory={config.service_id: config},
        trusted_clients=bundle,
    )

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "OUTBOUND_DEPLOYMENT_INVALID"
    assert provider_calls == []
    assert transport_calls == []
    assert not (tmp_path / "registry.sqlite3").exists()
    assert host.upstream is None


def test_configured_clients_require_bundle_and_unused_bundle_is_rejected(tmp_path):
    endpoint = _endpoint()
    config = _service_config(
        {"monitor": _monitor_component()},
        clients=_outbound_client("account-service", endpoint),
        client_endpoint=endpoint,
    )
    upstream_config = _service_config(
        {"gateway": _account_read_component()},
        service_id="account-service",
        listener_endpoint=endpoint,
    )
    directory = {config.service_id: config, upstream_config.service_id: upstream_config}
    provider_calls: list[str] = []

    missing_bundle = ServiceHost(
        config,
        _authority(config),
        tmp_path / "missing-bundle.sqlite3",
        lambda component, *_args: provider_calls.append(component),
        service_directory=directory,
    )
    with pytest.raises(ServiceHostError) as exc_info:
        missing_bundle.start()
    assert exc_info.value.code == "TRUSTED_CLIENTS_REQUIRED"
    assert provider_calls == []

    no_clients = _service_config({"monitor": _monitor_component()})
    unused_bundle = TrustedServiceClients(
        scope_resolver=lambda _reference: _SCOPE_TEMPLATE,
        peer_resolver=lambda _service_id: WireIdentity(WireRole.SERVICE, "peer", "account-service"),
        transport_factory=lambda *_args: pytest.fail("unused bundle must not open transports"),
    )
    unused = ServiceHost(
        no_clients,
        _authority(no_clients),
        tmp_path / "unused-bundle.sqlite3",
        lambda component, *_args: provider_calls.append(component),
        trusted_clients=unused_bundle,
    )
    with pytest.raises(ServiceHostError) as exc_info:
        unused.start()
    assert exc_info.value.code == "UNUSED_TRUSTED_CLIENTS"
    assert provider_calls == []
    assert not (tmp_path / "missing-bundle.sqlite3").exists()
    assert not (tmp_path / "unused-bundle.sqlite3").exists()


def test_optional_upstream_degradation_does_not_withdraw_local_readiness(tmp_path):
    (
        host,
        _downstream,
        _upstream_config,
        _authority_value,
        _peer,
        remote_registry,
        _resources,
        _outbox,
        provider_calls,
        _events,
        _account_reads,
    ) = _outbound_setup(tmp_path, client_required=False, remote_ready=False)
    try:
        host.start()

        assert host.upstream is not None
        assert host.upstream.healthy is True
        assert host.summary["upstream"]["degraded_count"] == 1
        assert host.healthy is True
        assert provider_calls == ["monitor"]
        assert host._registry is not None and host._registry.ready is True
    finally:
        host.close()
        remote_registry.close()


def test_upstream_maintenance_clamps_poll_and_explicit_recovery_restores_readiness(
    tmp_path, monkeypatch
):
    (
        host,
        _downstream,
        _upstream_config,
        _authority_value,
        _peer,
        remote_registry,
        _resources,
        outbox,
        _provider_calls,
        events,
        account_reads,
    ) = _outbound_setup(tmp_path)
    service_v2 = importlib.import_module("bt_api_transport_zmq.service_v2")
    poll_timeouts: list[int] = []
    monkeypatch.setattr(
        service_v2.ZmqServiceServer,
        "serve_once",
        lambda _server, timeout: poll_timeouts.append(timeout) or False,
    )
    try:
        host.start()
        runtime = host.upstream
        assert runtime is not None
        monkeypatch.setattr(runtime, "next_maintenance_delay", lambda: 0.125)

        with pytest.raises(ServiceHostError) as exc_info:
            host.serve_once(True)
        assert exc_info.value.code == "INVALID_TIMEOUT"
        assert poll_timeouts == []

        assert host.serve_once(2_000) is False
        assert poll_timeouts == [125]
        assert host._registry is not None and host._registry.ready is True

        # Model a required lease loss without manufacturing a transport result.
        # Runtime.maintain observes the failed session and the host still polls
        # once so service discovery/unregister remain reachable.
        runtime._state = "failed"
        assert host.serve_once(2_000) is False
        assert poll_timeouts == [125, 125]
        assert host._registry.ready is False
        assert runtime.state == "failed"
        assert events.count("transport.open") == 1
        assert account_reads == []

        monkeypatch.delattr(runtime, "next_maintenance_delay")
        summary = host.recover_upstreams()
        assert isinstance(summary, RegistrationSummary)
        assert runtime.state == "registered"
        assert host.healthy is True
        assert host._registry.ready is True
        assert events.count("transport.open") == 2
        assert account_reads == []

        value = runtime.call(
            "upstream.account",
            "account.snapshot",
            {},
            account_ref="account-a",
            scope_ref="account-grant-v1",
        )
        assert value["balance"] == 125.0
        assert len(account_reads) == 1
    finally:
        host.close()
        remote_registry.close()


def test_component_close_failure_keeps_upstream_until_component_retry(tmp_path):
    close_attempts: list[int] = []

    def fail_first_close():
        close_attempts.append(1)
        if len(close_attempts) == 1:
            raise OSError("private component close detail")

    (
        host,
        _downstream,
        _upstream_config,
        _authority_value,
        _peer,
        remote_registry,
        _resources,
        outbox,
        _provider_calls,
        events,
        _account_reads,
    ) = _outbound_setup(tmp_path, ready=False, component_close=fail_first_close)
    try:
        with pytest.raises(ServiceHostError) as exc_info:
            host.start()
        assert exc_info.value.code == "START_CLEANUP_FAILED"
        assert host.state == "close_failed"
        assert host._components
        assert host.upstream is not None and host.upstream.state == "registered"
        assert events.count("transport.close") == 0

        host.close()
        assert host.state == "closed"
        assert host._components == []
        assert close_attempts == [1, 1]
        assert host.upstream is None
        assert events.count("transport.close") == 1
    finally:
        if host.state != "closed":
            host.close()
        remote_registry.close()
