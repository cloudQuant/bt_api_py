"""Focused lifecycle tests for the fixed SDK service host."""

from __future__ import annotations

import importlib
import socket
from dataclasses import replace
from datetime import UTC
from pathlib import Path
from typing import Any

import bt_api_monitor.service_component as monitor_service_component
import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
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
from bt_api_py.service_config import ServiceConfigError, parse_service_config
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


def _request(
    operation: str,
    payload: dict[str, Any],
    *,
    schema_id: str,
    identity: WireIdentity = _CLIENT_IDENTITY,
    scope: WireScope = _REQUEST_SCOPE,
) -> WireEnvelope:
    _request.next_id += 1
    return WireEnvelope(
        header=WireHeader(
            protocol_version=WIRE_PROTOCOL_VERSION,
            kind=WireMessageKind.REQUEST,
            message_id=f"host-test-{_request.next_id}",
            correlation_id=None,
            identity=identity,
            scope=scope,
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
    market_endpoint: str | None = None,
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
                    "endpoints": {
                        "rpc_bind": listener_endpoint or _endpoint(),
                        **({"market_bind": market_endpoint} if market_endpoint else {}),
                    },
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


def _account_session_worker_component() -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": "gateway-session",
            "bind_ports": ["service.account_read", "service.provider_session"],
            "options": {
                "role": "account_session_worker",
                "account_session_ref": "trusted-session-v1",
            },
        },
    }


def _market_hub_component(*, instance_id: str = "market-source") -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": ["service.market_source"],
            "options": {"role": "market_hub"},
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


