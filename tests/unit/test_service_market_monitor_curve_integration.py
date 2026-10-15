"""Real Gateway-to-Monitor projection over spawned CURVE ServiceHosts.

The provider is deterministic and local. This case proves receipt through the
normal public Gateway market client, relay, durable Monitor ingress, and three
separately authorized read views; it does not synthesize execution facts.
"""

from __future__ import annotations

import multiprocessing
import socket
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.market_hub import GatewayMarketHub
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.market_observation import GATEWAY_MARKET_OBSERVATION_EVENT_TYPE
from bt_api_monitor.service_component import MonitorReadTarget
from bt_api_monitor.service_ingress import MonitorIngressTarget

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import (
    ServiceHost,
    TrustedServiceAuthority,
    TrustedServiceClients,
)
from bt_api_py.service_market import ServiceMarketClient, ZmqMarketTransportFactory
from bt_api_py.service_market_monitor import ServiceMarketMonitorRelay
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_DOMAIN = "paper:mon03-curve"
_ACCOUNT = "account:synthetic-a"
_STRATEGY = "strategy:synthetic-a"
_OTHER_ACCOUNT = "account:synthetic-b"
_SYSTEM_SCOPE = WireScope(_DOMAIN)
_ACCOUNT_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT)
_STRATEGY_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
_OTHER_ACCOUNT_SCOPE = WireScope(_DOMAIN, account_ref=_OTHER_ACCOUNT)

_GATEWAY_ID = "mon03-synthetic-gateway"
_GATEWAY_INSTANCE = "mon03-market-hub"
_GATEWAY_SCOPE_REF = "mon03-gateway-system-v1"
_GATEWAY_SECURITY_REF = "mon03-gateway-server-curve-v1"
_GATEWAY_PRINCIPAL = "mon03-gateway-service"
_GATEWAY_IDENTITY = WireIdentity(WireRole.SERVICE, _GATEWAY_PRINCIPAL, _GATEWAY_ID)

_INGRESS_ID = "mon03-synthetic-monitor-ingress"
_INGRESS_INSTANCE = "mon03-monitor-ingress"
_INGRESS_SCOPE_REF = "mon03-monitor-system-v1"
_INGRESS_SECURITY_REF = "mon03-monitor-ingress-server-curve-v1"
_INGRESS_PRINCIPAL = "mon03-monitor-ingress-service"
_INGRESS_IDENTITY = WireIdentity(WireRole.SERVICE, _INGRESS_PRINCIPAL, _INGRESS_ID)

_READ_ID = "mon03-synthetic-monitor-reader"
_READ_INSTANCE = "mon03-monitor-read"
_READ_SECURITY_REF = "mon03-monitor-read-server-curve-v1"
_READ_PRINCIPAL = "mon03-monitor-read-service"
_READ_IDENTITY = WireIdentity(WireRole.SERVICE, _READ_PRINCIPAL, _READ_ID)

_RELAY_PRINCIPAL = "mon03-synthetic-market-relay"
_RELAY_ACTOR = "mon03-service-process"
_RELAY_IDENTITY = WireIdentity(WireRole.SERVICE, _RELAY_PRINCIPAL, _RELAY_ACTOR)
_APPLICATION_PRINCIPAL = "mon03-synthetic-monitor-reader-app"
_APPLICATION_ID = "mon03-synthetic-dashboard"
_APPLICATION_IDENTITY = WireIdentity(
    WireRole.APPLICATION_CLIENT, _APPLICATION_PRINCIPAL, _APPLICATION_ID
)

_SYSTEM_OUTBOX_SCOPE = "mon03:system"
_ACCOUNT_OUTBOX_SCOPE = "mon03:account-a"
_STRATEGY_OUTBOX_SCOPE = "mon03:strategy-a"
_SYSTEM_READ_SCOPE_REF = "mon03-read-system-v1"
_ACCOUNT_READ_SCOPE_REF = "mon03-read-account-v1"
_STRATEGY_READ_SCOPE_REF = "mon03-read-strategy-v1"
_OTHER_ACCOUNT_READ_SCOPE_REF = "mon03-read-account-b-v1"
_VIEW_SCOPES = (
    (_SYSTEM_READ_SCOPE_REF, _SYSTEM_SCOPE, _SYSTEM_OUTBOX_SCOPE),
    (_ACCOUNT_READ_SCOPE_REF, _ACCOUNT_SCOPE, _ACCOUNT_OUTBOX_SCOPE),
    (_STRATEGY_READ_SCOPE_REF, _STRATEGY_SCOPE, _STRATEGY_OUTBOX_SCOPE),
)

