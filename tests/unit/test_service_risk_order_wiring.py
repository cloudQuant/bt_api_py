"""Focused SDK wiring checks for the Risk-only order owner."""

from __future__ import annotations

import importlib
import socket
from dataclasses import dataclass, replace
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py.service_config import ServiceConfigError, parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError

_DOMAIN = "paper:risk-order-wiring"
_SERVICE_ID = "risk-order-wiring-service"
_SERVICE_INSTANCE = "risk-order-owner"
_SERVICE_PRINCIPAL = "risk-order-service-principal"
_SERVICE_SCOPE_REF = "risk-order-root-v1"
_REGISTRATION_POLICY_REF = "risk-order-registration-v1"
_REQUIREMENTS_REF = "risk-order-requirements-v1"
_SERVER_SECURITY_REF = "risk-order-server-curve-v1"
_CLIENT_SECURITY_REF = "risk-order-client-curve-v1"
_STRATEGY = "risk-order-strategy"
_PRINCIPAL = "risk-order-strategy-principal"
_ACCOUNT = "risk-order-account"
_ORDER_SCOPE_REF = "risk-order-account-scope-v1"
_QUERY_SCOPE_REF = "risk-order-query-scope-v1"
_CLIENT_IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY)
_SERVICE_IDENTITY = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
_REGISTRY_PEER = WireIdentity(WireRole.SERVICE, "account-service-principal", "account-service")
_WIRE_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _risk_service_config(
    endpoint: str,
    *,
    bind_ports: tuple[str, ...] = ("service.order_entry", "service.order_query"),
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": "risk-order-listener-v1",
        "registration_policy_ref": _REGISTRATION_POLICY_REF,
        "service_requirements_ref": _REQUIREMENTS_REF,
        "components": {
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _SERVICE_INSTANCE,
                    "bind_ports": list(bind_ports),
                    "options": options
                    if options is not None
                    else {
                        "role": "account_owner",
                        "account_policy_ref": "risk-account-policy-v1",
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "risk-order-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SERVER_SECURITY_REF,
                    "options": {"high_water_mark": 16, "max_message_bytes": 65_536},
                }
            },
        },
    }


def _client_config(endpoint: str) -> object:
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": _STRATEGY},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "broker.orders",
                                "service_id": "account-service",
                                "instance_id": "risk-owner",
                                "scope_ref": _ORDER_SCOPE_REF,
                                "account_ref": _ACCOUNT,
                                "transport_ref": "risk-order-connect-v1",
                            }
                        ],
                        "options": {"request_timeout_ms": 1_000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "risk-order-connect-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": _CLIENT_SECURITY_REF,
                        "options": {
                            "request_timeout_ms": 1_000,
                            "high_water_mark": 16,
                            "max_message_bytes": 65_536,
                        },
                    }
                },
            },
        }
    )


class _RegistryTransport:
    """Short local transport shim around the real durable ServiceRegistry."""

    def __init__(self, registry: ServiceRegistry) -> None:
        self.registry = registry
        self.requests = []
        self.closed = False

    def request(self, request, *, timeout):
        del timeout
        self.requests.append(request)
        result = self.registry.handle(_PRINCIPAL, request)
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=_REGISTRY_PEER,
            ),
            result,
        )

    def close(self) -> None:
        self.closed = True