def _account_worker_setup(
    tmp_path: Path,
    *,
    fail_read: bool = False,
    failed_close_attempts: int = 0,
    invalid_worker_mapping: bool = False,
    refresh_interval_seconds: float = 10.0,
    max_staleness_seconds: float = 5.0,
):
    from datetime import datetime

    from bt_api_base.account_protocol import (
        AccountBalance,
        AccountKey,
        AccountObservation,
        AccountResourceKey,
        AccountValuation,
    )
    from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker
    from bt_api_gateway.event_journal import GatewayEventJournal
    from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
    from bt_api_gateway.service_component import SUPPORTED_OPERATIONS, GatewayServiceAccountScope
    from bt_api_gateway.session_guard import LocalAccountSessionGuard

    class Clock:
        def __init__(self) -> None:
            self.wall_datetime = datetime.now(UTC).replace(microsecond=0)
            self.wall_seconds = self.wall_datetime.timestamp()
            self.monotonic_seconds = 100.0

        def wall(self) -> float:
            return self.wall_seconds

        def monotonic(self) -> float:
            return self.monotonic_seconds

        def utc_text(self) -> str:
            return self.wall_datetime.strftime("%Y-%m-%dT%H:%M:%SZ")

    clock = Clock()
    account_key = AccountKey(
        provider="synthetic-provider",
        venue="synthetic-venue",
        environment="paper",
        account_id="account-a",
    )
    resource_key = AccountResourceKey(product_type="spot", margin_pool="default")
    observed_at = clock.utc_text()
    observation = AccountObservation(
        account_key=account_key,
        observation_id="observation-1",
        coverage=(resource_key,),
        balances=(
            AccountBalance(
                resource=resource_key,
                currency="USD",
                total="100",
                available="80",
                locked="20",
                observed_at_utc=observed_at,
            ),
        ),
        positions=(),
        valuation=AccountValuation(
            currency="USD",
            available_cash="80",
            equity="100",
            observed_at_utc=observed_at,
        ),
        started_at_utc=observed_at,
        completed_at_utc=observed_at,
        consistency="provider_atomic",
        balances_complete=True,
        positions_complete=True,
    )

    class Provider:
        def __init__(self) -> None:
            self.account_key = account_key
            self.connect_calls = 0
            self.read_calls = 0
            self.close_calls = 0
            self.connected_generation: str | None = None
            self.closed = False
            self.fail_reads = fail_read

        def connect(self, *, account_key, generation: str, timeout: float) -> None:
            assert account_key == self.account_key
            assert generation
            assert timeout > 0
            self.connect_calls += 1
            self.connected_generation = generation
            self.closed = False

        def read_account(self, *, generation: str, timeout: float) -> AccountObservation:
            assert timeout > 0
            self.read_calls += 1
            if self.fail_reads:
                raise RuntimeError("private provider read detail")
            assert generation == self.connected_generation
            return observation

        def close(self) -> bool:
            self.close_calls += 1
            if self.close_calls <= failed_close_attempts:
                raise RuntimeError("private provider close detail")
            self.closed = True
            return True

    provider = Provider()
    journal = GatewayEventJournal(tmp_path / "account-state.sqlite3")
    guard_directory = tmp_path / "account-guards"
    guard_directory.mkdir(exist_ok=True)
    guard = LocalAccountSessionGuard(guard_directory, account_key.account_key_id, "host-owner-v1")
    worker = GatewayAccountSessionWorker(
        provider,
        journal,
        account_key=account_key,
        journal_account_scope="journal-account-a",
        state_scope="shared-account-facts-v1",
        guard=guard,
        required_coverage=(resource_key,),
        refresh_interval_seconds=refresh_interval_seconds,
        max_staleness_seconds=max_staleness_seconds,
        request_timeout_seconds=2.0,
        clock=clock.wall,
        monotonic_clock=clock.monotonic,
    )
    account = GatewayServiceAccountScope(
        account_ref="account-a",
        execution_domain_ref="paper:demo",
        journal_account_scope=(
            "journal-account-mismatch" if invalid_worker_mapping else "journal-account-a"
        ),
        shared_account_facts_scope="shared-account-facts-v1",
        shared_fact_strategy_refs=frozenset({"strategy-a", "strategy-b"}),
    )
    principal = GatewayPrincipal(
        principal_id="curve-principal",
        account_scopes=frozenset({"journal-account-a"}),
        strategy_scopes=frozenset({"shared-account-facts-v1"}),
        allowed_kinds=frozenset({GatewayCommandKind.READ}),
    )
    config = _service_config(
        {"gateway": _account_session_worker_component()},
        service_id="gateway-service",
    )
    service_authority = WireScope(execution_domain_ref="paper:demo")
    client_public, _client_secret = zmq.curve_keypair()
    server_public, server_secret = zmq.curve_keypair()
    templates = {
        strategy_ref: WireScope("paper:demo", account_ref="account-a", strategy_ref=strategy_ref)
        for strategy_ref in ("strategy-a", "strategy-b")
    }
    grants = tuple(
        ResourceGrant(
            instance_id="gateway-session",
            port="service.account_read",
            scope_ref=f"account-{strategy_ref}-grant-v1",
            scope=scope,
            operations=SUPPORTED_OPERATIONS,
        )
        for strategy_ref, scope in templates.items()
    )
    authority = TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, "gateway-service", config.service_id),
        scope=service_authority,
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

    close_attempts: list[int] = []

    def close_resources() -> None:
        close_attempts.append(len(close_attempts) + 1)
        worker.close()

    resources = ComponentResources.owned(
        {
            "journal": journal,
            "accounts": {"account-a": account},
            "principals": {"curve-principal": principal},
        },
        ready=lambda: True,
        close=close_resources,
        internal_ports={"service.provider_session": worker},
    )

    def resource_provider(component, instance_id, options, scope):
        assert component == "gateway"
        assert instance_id == "gateway-session"
        assert dict(options) == {
            "role": "account_session_worker",
            "account_session_ref": "trusted-session-v1",
        }
        assert scope is authority.scope
        return resources

    host = ServiceHost(
        config,
        authority,
        tmp_path / "gateway-registry.sqlite3",
        resource_provider,
    )
    return (
        host,
        config,
        authority,
        worker,
        provider,
        clock,
        resources,
        close_attempts,
        templates,
    )


def _market_authority(config: Any) -> tuple[TrustedServiceAuthority, WireScope]:
    from bt_api_gateway.market_hub import MARKET_OPERATIONS

    client_public, _client_secret = zmq.curve_keypair()
    server_public, server_secret = zmq.curve_keypair()
    service_scope = WireScope("paper:demo")
    registration_template = WireScope(
        "paper:demo",
        strategy_ref="strategy-client-a",
    )
    capability = ResourceGrant(
        instance_id="market-source",
        port="service.market_source",
        scope_ref="market-grant-v1",
        scope=registration_template,
        operations=MARKET_OPERATIONS,
    )
    authority = TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, "market-service", config.service_id),
        scope=service_scope,
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
        grants={"curve-principal": (PrincipalGrant(_CLIENT_IDENTITY, (capability,)),)},
    )
    return authority, registration_template