_MARKET_OPERATIONS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
_EVENT_TYPES = (GATEWAY_MARKET_OBSERVATION_EVENT_TYPE,)
_REQUEST_TIMEOUT_MS = 2_000
_HEARTBEAT_SECONDS = 5
_MAX_MESSAGE_BYTES = 262_144

_MARKET_KEY = MarketSubscriptionKey(
    source_ref="synthetic-public-market",
    entitlement_ref="synthetic-entitlement",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="loopback",
        environment="paper",
        product_type="spot",
        instrument_id="mon03-instrument-a",
        native_symbol="MON03-A-USD",
    ),
    channel="trades",
)


def _free_endpoints(count: int) -> tuple[str, ...]:
    probes: list[socket.socket] = []
    try:
        endpoints: list[str] = []
        for _ in range(count):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            probes.append(probe)
            endpoints.append(f"tcp://127.0.0.1:{probe.getsockname()[1]}")
        return tuple(endpoints)
    finally:
        for probe in probes:
            probe.close()


def _component(instance_id: str, port: str, options: dict[str, Any]) -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {"instance_id": instance_id, "bind_ports": [port], "options": options},
    }


def _server_config(
    *,
    service_id: str,
    scope_ref: str,
    security_ref: str,
    endpoint: str,
    component: str,
    instance_id: str,
    port: str,
    options: dict[str, Any],
    market_endpoint: str | None = None,
) -> dict[str, Any]:
    endpoints = {"rpc_bind": endpoint}
    if market_endpoint is not None:
        endpoints["market_bind"] = market_endpoint
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": service_id,
        "scope_ref": scope_ref,
        "transport_ref": "listener-v1",
        "registration_policy_ref": f"{service_id}-registration-v1",
        "service_requirements_ref": f"{service_id}-requirements-v1",
        "components": {
            component: _component(instance_id, port, options),
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": endpoints,
                    "security_profile_ref": security_ref,
                    "options": {
                        "high_water_mark": 64,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _build_service_configs(endpoints: tuple[str, str, str, str]) -> dict[str, dict[str, Any]]:
    gateway_rpc, gateway_market, ingress_rpc, reader_rpc = endpoints
    gateway = _server_config(
        service_id=_GATEWAY_ID,
        scope_ref="mon03-gateway-host-v1",
        security_ref=_GATEWAY_SECURITY_REF,
        endpoint=gateway_rpc,
        market_endpoint=gateway_market,
        component="gateway",
        instance_id=_GATEWAY_INSTANCE,
        port="service.market_source",
        options={"role": "market_hub"},
    )
    ingress = _server_config(
        service_id=_INGRESS_ID,
        scope_ref="mon03-ingress-host-v1",
        security_ref=_INGRESS_SECURITY_REF,
        endpoint=ingress_rpc,
        component="monitor",
        instance_id=_INGRESS_INSTANCE,
        port="service.telemetry_ingress",
        options={
            "role": "durable_ingress",
            "max_events": 32,
            "max_bytes": 65_536,
            "max_event_bytes": 16_384,
        },
    )
    reader = _server_config(
        service_id=_READ_ID,
        scope_ref="mon03-reader-host-v1",
        security_ref=_READ_SECURITY_REF,
        endpoint=reader_rpc,
        component="monitor",
        instance_id=_READ_INSTANCE,
        port="service.monitor_query",
        options={},
    )
    _add_relay_clients(
        reader,
        gateway_rpc=gateway_rpc,
        gateway_market=gateway_market,
        ingress_rpc=ingress_rpc,
    )
    return {"gateway": gateway, "ingress": ingress, "reader": reader}


def _parse_all_mon03_configs(
    service_configs: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    parsed_services = {name: parse_service_config(raw) for name, raw in service_configs.items()}
    app_scope_refs = [scope_ref for scope_ref, _scope, _outbox in _VIEW_SCOPES]
    app_scope_refs.append(_OTHER_ACCOUNT_READ_SCOPE_REF)
    parsed_clients = {
        scope_ref: parse_client_config(_application_config(scope_ref))
        for scope_ref in app_scope_refs
    }
    return parsed_services, parsed_clients


def _authority(
    config,
    *,
    identity: WireIdentity,
    security_ref: str,
    server_keys: tuple[bytes, bytes],
    principals: dict[bytes, str],
    grants: dict[str, tuple[PrincipalGrant, ...]],
) -> TrustedServiceAuthority:
    return TrustedServiceAuthority(
        identity=identity,
        scope=_SYSTEM_SCOPE,
        scope_ref=config.scope_ref,
        scope_version="mon03-system-scope-v1",
        security_ref=security_ref,
        security_version="curve-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="registration-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="requirements-v1",
        curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],
        principals=principals,
        grants=grants,
    )


class _SyntheticProvider:
    """Single-stream provider that publishes only a deterministic local marker."""

    def __init__(self) -> None:
        self.hub: GatewayMarketHub | None = None
        self.generation: str | None = None

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        if key != _MARKET_KEY or not generation:
            raise AssertionError("unexpected synthetic market subscription")
        self.generation = generation
        return generation

    def unsubscribe(self, handle: object) -> bool:
        if self.generation is None or handle != self.generation:
            raise AssertionError("synthetic Gateway released an unknown subscription")
        self.generation = None
        return True

    def publish(self, marker: str) -> int:
        if self.hub is None or self.generation is None:
            raise AssertionError("synthetic provider has no active Gateway stream")
        sent_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        frame = self.hub.publish(
            _MARKET_KEY,
            {"synthetic_marker": marker},
            sent_at,
            generation=self.generation,
        )
        return frame.header.sequence


def _host_process(
    control,
    mode: str,
    raw_config,
    registry_path: str,
    outbox_path: str,
    keys,
    raw_service_directory=None,
):
    """Spawn target for one real ServiceHost; all provider and records are synthetic."""

    host: ServiceHost | None = None
    provider: _SyntheticProvider | None = None
    client_runtime: ServiceClientRuntime | None = None
    market_client: ServiceMarketClient | None = None
    relay: ServiceMarketMonitorRelay | None = None
    try:
        config = parse_service_config(raw_config)
        if mode == "gateway":
            server_identity = _GATEWAY_IDENTITY
            security_ref = _GATEWAY_SECURITY_REF
            reader_public, _reader_secret = keys["reader_client"]
            relay_public, _relay_secret = keys["relay_client"]
            principals = {
                reader_public: _READ_PRINCIPAL,
                relay_public: _RELAY_PRINCIPAL,
            }
            market_grant = ResourceGrant(
                _GATEWAY_INSTANCE,
                "service.market_source",
                _GATEWAY_SCOPE_REF,
                _SYSTEM_SCOPE,
                _MARKET_OPERATIONS,
            )
            grants = {
                _READ_PRINCIPAL: (PrincipalGrant(_READ_IDENTITY, (market_grant,)),),
                _RELAY_PRINCIPAL: (PrincipalGrant(_RELAY_IDENTITY, (market_grant,)),),
            }
            provider = _SyntheticProvider()
            hub = GatewayMarketHub(
                provider,
                key_universe=frozenset({_MARKET_KEY}),
                principal_allowed_keys={
                    _READ_PRINCIPAL: frozenset({_MARKET_KEY}),
                    _RELAY_PRINCIPAL: frozenset({_MARKET_KEY}),
                },
                service_identity=server_identity,
                service_scope=_SYSTEM_SCOPE,
            )
            provider.hub = hub

            def resources(component, _instance, _options, _scope):
                if component != "gateway":
                    raise AssertionError("unexpected Gateway component")
                return ComponentResources.owned(
                    {"hub": hub}, ready=lambda: hub.ready, close=hub.close
                )

            authority = _authority(
                config,
                identity=server_identity,
                security_ref=security_ref,
                server_keys=keys["gateway_server"],
                principals=principals,
                grants=grants,
            )
        elif mode == "ingress":
            server_identity = _INGRESS_IDENTITY
            security_ref = _INGRESS_SECURITY_REF
            reader_public, _reader_secret = keys["reader_client"]
            relay_public, _relay_secret = keys["relay_client"]
            principals = {
                reader_public: _READ_PRINCIPAL,
                relay_public: _RELAY_PRINCIPAL,
            }
            grant = ResourceGrant(
                _INGRESS_INSTANCE,
                "service.telemetry_ingress",
                _INGRESS_SCOPE_REF,
                _SYSTEM_SCOPE,
                frozenset({"events.append"}),
            )
            grants = {
                _READ_PRINCIPAL: (PrincipalGrant(_READ_IDENTITY, (grant,)),),
                _RELAY_PRINCIPAL: (PrincipalGrant(_RELAY_IDENTITY, (grant,)),),
            }
            outbox = DurableOutbox(outbox_path)
            targets = {
                _SYSTEM_SCOPE: MonitorIngressTarget(_SYSTEM_OUTBOX_SCOPE, _EVENT_TYPES),
                _ACCOUNT_SCOPE: MonitorIngressTarget(_ACCOUNT_OUTBOX_SCOPE, _EVENT_TYPES),
                _STRATEGY_SCOPE: MonitorIngressTarget(_STRATEGY_OUTBOX_SCOPE, _EVENT_TYPES),
            }

            def resources(component, _instance, _options, _scope):
                if component != "monitor":
                    raise AssertionError("unexpected Monitor ingress component")
                return ComponentResources.borrowed(
                    {"outbox": outbox, "targets": targets}, ready=lambda: True
                )

            authority = _authority(
                config,
                identity=server_identity,
                security_ref=security_ref,
                server_keys=keys["ingress_server"],
                principals=principals,
                grants=grants,
            )
        elif mode == "reader":
            server_identity = _READ_IDENTITY
            security_ref = _READ_SECURITY_REF
            app_public, _app_secret = keys["application_client"]
            principals = {app_public: _APPLICATION_PRINCIPAL}
            view_grants = tuple(
                ResourceGrant(
                    _READ_INSTANCE,
                    "service.monitor_query",
                    scope_ref,
                    scope,
                    frozenset({"monitor.read_after"}),
                )
                for scope_ref, scope, _outbox_scope in _VIEW_SCOPES
            )
            grants = {_APPLICATION_PRINCIPAL: (PrincipalGrant(_APPLICATION_IDENTITY, view_grants),)}
            outbox = DurableOutbox(outbox_path, read_only=True)
            targets = {
                scope: MonitorReadTarget(outbox_scope, _EVENT_TYPES)
                for _scope_ref, scope, outbox_scope in _VIEW_SCOPES
            }

            def resources(component, _instance, _options, _scope):
                if component != "monitor":
                    raise AssertionError("unexpected Monitor query component")
                return ComponentResources.borrowed(
                    {"outbox": outbox, "targets": targets},
                    ready=lambda: outbox.is_read_only,
                )

            authority = _authority(
                config,
                identity=server_identity,
                security_ref=security_ref,
                server_keys=keys["read_server"],
                principals=principals,
                grants=grants,
            )
        else:
            raise AssertionError("unknown synthetic host mode")

        service_directory = None
        trusted_clients = None
        if raw_service_directory is not None:
            service_directory = {
                service_config["service_id"]: parse_service_config(service_config)
                for service_config in raw_service_directory.values()
            }
        if mode == "reader":
            if service_directory is None:
                raise AssertionError("reader host requires its parsed outbound service directory")
            trusted_clients = _trusted_reader_clients(keys)
        host = ServiceHost(
            config,
            authority,
            registry_path,
            resources,
            service_directory=service_directory,
            trusted_clients=trusted_clients,
        )
        host.start()
        relay_cursor = None
        if mode == "reader":
            client_runtime = host.upstream
            if client_runtime is None or client_runtime.healthy is not True:
                raise AssertionError("reader host did not retain its healthy upstream runtime")
            market_client = ServiceMarketClient(
                client_runtime,
                "upstream.market",
                _MARKET_KEY,
                subscriber_factory=ZmqMarketTransportFactory(
                    {
                        _GATEWAY_SECURITY_REF: _client_keys(
                            keys["gateway_server"], keys["reader_client"]
                        )
                    }
                ),
                stream_scope=_SYSTEM_SCOPE,
                head_check_interval_seconds=0.02,
            )
            relay = ServiceMarketMonitorRelay(
                market_client,
                client_runtime,
                monitor_scope_ref=_INGRESS_SCOPE_REF,
                system_ref="mon03-system",
                process_ref="synthetic-service-process",
                max_pending_events=4,
                max_pending_bytes=16_384,
            )
            relay_cursor = relay.start().cursor_sequence
            mounts = {binding.mount for binding in client_runtime.bindings}
            assert mounts == {"upstream.market", "upstream.telemetry"}
            assert all(binding.active for binding in client_runtime.bindings)
            assert all(binding.scope == _SYSTEM_SCOPE for binding in client_runtime.bindings)
            assert client_runtime.identity.role is WireRole.SERVICE
            assert "runtime.telemetry" not in mounts
        control.send(
            (
                "ready",
                host.endpoint,
                host.market_endpoint if mode == "gateway" else None,
                relay_cursor,
            )
        )
        while True:
            host.serve_once(timeout_ms=10)
            if not control.poll():
                continue
            command = control.recv()
            if command[0] == "close":
                if market_client is not None:
                    market_client.close()
                    market_client = None
                if client_runtime is not None:
                    client_runtime = None
                host.close()
                control.send(("closed", host.state))
                return
            if mode == "gateway" and command[0] == "publish":
                sequence = provider.publish(command[1])
                control.send(("published", command[1], sequence))
                continue
            if mode == "reader" and command[0] == "pump" and relay is not None:
                frame = relay.pump_once(timeout_ms=command[1])
                status = relay.status
                projection_source = None
                if frame is not None:
                    projection_source = {
                        "principal_ref": frame.header.identity.principal_ref,
                        "execution_domain_ref": frame.header.scope.execution_domain_ref,
                        "stream_epoch": frame.header.stream_epoch,
                        "sequence": frame.header.sequence,
                        "sent_at_utc": frame.header.sent_at_utc,
                        "synthetic_marker": frame.payload["data"].get("synthetic_marker"),
                    }
                control.send(
                    (
                        "pumped",
                        projection_source,
                        status.received_frames,
                        status.appended_observations,
                        status.pending_events,
                        status.lost_observations,
                        status.append_failures,
                    )
                )
                continue
            raise AssertionError("unsupported synthetic host command")
    except BaseException as exc:
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        if market_client is not None:
            with suppress(BaseException):
                market_client.close()
        if client_runtime is not None:
            # ServiceHost owns the outbound runtime and closes it below.
            client_runtime = None
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        raise
    finally:
        control.close()


def _receive(pipe, timeout: float = 10.0):
    assert pipe.poll(timeout), "spawned service did not answer within its bound"
    result = pipe.recv()
    assert result[0] != "error", result
    return result


def _spawn_host(
    context,
    mode,
    raw_config,
    registry_path,
    outbox_path,
    keys,
    *,
    raw_service_directory=None,
):
    parent, child = context.Pipe(duplex=True)
    process = context.Process(
        target=_host_process,
        args=(
            child,
            mode,
            raw_config,
            str(registry_path),
            str(outbox_path),
            keys,
            raw_service_directory,
        ),
        name=f"mon03-{mode}",
    )
    process.start()
    child.close()
    return {"mode": mode, "process": process, "pipe": parent, "closed": False}


def _ready(record):
    result = _receive(record["pipe"])
    assert result[0] == "ready", result
    return result[1:]


def _close_host(record) -> None:
    process = record["process"]
    if record["closed"]:
        return
    assert process.is_alive(), f"{record['mode']} host exited before normal close"
    record["pipe"].send(("close",))
    result = _receive(record["pipe"])
    assert result == ("closed", "closed"), result
    process.join(10)
    assert not process.is_alive(), f"{record['mode']} host did not exit normally"
    assert process.exitcode == 0, f"{record['mode']} host exit={process.exitcode}"
    record["closed"] = True
    record["pipe"].close()
    process.close()


def _client_keys(server_keys, client_pair) -> ClientCurveKeys:
    return ClientCurveKeys(server_keys[0], client_pair[0], client_pair[1])


def _trusted_reader_clients(keys) -> TrustedServiceClients:
    return TrustedServiceClients(
        scope_resolver=lambda scope_ref: {
            _GATEWAY_SCOPE_REF: _SYSTEM_SCOPE,
            _INGRESS_SCOPE_REF: _SYSTEM_SCOPE,
        }[scope_ref],
        peer_resolver=lambda service_id: {
            _GATEWAY_ID: _GATEWAY_IDENTITY,
            _INGRESS_ID: _INGRESS_IDENTITY,
        }[service_id],
        transport_factory=ZmqTransportFactory(
            {
                _GATEWAY_SECURITY_REF: _client_keys(keys["gateway_server"], keys["reader_client"]),
                _INGRESS_SECURITY_REF: _client_keys(keys["ingress_server"], keys["reader_client"]),
            }
        ),
        requested_operations={
            "upstream.market": _MARKET_OPERATIONS,
            "upstream.telemetry": frozenset({"events.append"}),
        },
    )


def _add_relay_clients(
    reader_config: dict[str, Any], *, gateway_rpc: str, gateway_market: str, ingress_rpc: str
) -> None:
    """Compose the relay as clients of the real Monitor reader service host."""

    reader_config["clients"] = {
        "gateway": {
            "config_schema_version": 1,
            "settings": {
                "bindings": [
                    {
                        "mount": "upstream.market",
                        "service_id": _GATEWAY_ID,
                        "instance_id": _GATEWAY_INSTANCE,
                        "scope_ref": _GATEWAY_SCOPE_REF,
                        "transport_ref": "gateway-client-v1",
                    }
                ],
                "options": {
                    "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                    "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                },
            },
        },
        "monitor": {
            "config_schema_version": 1,
            "settings": {
                "bindings": [
                    {
                        "mount": "upstream.telemetry",
                        "service_id": _INGRESS_ID,
                        "instance_id": _INGRESS_INSTANCE,
                        "scope_ref": _INGRESS_SCOPE_REF,
                        "transport_ref": "monitor-client-v1",
                    }
                ],
                "options": {
                    "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                    "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                },
            },
        },
    }
    profiles = reader_config["communication"]["profiles"]
    profiles.update(
        {
            "gateway-client-v1": {
                "backend": "zmq",
                "config_schema_version": 1,
                "endpoints": {
                    "rpc_connect": gateway_rpc,
                    "market_connect": gateway_market,
                },
                "security_profile_ref": _GATEWAY_SECURITY_REF,
                "options": {
                    "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                    "high_water_mark": 64,
                    "max_message_bytes": _MAX_MESSAGE_BYTES,
                },
            },
            "monitor-client-v1": {
                "backend": "zmq",
                "config_schema_version": 1,
                "endpoints": {"rpc_connect": ingress_rpc},
                "security_profile_ref": _INGRESS_SECURITY_REF,
                "options": {
                    "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                    "high_water_mark": 64,
                    "max_message_bytes": _MAX_MESSAGE_BYTES,
                },
            },
        }
    )


def _market_relay_runtime(config, keys, monitor_scope=_SYSTEM_SCOPE):
    return ServiceClientRuntime(
        config,
        identity=_RELAY_IDENTITY,
        scope_resolver=lambda scope_ref: (
            monitor_scope if scope_ref == _INGRESS_SCOPE_REF else _SYSTEM_SCOPE
        ),
        peer_resolver=lambda service_id: {
            _GATEWAY_ID: _GATEWAY_IDENTITY,
            _INGRESS_ID: _INGRESS_IDENTITY,
        }[service_id],
        transport_factory=ZmqTransportFactory(
            {
                _GATEWAY_SECURITY_REF: _client_keys(keys["gateway_server"], keys["relay_client"]),
                _INGRESS_SECURITY_REF: _client_keys(keys["ingress_server"], keys["relay_client"]),
            }
        ),
        requested_operations={
            "upstream.market": _MARKET_OPERATIONS,
            "upstream.telemetry": frozenset({"events.append"}),
        },
    )


def _application_config(scope_ref: str) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "registration": {"application_ref": _APPLICATION_ID},
        "clients": {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "application.monitor",
                            "service_id": _READ_ID,
                            "instance_id": _READ_INSTANCE,
                            "scope_ref": scope_ref,
                            "transport_ref": "monitor-read-client-v1",
                        }
                    ],
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "monitor-read-client-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": "tcp://127.0.0.1:1"},
                    "security_profile_ref": _READ_SECURITY_REF,
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _application_runtime(endpoint, keys, scope_ref, requested_scope):
    config_raw = _application_config(scope_ref)
    config_raw["communication"]["profiles"]["monitor-read-client-v1"]["endpoints"][
        "rpc_connect"
    ] = endpoint
    config = parse_client_config(config_raw)
    return ServiceClientRuntime(
        config,
        identity=_APPLICATION_IDENTITY,
        scope_resolver=lambda _ref: requested_scope,
        peer_resolver=lambda _service_id: _READ_IDENTITY,
        transport_factory=ZmqTransportFactory(
            {_READ_SECURITY_REF: _client_keys(keys["read_server"], keys["application_client"])}
        ),
        requested_operations={"application.monitor": frozenset({"monitor.read_after"})},
    )