def test_runtime_registers_submit_only_and_does_not_route_cancel(tmp_path, request) -> None:
    operations = frozenset({"orders.submit"})
    calls = []
    registry = ServiceRegistry(
        tmp_path / "risk-submit-only-registry.sqlite3",
        service_id="account-service",
        capabilities=(ServiceCapability("risk-owner", "service.order_entry", operations),),
        grants={
            _PRINCIPAL: (
                PrincipalGrant(
                    _CLIENT_IDENTITY,
                    (
                        ResourceGrant(
                            "risk-owner",
                            "service.order_entry",
                            _ORDER_SCOPE_REF,
                            _WIRE_SCOPE,
                            operations,
                        ),
                    ),
                ),
            )
        },
        policy_version="risk-order-access-v1",
        requirements_version="risk-order-ready-v1",
    )
    request.addfinalizer(registry.close)
    registry.register_handler(
        "risk-owner",
        "orders.submit",
        lambda context, arguments: (
            calls.append((context.principal_ref, context.scope.account_ref, dict(arguments)))
            or {"accepted": True, "intent_id": arguments["intent_id"]}
        ),
    )
    endpoint = _endpoint()
    transport = _RegistryTransport(registry)
    runtime = ServiceClientRuntime(
        _client_config(endpoint),
        identity=_CLIENT_IDENTITY,
        scope_resolver=lambda _scope_ref: _WIRE_SCOPE,
        peer_resolver=lambda _service_id: _REGISTRY_PEER,
        transport_factory=lambda _profile, _peer: transport,
        run_id="risk-order-run-v1",
    )
    try:
        summary = runtime.start()
        assert summary.degraded_count == 0
        assert len(runtime.bindings) == 1
        assert runtime.bindings[0].operations == operations
        assert runtime.call("broker.orders", "orders.submit", {"intent_id": "submit-1"}) == {
            "accepted": True,
            "intent_id": "submit-1",
        }
        assert calls == [(_PRINCIPAL, _ACCOUNT, {"intent_id": "submit-1"})]

        request_count = len(transport.requests)
        with pytest.raises(ServiceRuntimeError) as missing_route:
            runtime.call("broker.orders", "orders.cancel", {"intent_id": "submit-1"})
        assert missing_route.value.code == "AMBIGUOUS_OR_MISSING_ROUTE"
        assert len(transport.requests) == request_count
        assert len(calls) == 1
    finally:
        runtime.close()
    assert transport.closed is True

    explicit_transport = _RegistryTransport(registry)
    explicit_cancel = ServiceClientRuntime(
        _client_config(endpoint),
        identity=_CLIENT_IDENTITY,
        scope_resolver=lambda _scope_ref: _WIRE_SCOPE,
        peer_resolver=lambda _service_id: _REGISTRY_PEER,
        transport_factory=lambda _profile, _peer: explicit_transport,
        run_id="risk-order-run-cancel-v1",
        requested_operations={"broker.orders": frozenset({"orders.submit", "orders.cancel"})},
    )
    try:
        with pytest.raises(ServiceRuntimeError) as explicit_rejection:
            explicit_cancel.start()
        assert explicit_rejection.value.code == "CAPABILITY_UNAVAILABLE"
        assert [request.header.operation for request in explicit_transport.requests] == [
            "service.discover"
        ]
        assert calls == [(_PRINCIPAL, _ACCOUNT, {"intent_id": "submit-1"})]
    finally:
        explicit_cancel.close()
        explicit_transport.close()


@pytest.mark.parametrize(
    ("bind_ports", "options", "expected_code"),
    [
        (("service.owner_control",), None, "PORT_ROLE_MISMATCH"),
        (("service.order_entry",), {"role": "account_owner"}, "MISSING_FIELD"),
    ],
)
def test_risk_owner_config_rejects_internal_port_and_missing_policy(
    bind_ports, options, expected_code
) -> None:
    with pytest.raises(ServiceConfigError) as error:
        parse_service_config(
            _risk_service_config(_endpoint(), bind_ports=bind_ports, options=options)
        )
    assert error.value.code == expected_code


@dataclass(frozen=True)
class _FakeAccountScope:
    key: str


@dataclass(frozen=True)
class _FakeRiskOrderCommand:
    scope: _FakeAccountScope
    principal_ref: str
    state: str
    latest_event: dict[str, Any]
    permit_id: str | None = None
    risk_intent_id: str = "risk-intent-a"


class _StartupProbeAdapter:
    def __init__(self, events: list[str], *, submit_ready: bool) -> None:
        self.events = events
        self.submit_ready = submit_ready
        self.dispatch_calls = 0
        self.recovery_calls = 0
        self.account_scope = _FakeAccountScope("risk-account-scope-key")

    def capability_state(self, scope: WireScope) -> dict[str, bool]:
        self.events.append("capability")
        return {"orders.submit": self.submit_ready, "orders.query": True}

    def recover_interrupted_dispatches(self) -> tuple[_FakeRiskOrderCommand, ...]:
        self.events.append("recovery")
        self.recovery_calls += 1
        return (
            _FakeRiskOrderCommand(
                scope=self.account_scope,
                principal_ref=_PRINCIPAL,
                state="UNKNOWN",
                latest_event={"public_state": "UNKNOWN"},
            ),
        )

    def handle(self, _operation: str, **_kwargs: object) -> dict[str, bool]:
        self.dispatch_calls += 1
        return {"accepted": True}


def _install_fake_risk_component(monkeypatch, adapter: _StartupProbeAdapter) -> None:
    from bt_api_py import service_components

    module = ModuleType("bt_api_py.service_risk_order_entry")
    module.SUPPORTED_OPERATIONS = frozenset({"orders.submit", "orders.query"})
    module.RiskOrderCommand = _FakeRiskOrderCommand
    module.validate_service_options = lambda options, bind_ports: {
        "role": options["role"],
        "account_policy_ref": options["account_policy_ref"],
    }
    module.RiskOrderEntryService = lambda *_args: adapter
    real_import_module = importlib.import_module
    monkeypatch.setattr(
        service_components,
        "importlib",
        SimpleNamespace(
            import_module=lambda name: (
                module if name == "bt_api_py.service_risk_order_entry" else real_import_module(name)
            )
        ),
    )