def _market_key() -> MarketSubscriptionKey:
    return MarketSubscriptionKey(
        source_ref="source-v1",
        entitlement_ref="entitlement-v1",
        instrument=MarketInstrumentKey(
            provider="synthetic-provider",
            venue="synthetic-venue",
            environment="paper",
            product_type="future",
            instrument_id="instrument-1",
            native_symbol="SYNTH1",
        ),
        channel="trades",
    )


def _market_resource(
    authority: TrustedServiceAuthority,
    *,
    mismatched_principal: bool = False,
    close_events: list[str] | None = None,
) -> tuple[ComponentResources, Any, MarketSubscriptionKey]:
    from bt_api_gateway.market_hub import GatewayMarketHub

    key = _market_key()

    class Provider:
        def __init__(self) -> None:
            self.subscribed: list[MarketSubscriptionKey] = []
            self.generations: list[str] = []
            self.unsubscribed: list[object] = []

        def subscribe(self, market_key: MarketSubscriptionKey, *, generation: str) -> object:
            self.subscribed.append(market_key)
            self.generations.append(generation)
            return market_key

        def unsubscribe(self, handle: object) -> None:
            self.unsubscribed.append(handle)

    provider = Provider()
    principal_ref = "other-principal" if mismatched_principal else "curve-principal"
    hub = GatewayMarketHub(
        provider,
        key_universe={key},
        principal_allowed_keys={principal_ref: {key}},
        service_identity=authority.identity,
        service_scope=authority.scope,
    )

    def close_hub() -> None:
        if close_events is not None:
            close_events.append("resource.close")
        hub.close()

    resources = ComponentResources.owned(
        {"hub": hub},
        ready=lambda: True,
        close=close_hub,
    )
    return resources, provider, key


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