def test_mon03_service_and_client_configs_pass_the_public_parsers():
    service_configs = _build_service_configs(
        (
            "tcp://127.0.0.1:31001",
            "tcp://127.0.0.1:31002",
            "tcp://127.0.0.1:31003",
            "tcp://127.0.0.1:31004",
        )
    )
    parsed_services, parsed_clients = _parse_all_mon03_configs(service_configs)
    assert set(parsed_services) == {"gateway", "ingress", "reader"}
    assert parsed_services["reader"].components
    assert set(parsed_services["reader"].clients) == {"gateway", "monitor"}
    assert set(parsed_clients) == {
        _SYSTEM_READ_SCOPE_REF,
        _ACCOUNT_READ_SCOPE_REF,
        _STRATEGY_READ_SCOPE_REF,
        _OTHER_ACCOUNT_READ_SCOPE_REF,
    }


def test_gateway_market_frame_reaches_system_monitor_and_stays_out_of_other_scope_views(
    tmp_path,
):
    context = multiprocessing.get_context("spawn")
    endpoint_values = _free_endpoints(4)
    gateway_rpc, gateway_market, ingress_rpc, read_rpc = endpoint_values
    service_configs = _build_service_configs(endpoint_values)
    parsed_services, _parsed_app_configs = _parse_all_mon03_configs(service_configs)
    keys = {
        "gateway_server": zmq.curve_keypair(),
        "ingress_server": zmq.curve_keypair(),
        "read_server": zmq.curve_keypair(),
        "relay_client": zmq.curve_keypair(),
        "reader_client": zmq.curve_keypair(),
        "application_client": zmq.curve_keypair(),
    }
    outbox_path = tmp_path / "mon03-monitor.sqlite"

    hosts = []
    cleanup_failures: list[str] = []
    forced_cleanup: list[str] = []
    try:
        gateway_record = _spawn_host(
            context,
            "gateway",
            service_configs["gateway"],
            tmp_path / "gateway-registry.sqlite",
            outbox_path,
            keys,
        )
        hosts.append(gateway_record)
        actual_gateway_rpc, actual_market_endpoint, _gateway_cursor = _ready(gateway_record)

        ingress_record = _spawn_host(
            context,
            "ingress",
            service_configs["ingress"],
            tmp_path / "ingress-registry.sqlite",
            outbox_path,
            keys,
        )
        hosts.append(ingress_record)
        actual_ingress_rpc, _ingress_market, _ingress_cursor = _ready(ingress_record)

        reader_record = _spawn_host(
            context,
            "reader",
            service_configs["reader"],
            tmp_path / "reader-registry.sqlite",
            outbox_path,
            keys,
            raw_service_directory=service_configs,
        )
        hosts.append(reader_record)
        actual_read_rpc, _reader_market, relay_cursor = _ready(reader_record)
        assert actual_gateway_rpc == gateway_rpc
        assert actual_market_endpoint == gateway_market
        assert actual_ingress_rpc == ingress_rpc
        assert actual_read_rpc == read_rpc
        assert relay_cursor == 0

        # The same public runtime must not widen its Monitor append grant to an
        # account scope, even when the binding name and operation stay valid.
        unauthorized_runtime = _market_relay_runtime(
            parsed_services["reader"], keys, monitor_scope=_ACCOUNT_SCOPE
        )
        try:
            with pytest.raises(ServiceRuntimeError) as denied:
                unauthorized_runtime.start()
            assert denied.value.code == "SCOPE_DENIED"
        finally:
            unauthorized_runtime.close()

        published = gateway_record["pipe"]
        published.send(("publish", "synthetic-market-frame-a"))
        publish_result = _receive(published)
        assert publish_result == ("published", "synthetic-market-frame-a", 1)

        frame = None
        relay_status = None
        deadline = time.monotonic() + 8.0
        while frame is None and time.monotonic() < deadline:
            reader_record["pipe"].send(("pump", 50))
            result = _receive(reader_record["pipe"], timeout=2.0)
            assert result[0] == "pumped", result
            frame = result[1]
            relay_status = result[2:]
        assert frame is not None, "relay did not receive the published Gateway frame"
        assert frame["principal_ref"] == _GATEWAY_PRINCIPAL
        assert frame["execution_domain_ref"] == _DOMAIN
        assert frame["sequence"] == 1
        assert frame["synthetic_marker"] == "synthetic-market-frame-a"
        assert relay_status == (1, 1, 0, 0, 0)

        pages: dict[str, dict[str, Any]] = {}
        tail = None
        for scope_ref, _scope, _outbox_scope in _VIEW_SCOPES:
            view_runtime = _application_runtime(actual_read_rpc, keys, scope_ref, _scope)
            try:
                view_registration = view_runtime.start()
                assert view_registration.degraded_count == 0
                assert view_runtime.bindings[0].operations == frozenset({"monitor.read_after"})
                pages[scope_ref] = dict(
                    view_runtime.call(
                        "application.monitor",
                        "monitor.read_after",
                        {"after_sequence": 0, "limit": 10},
                    )
                )
                assert type(pages[scope_ref]["requested_cursor"]) is int
                assert type(pages[scope_ref]["next_cursor"]) is int
                assert type(pages[scope_ref]["has_more"]) is bool
                assert type(pages[scope_ref]["events"]) is tuple
                assert pages[scope_ref]["requested_cursor"] == 0
                assert pages[scope_ref]["has_more"] is False
                if scope_ref == _SYSTEM_READ_SCOPE_REF:
                    tail = dict(
                        view_runtime.call(
                            "application.monitor",
                            "monitor.read_after",
                            {
                                "after_sequence": pages[scope_ref]["next_cursor"],
                                "limit": 10,
                            },
                        )
                    )
                    assert type(tail["requested_cursor"]) is int
                    assert type(tail["next_cursor"]) is int
                    assert type(tail["has_more"]) is bool
                    assert type(tail["events"]) is tuple
            finally:
                view_runtime.close()

        denied_view = _application_runtime(
            actual_read_rpc, keys, _OTHER_ACCOUNT_READ_SCOPE_REF, _OTHER_ACCOUNT_SCOPE
        )
        try:
            with pytest.raises(ServiceRuntimeError) as denied:
                denied_view.start()
            assert denied.value.code == "SCOPE_DENIED"
        finally:
            denied_view.close()

        system_page = pages[_SYSTEM_READ_SCOPE_REF]
        assert len(system_page["events"]) == 1
        assert not pages[_ACCOUNT_READ_SCOPE_REF]["events"]
        assert not pages[_STRATEGY_READ_SCOPE_REF]["events"]
        observed = system_page["events"][0]
        assert set(observed) == {
            "sequence",
            "event_id",
            "event_type",
            "occurred_at",
            "data",
        }
        assert type(observed["sequence"]) is int
        assert type(observed["occurred_at"]) is float
        assert observed["sequence"] == 1
        assert (
            observed["occurred_at"]
            == datetime.fromisoformat(frame["sent_at_utc"][:-1] + "+00:00").timestamp()
        )
        assert observed["event_type"] == GATEWAY_MARKET_OBSERVATION_EVENT_TYPE
        assert observed["event_id"].startswith("service-event:")
        assert set(observed["data"]) == {"producer", "source_event_id", "payload"}
        assert observed["data"]["producer"] == {
            "role": WireRole.SERVICE.value,
            "principal_ref": _READ_PRINCIPAL,
            "actor_ref": _READ_ID,
            "delegate_ref": None,
        }
        source_request_id = observed["data"]["source_event_id"]
        projection = observed["data"]["payload"]
        assert source_request_id.startswith("gateway-market-observation-v1:")
        assert set(projection) == {
            "schema",
            "scope_type",
            "system_ref",
            "process_ref",
            "observation_kind",
            "source",
            "market",
        }
        assert projection["schema"] == "bt_api.monitor.market_observation.v1"
        assert projection["scope_type"] == "system"
        assert projection["system_ref"] == "mon03-system"
        assert projection["process_ref"] == "synthetic-service-process"
        assert projection["observation_kind"] == "consumer_received_frame"
        assert set(projection["source"]) == {
            "service_ref",
            "stream_epoch",
            "sequence",
            "gateway_sent_at_utc",
        }
        assert projection["source"]["service_ref"] == _GATEWAY_PRINCIPAL
        assert projection["source"]["stream_epoch"] == frame["stream_epoch"]
        assert projection["source"]["sequence"] == frame["sequence"]
        assert projection["source"]["gateway_sent_at_utc"] == frame["sent_at_utc"]
        assert projection["market"] == {
            "instrument": _MARKET_KEY.instrument.to_dict(),
            "channel": _MARKET_KEY.channel,
            "depth": _MARKET_KEY.depth,
            "timeframe": _MARKET_KEY.timeframe,
        }
        assert "synthetic_marker" not in projection

        assert tail is not None
        assert system_page["next_cursor"] == observed["sequence"]
        assert tail["requested_cursor"] == observed["sequence"]
        assert tail["next_cursor"] == observed["sequence"]
        assert tail["has_more"] is False
        assert not tail["events"]

        for host_record in reversed(hosts):
            _close_host(host_record)
    finally:
        for host_record in reversed(hosts):
            process = host_record["process"]
            if host_record["closed"]:
                continue
            try:
                if process.is_alive():
                    host_record["pipe"].send(("close",))
                    result = _receive(host_record["pipe"], timeout=5.0)
                    if result == ("closed", "closed"):
                        host_record["closed"] = True
                process.join(5)
                if process.is_alive():
                    forced_cleanup.append(host_record["mode"])
                    process.terminate()
                    process.join(5)
                if process.exitcode not in (0, None):
                    cleanup_failures.append(f"{host_record['mode']}_exit:{process.exitcode}")
            except Exception as exc:
                cleanup_failures.append(f"{host_record['mode']}_cleanup:{type(exc).__name__}")
            finally:
                with suppress(Exception):
                    host_record["pipe"].close()
                with suppress(Exception):
                    process.close()
        assert forced_cleanup == [], f"spawned hosts required forced cleanup: {forced_cleanup}"
        assert cleanup_failures == [], f"cleanup failures: {cleanup_failures}"