def _service_authority(config) -> TrustedServiceAuthority:
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, _strategy_secret = zmq.curve_keypair()
    strategy_identity = _CLIENT_IDENTITY
    grants = {
        _PRINCIPAL: (
            PrincipalGrant(
                strategy_identity,
                (
                    ResourceGrant(
                        _SERVICE_INSTANCE,
                        "service.order_entry",
                        _ORDER_SCOPE_REF,
                        _WIRE_SCOPE,
                        frozenset({"orders.submit"}),
                    ),
                    ResourceGrant(
                        _SERVICE_INSTANCE,
                        "service.order_query",
                        _QUERY_SCOPE_REF,
                        _WIRE_SCOPE,
                        frozenset({"orders.query"}),
                    ),
                ),
            ),
        )
    }
    return TrustedServiceAuthority(
        identity=_SERVICE_IDENTITY,
        scope=WireScope(_DOMAIN),
        scope_ref=config.scope_ref,
        scope_version="risk-order-scope-version-v1",
        security_ref=_SERVER_SECURITY_REF,
        security_version="risk-order-curve-version-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="risk-order-registry-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="risk-order-registry-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={strategy_public: _PRINCIPAL},
        grants=grants,
    )


def _startup_resources(events: list[str], scope: WireScope):
    from bt_api_py.service_components import ComponentResources

    account_scope = _FakeAccountScope("risk-account-scope-key")
    target = SimpleNamespace(account_scope=account_scope)
    values = {
        "bt_api": object(),
        "targets": {scope: target},
        "admission": {scope: object()},
        "journal": object(),
        "gate": object(),
        "provider_contracts": {scope: object()},
    }
    return ComponentResources.owned(
        values,
        ready=lambda: events.append("ready") is None,
        close=lambda: events.append("close"),
    )


def _start_host_with_probe(tmp_path, monkeypatch, *, submit_ready: bool):
    events: list[str] = []
    adapter = _StartupProbeAdapter(events, submit_ready=submit_ready)
    _install_fake_risk_component(monkeypatch, adapter)
    config = parse_service_config(_risk_service_config(_endpoint()))
    scope = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
    resources = _startup_resources(events, scope)
    host = ServiceHost(
        config,
        _service_authority(config),
        tmp_path / "risk-order-startup-registry.sqlite3",
        lambda *_args: resources,
    )
    return host, adapter, events


def test_risk_owner_requires_submit_capability_before_recovery_and_closes_resources(
    tmp_path, monkeypatch
) -> None:
    host, adapter, events = _start_host_with_probe(
        tmp_path,
        monkeypatch,
        submit_ready=False,
    )
    with pytest.raises(ServiceHostError) as error:
        host.start()
    assert error.value.code == "ORDER_CAPABILITY_UNAVAILABLE"
    assert host.state == "failed"
    assert adapter.recovery_calls == 0
    assert adapter.dispatch_calls == 0
    assert events == ["capability", "close"]


def test_risk_owner_recovers_unknown_before_ready_without_startup_dispatch(
    tmp_path, monkeypatch
) -> None:
    host, adapter, events = _start_host_with_probe(
        tmp_path,
        monkeypatch,
        submit_ready=True,
    )
    try:
        host.start()
        assert host.state == "started"
        assert adapter.recovery_calls == 1
        assert adapter.dispatch_calls == 0
        assert events.index("recovery") < events.index("ready")
    finally:
        host.close()
    assert events.count("close") == 1


@pytest.mark.parametrize("invalid_field", [None, "public_state", "proof_sha256", "permit_id"])
def test_risk_owner_accepts_proven_recovery_ack_and_rejects_invalid_projection(
    tmp_path, monkeypatch, invalid_field
) -> None:
    host, adapter, events = _start_host_with_probe(tmp_path, monkeypatch, submit_ready=True)
    recover = adapter.recover_interrupted_dispatches

    def recovered_ack():
        original = recover()[0]
        payload = {
            "public_state": "ACKED_TRACKED",
            "dispatch_attempt_count": 1,
            "provider_order_id": "provider-order-a",
            "permit_id": "permit-a",
            "risk_intent_id": original.risk_intent_id,
            "proof_sha256": "a" * 64,
        }
        if invalid_field is not None:
            payload[invalid_field] = "invalid"
        return (
            replace(original, state="ACKED_TRACKED", permit_id="permit-a", latest_event=payload),
        )

    monkeypatch.setattr(adapter, "recover_interrupted_dispatches", recovered_ack)
    try:
        if invalid_field is None:
            host.start()
            assert host.state == "started"
            assert events.index("recovery") < events.index("ready")
        else:
            with pytest.raises(ServiceHostError, match="ORDER_RECOVERY_INVALID"):
                host.start()
            assert host.state == "failed"
            assert "ready" not in events
        assert adapter.recovery_calls == 1
        assert adapter.dispatch_calls == 0
    finally:
        host.close()
    assert events.count("close") == 1