def test_market_source_uses_registry_registration_and_idle_lease_pruning(tmp_path, monkeypatch):
    from bt_api_gateway.market_hub import MARKET_OPERATIONS

    market_transport = importlib.import_module("bt_api_transport_zmq.market_v2")
    service_transport = importlib.import_module("bt_api_transport_zmq.service_v2")
    market_endpoint = _endpoint()
    config = _service_config(
        {"gateway": _market_hub_component()},
        service_id="market-service",
        market_endpoint=market_endpoint,
    )
    authority, grant_scope = _market_authority(config)
    resources, provider, key = _market_resource(authority)
    events: list[str] = []

    class Publisher:
        def __init__(self, endpoint, *_args, **_kwargs):
            self.endpoint = endpoint
            self.closed = False
            events.append("publisher.open")

        def publish(self, _event):
            events.append("publisher.publish")

        def close(self):
            events.append("publisher.close")
            self.closed = True

    monkeypatch.setattr(market_transport, "ZmqMarketPublisher", Publisher)
    monkeypatch.setattr(service_transport.ZmqServiceServer, "serve_once", lambda _s, _t: False)
    host = ServiceHost(
        config,
        authority,
        tmp_path / "market-registry.sqlite3",
        lambda *_args: resources,
    ).start()
    try:
        registry = host._registry
        assert registry is not None and registry.ready is True
        assert host.market_endpoint == market_endpoint
        assert host.healthy is True
        component = host._components[0]
        assert {
            (capability.port, capability.operations) for capability in component.capabilities
        } == {("service.market_source", MARKET_OPERATIONS)}
        assert set(component.handlers) == set(MARKET_OPERATIONS)

        client_scope = WireScope(
            grant_scope.execution_domain_ref,
            strategy_ref=grant_scope.strategy_ref,
            run_id="run-market-1",
        )
        registration_reply = registry.handle(
            "curve-principal",
            _request(
                "service.register",
                {
                    "registration_key": "market-registration",
                    "client_session_id": "market-client-session",
                    "instance_id": "market-source",
                    "port": "service.market_source",
                    "scope_ref": "market-grant-v1",
                    "operations": tuple(sorted(MARKET_OPERATIONS)),
                },
                schema_id="sdk.service.control",
                identity=_CLIENT_IDENTITY,
                scope=client_scope,
            ),
        )
        assert registration_reply["code"] == "OK"
        registration = registration_reply["result"]
        registration_id = registration["registration_id"]

        def call_market(operation: str, arguments: dict[str, Any]):
            return registry.handle(
                "curve-principal",
                _request(
                    operation,
                    {
                        "registration_id": registration_id,
                        "service_epoch": registration["service_epoch"],
                        "instance_id": "market-source",
                        "arguments": arguments,
                    },
                    schema_id="sdk.service.call",
                    identity=_CLIENT_IDENTITY,
                    scope=client_scope,
                ),
            )

        subscribed = call_market("market.subscribe", {"key": key.to_dict()})
        assert subscribed["code"] == "OK"
        hub = resources.values["hub"]
        assert hub.registration_active(key, registration_id) is True
        assert hub.registration_active(key, "payload-controlled-id") is False
        assert provider.subscribed == [key]
        assert len(provider.generations) == 1
        assert isinstance(provider.generations[0], str) and provider.generations[0]

        spoofed = call_market(
            "market.unsubscribe",
            {"key": key.to_dict(), "registration_id": "payload-controlled-id"},
        )
        assert spoofed["code"] == "HANDLER_FAILED"
        assert hub.registration_active(key, registration_id) is True

        active_reads: list[str] = []
        original_active_ids = registry.active_registration_ids

        def observe_active_ids(*, instance_id: str):
            active_reads.append(instance_id)
            return original_active_ids(instance_id=instance_id)

        monkeypatch.setattr(registry, "active_registration_ids", observe_active_ids)
        assert host.serve_once(0) is False
        assert active_reads == ["market-source", "market-source"]
        assert host.summary["market"]["active_registrations"] == 1

        maintenance_failure = {"enabled": True}

        def fail_registry_projection(*, instance_id: str):
            active_reads.append(instance_id)
            if maintenance_failure["enabled"]:
                raise OSError("private registry read detail")
            return original_active_ids(instance_id=instance_id)

        monkeypatch.setattr(registry, "active_registration_ids", fail_registry_projection)
        assert host.serve_once(0) is False
        assert registry.ready is False
        assert host.healthy is False
        assert host.summary["market"]["maintenance_failures"] == ("market-source",)

        maintenance_failure["enabled"] = False
        assert host.serve_once(0) is False
        assert registry.ready is True
        assert host.healthy is True
        assert host.summary["market"]["active_registrations"] == 1

        unregister_reply = registry.handle(
            "curve-principal",
            _request(
                "service.unregister",
                {
                    "registration_id": registration_id,
                    "service_epoch": registration["service_epoch"],
                },
                schema_id="sdk.service.control",
                identity=_CLIENT_IDENTITY,
                scope=client_scope,
            ),
        )
        assert unregister_reply["code"] == "OK"
        assert host.serve_once(0) is False
        assert active_reads == ["market-source"] * 8
        assert hub.registration_active(key, registration_id) is False
        assert provider.unsubscribed == [key]
        assert host.summary["market"]["active_registrations"] == 0
    finally:
        host.close()
    assert host.state == "closed"
    assert events.count("publisher.open") == 1
    assert events.count("publisher.close") == 1


def test_invalid_market_audience_is_rejected_before_either_listener_binds(tmp_path, monkeypatch):
    market_transport = importlib.import_module("bt_api_transport_zmq.market_v2")
    service_transport = importlib.import_module("bt_api_transport_zmq.service_v2")
    config = _service_config(
        {"gateway": _market_hub_component()},
        service_id="market-service",
        market_endpoint=_endpoint(),
    )
    authority, _grant_scope = _market_authority(config)
    resource_close_events: list[str] = []
    resources, _provider, _key = _market_resource(
        authority,
        mismatched_principal=True,
        close_events=resource_close_events,
    )
    provider_calls: list[str] = []
    bind_attempts: list[str] = []

    def forbidden_publisher(*_args, **_kwargs):
        bind_attempts.append("market")
        raise AssertionError("publisher must not be constructed for a malformed audience")

    def forbidden_listener(*_args, **_kwargs):
        bind_attempts.append("rpc")
        raise AssertionError("RPC listener must not be constructed for a malformed audience")

    monkeypatch.setattr(market_transport, "ZmqMarketPublisher", forbidden_publisher)
    monkeypatch.setattr(service_transport, "ZmqServiceServer", forbidden_listener)
    host = ServiceHost(
        config,
        authority,
        tmp_path / "market-registry.sqlite3",
        lambda component, *_args: provider_calls.append(component) or resources,
    )

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "MARKET_HUB_AUDIENCE_INVALID"
    assert provider_calls == ["gateway"]
    assert bind_attempts == []
    assert resource_close_events == ["resource.close"]
    assert host._server is None
    assert host._market_publisher is None
    assert host._registry is None
    assert host.state == "failed"


def test_market_bind_without_selected_market_component_fails_before_provider(tmp_path):
    config = _service_config(
        {"monitor": _monitor_component()},
        market_endpoint=_endpoint(),
    )
    provider_calls: list[str] = []
    host = ServiceHost(
        config,
        _authority(config),
        tmp_path / "registry.sqlite3",
        lambda component, *_args: provider_calls.append(component),
    )

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "MARKET_ENDPOINT_WITHOUT_COMPONENT"
    assert provider_calls == []
    assert not (tmp_path / "registry.sqlite3").exists()


def test_market_component_without_market_bind_is_rejected_by_parser():
    with pytest.raises(ServiceConfigError):
        _service_config({"gateway": _market_hub_component()})


def test_market_publisher_close_failure_retains_hub_until_explicit_retry(tmp_path, monkeypatch):
    market_transport = importlib.import_module("bt_api_transport_zmq.market_v2")
    service_transport = importlib.import_module("bt_api_transport_zmq.service_v2")
    market_endpoint = _endpoint()
    config = _service_config(
        {"gateway": _market_hub_component()},
        service_id="market-service",
        market_endpoint=market_endpoint,
    )
    authority, _grant_scope = _market_authority(config)
    resource_close_events: list[str] = []
    resources, _provider, _key = _market_resource(authority, close_events=resource_close_events)
    events: list[str] = []
    publisher_closes: list[int] = []

    class RetainedPublisher:
        def __init__(self, endpoint, *_args, **_kwargs):
            self.endpoint = endpoint

        def publish(self, _event):
            return None

        def close(self):
            events.append("publisher.close")
            publisher_closes.append(1)
            if len(publisher_closes) == 1:
                raise OSError("private publisher cleanup detail")

    original_server_close = service_transport.ZmqServiceServer.close

    def observed_server_close(server):
        events.append("rpc.close")
        return original_server_close(server)

    monkeypatch.setattr(market_transport, "ZmqMarketPublisher", RetainedPublisher)
    monkeypatch.setattr(service_transport.ZmqServiceServer, "close", observed_server_close)
    host = ServiceHost(
        config,
        authority,
        tmp_path / "market-registry.sqlite3",
        lambda *_args: resources,
    ).start()

    with pytest.raises(ServiceHostError) as exc_info:
        host.close()

    assert exc_info.value.code == "CLOSE_FAILED"
    assert host.state == "close_failed"
    assert host._server is None
    assert host._market_publisher is not None
    assert host._components
    assert resource_close_events == []
    assert events == ["rpc.close", "publisher.close"]

    host.close()
    assert host.state == "closed"
    assert host._market_publisher is None
    assert host._components == []
    assert publisher_closes == [1, 1]
    assert resource_close_events == ["resource.close"]
    assert events == ["rpc.close", "publisher.close", "publisher.close"]


def test_account_session_worker_shares_one_read_across_strategies_and_survives_one_unregister(
    tmp_path,
):
    (
        host,
        _config,
        _authority_value,
        worker,
        provider,
        _clock,
        _resources,
        _close_attempts,
        _templates,
    ) = _account_worker_setup(tmp_path)
    from bt_api_gateway.service_component import SUPPORTED_OPERATIONS

    try:
        host.start()
        assert worker.ready is True
        assert provider.connect_calls == 1
        assert provider.read_calls == 1
        registry = host._registry
        assert registry is not None and registry.ready is True
        account_read_capability = host._components[0].capabilities[0]
        assert account_read_capability.operations == SUPPORTED_OPERATIONS
        assert account_read_capability.write_operations == frozenset()

        registered: dict[str, tuple[dict[str, Any], WireScope]] = {}
        for strategy_ref in ("strategy-a", "strategy-b"):
            scope = WireScope(
                "paper:demo",
                account_ref="account-a",
                strategy_ref=strategy_ref,
                run_id=f"run-{strategy_ref}",
            )
            grant_ref = f"account-{strategy_ref}-grant-v1"
            reply = registry.handle(
                "curve-principal",
                _request(
                    "service.register",
                    {
                        "registration_key": f"registration-{strategy_ref}",
                        "client_session_id": f"client-session-{strategy_ref}",
                        "instance_id": "gateway-session",
                        "port": "service.account_read",
                        "scope_ref": grant_ref,
                        "operations": tuple(sorted(SUPPORTED_OPERATIONS)),
                    },
                    schema_id="sdk.service.control",
                    identity=_CLIENT_IDENTITY,
                    scope=scope,
                ),
            )
            assert reply["code"] == "OK"
            registered[strategy_ref] = (reply["result"], scope)

        generation = worker.session_generation
        for strategy_ref in ("strategy-a", "strategy-b"):
            registration, scope = registered[strategy_ref]
            reply = registry.handle(
                "curve-principal",
                _request(
                    "account.snapshot",
                    {
                        "registration_id": registration["registration_id"],
                        "service_epoch": registration["service_epoch"],
                        "instance_id": "gateway-session",
                        "arguments": {"limit": 1},
                    },
                    schema_id="sdk.service.call",
                    identity=_CLIENT_IDENTITY,
                    scope=scope,
                ),
            )
            assert reply["code"] == "OK"
            result = reply["result"]
            assert result["state_status"]["ready"] is True
            assert result["state_status"]["session_generation"] == generation
            assert result["snapshot"]["payload"]["session_generation"] == generation

        assert provider.connect_calls == 1
        assert provider.read_calls == 1

        registration, scope = registered["strategy-a"]
        tracked_read = registry.handle(
            "curve-principal",
            _request(
                "account.read_after",
                {
                    "registration_id": registration["registration_id"],
                    "service_epoch": registration["service_epoch"],
                    "instance_id": "gateway-session",
                    "arguments": {"cursor": 0, "limit": 1, "track_delivery": True},
                },
                schema_id="sdk.service.call",
                identity=_CLIENT_IDENTITY,
                scope=scope,
            ),
        )
        assert tracked_read["code"] == "OK"
        progress = tracked_read["result"]["consumer_progress"]
        assert set(progress) == {"stream_epoch", "offered_cursor", "acknowledged_cursor"}
        assert progress["offered_cursor"] == tracked_read["result"]["next_cursor"]

        progress_reply = registry.handle(
            "curve-principal",
            _request(
                "account.progress",
                {
                    "registration_id": registration["registration_id"],
                    "service_epoch": registration["service_epoch"],
                    "instance_id": "gateway-session",
                    "arguments": {},
                },
                schema_id="sdk.service.call",
                identity=_CLIENT_IDENTITY,
                scope=scope,
            ),
        )
        assert progress_reply["code"] == "OK"
        assert progress_reply["result"]["consumer_progress"] == progress

        acknowledged = registry.handle(
            "curve-principal",
            _request(
                "account.ack",
                {
                    "registration_id": registration["registration_id"],
                    "service_epoch": registration["service_epoch"],
                    "instance_id": "gateway-session",
                    "arguments": {
                        "stream_epoch": progress["stream_epoch"],
                        "cursor": progress["offered_cursor"],
                    },
                },
                schema_id="sdk.service.call",
                identity=_CLIENT_IDENTITY,
                scope=scope,
            ),
        )
        assert acknowledged["code"] == "OK"
        acknowledged_progress = acknowledged["result"]["consumer_progress"]
        assert acknowledged_progress == {
            **progress,
            "acknowledged_cursor": progress["offered_cursor"],
        }

        persisted_progress = registry.handle(
            "curve-principal",
            _request(
                "account.progress",
                {
                    "registration_id": registration["registration_id"],
                    "service_epoch": registration["service_epoch"],
                    "instance_id": "gateway-session",
                    "arguments": {},
                },
                schema_id="sdk.service.call",
                identity=_CLIENT_IDENTITY,
                scope=scope,
            ),
        )
        assert persisted_progress["code"] == "OK"
        assert persisted_progress["result"]["consumer_progress"] == acknowledged_progress

        unregistered = registry.handle(
            "curve-principal",
            _request(
                "service.unregister",
                {
                    "registration_id": registration["registration_id"],
                    "service_epoch": registration["service_epoch"],
                },
                schema_id="sdk.service.control",
                identity=_CLIENT_IDENTITY,
                scope=scope,
            ),
        )
        assert unregistered["code"] == "OK"
        assert host.serve_once(0) is False
        assert provider.close_calls == 0
        assert provider.connect_calls == 1
        assert provider.read_calls == 1

        registration, scope = registered["strategy-b"]
        remaining_read = registry.handle(
            "curve-principal",
            _request(
                "account.snapshot",
                {
                    "registration_id": registration["registration_id"],
                    "service_epoch": registration["service_epoch"],
                    "instance_id": "gateway-session",
                    "arguments": {"limit": 1},
                },
                schema_id="sdk.service.call",
                identity=_CLIENT_IDENTITY,
                scope=scope,
            ),
        )
        assert remaining_read["code"] == "OK"
        assert provider.close_calls == 0
        assert provider.read_calls == 1
    finally:
        host.close()

    assert provider.close_calls == 1
    assert provider.closed is True


def test_account_session_start_failure_closes_tracked_worker_resources(tmp_path):
    (
        host,
        _config,
        _authority_value,
        worker,
        provider,
        _clock,
        _resources,
        close_attempts,
        _templates,
    ) = _account_worker_setup(tmp_path, fail_read=True)

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "ACCOUNT_SESSION_WORKER_START_FAILED"
    assert host.state == "failed"
    assert worker.state == "closed"
    assert provider.connect_calls == 1
    assert provider.read_calls == 1
    assert provider.close_calls == 1
    assert provider.closed is True
    assert close_attempts == [1]
    assert host._pending_resources == []


def test_account_session_invalid_mapping_fails_before_provider_connect(tmp_path):
    (
        host,
        _config,
        _authority_value,
        worker,
        provider,
        _clock,
        _resources,
        close_attempts,
        _templates,
    ) = _account_worker_setup(tmp_path, invalid_worker_mapping=True)

    with pytest.raises(ServiceHostError) as exc_info:
        host.start()

    assert exc_info.value.code == "START_FAILED"
    assert host.state == "failed"
    assert provider.connect_calls == 0
    assert provider.read_calls == 0
    assert worker.state == "closed"
    assert provider.close_calls == 0
    assert close_attempts == [1]


def test_account_session_provider_close_failure_retains_host_resources_for_retry(tmp_path):
    (
        host,
        _config,
        _authority_value,
        worker,
        provider,
        _clock,
        _resources,
        close_attempts,
        _templates,
    ) = _account_worker_setup(tmp_path, failed_close_attempts=1)
    try:
        host.start()

        with pytest.raises(ServiceHostError) as exc_info:
            host.close()

        assert exc_info.value.code == "CLOSE_FAILED"
        assert host.state == "close_failed"
        assert host._components
        assert worker.state == "close_failed"
        assert provider.close_calls == 1
        assert provider.closed is False
        assert len(close_attempts) == 1

        host.close()
        assert host.state == "closed"
        assert host._components == []
        assert worker.state == "closed"
        assert provider.close_calls == 2
        assert provider.closed is True
        assert close_attempts == [1, 2]
    finally:
        if host.state != "closed":
            host.close()


def test_expired_account_baseline_withdraws_readiness_and_maintains_without_reconnect(
    tmp_path,
):
    (
        host,
        _config,
        _authority_value,
        worker,
        provider,
        clock,
        _resources,
        _close_attempts,
        _templates,
    ) = _account_worker_setup(
        tmp_path,
        refresh_interval_seconds=1.0,
        max_staleness_seconds=5.0,
    )
    try:
        host.start()
        registry = host._registry
        assert registry is not None and registry.ready is True
        assert worker.ready is True

        provider.fail_reads = True
        clock.monotonic_seconds += 6.0
        assert host.serve_once(0) is False

        assert provider.connect_calls == 1
        assert provider.read_calls == 2
        assert worker.state == "refresh_failed"
        assert worker.ready is False
        assert host.healthy is False
        assert registry.ready is False
        assert provider.read_calls == 2
    finally:
        host.close()

    assert provider.close_calls == 1
