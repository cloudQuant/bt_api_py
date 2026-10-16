"""Local service-chain coverage for market expiry roll and monitor replay.

Both providers are deterministic synthetic leaves.  The test keeps Gateway,
Monitor ingress/outbox, market clients/relay, and the account snapshot on their
normal authenticated service paths; it does not create or call Risk/order
routes.  A new market key never rewrites the account's old-contract position.
"""

from __future__ import annotations

import multiprocessing
import socket
import sys
import time
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import zmq
from bt_api_base.account_protocol import (
    AccountBalance,
    AccountKey,
    AccountObservation,
    AccountPosition,
    AccountResourceKey,
    AccountValuation,
)
from bt_api_base.market_observation_protocol import GatewaySystemTelemetryProjector
from bt_api_base.market_protocol import (
    MarketInstrumentKey,
    MarketProviderEvent,
    MarketSubscriptionKey,
)
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.market_hub import GatewayMarketHub
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.market_observation import GATEWAY_MARKET_OBSERVATION_EVENT_TYPE
from bt_api_monitor.service_ingress import MonitorIngressTarget
from bt_api_monitor.threshold_rules import ScopedThresholdRuleLoop, ThresholdRule

from bt_api_py.service_account import ServiceAccountClient
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_market import ServiceMarketClient, ZmqMarketTransportFactory
from bt_api_py.service_market_monitor import ServiceMarketMonitorRelay
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)
from tests.unit import test_service_account_integration as account_fixture
from tests.unit._spawned_process_helpers import (
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)

_DOMAIN = "paper:dim08-expiry-roll"
_SYSTEM_SCOPE = WireScope(_DOMAIN)
_OUTBOX_SCOPE = "dim08:market-system"
_MARKET_SERVICE_ID = "dim08-synthetic-market-monitor"
_MARKET_SERVICE_SCOPE_REF = "dim08-market-monitor-host-v1"
_MARKET_SECURITY_REF = "dim08-market-monitor-curve-v1"
_GATEWAY_INSTANCE = "dim08-market-hub"
_INGRESS_INSTANCE = "dim08-monitor-ingress"
_GATEWAY_SCOPE_REF = "dim08-market-source-v1"
_INGRESS_SCOPE_REF = "dim08-monitor-ingress-v1"
_RELAY_PRINCIPAL = "dim08-market-monitor-relay"
_RELAY_ACTOR = "dim08-local-service-process"
_RELAY_IDENTITY = WireIdentity(WireRole.SERVICE, _RELAY_PRINCIPAL, _RELAY_ACTOR)
_MARKET_SERVICE_IDENTITY = WireIdentity(
    WireRole.SERVICE, "dim08-market-monitor-service", _MARKET_SERVICE_ID
)
_SYSTEM_REF = "dim08-system"
_PROCESS_REF = "dim08-relay-process"
_MARKET_OPERATIONS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
_REQUEST_TIMEOUT_MS = 2_000
_MAX_MESSAGE_BYTES = 262_144
_RESOURCE = AccountResourceKey("future", "cross")
_ACCOUNT_KEY = AccountKey("synthetic", "loopback", "paper", "owner-dim08")
_OLD_INSTRUMENT = MarketInstrumentKey(
    provider="synthetic",
    venue="loopback",
    environment="paper",
    product_type="future",
    instrument_id="BTC-USD-202612",
    native_symbol="BTCUSD-202612",
)
_NEW_INSTRUMENT = MarketInstrumentKey(
    provider="synthetic",
    venue="loopback",
    environment="paper",
    product_type="future",
    instrument_id="BTC-USD-202703",
    native_symbol="BTCUSD-202703",
)
_OLD_KEY = MarketSubscriptionKey(
    source_ref="dim08-synthetic-public-market",
    entitlement_ref="dim08-loopback-entitlement",
    instrument=_OLD_INSTRUMENT,
    channel="quotes",
)
_NEW_KEY = MarketSubscriptionKey(
    source_ref="dim08-synthetic-public-market",
    entitlement_ref="dim08-loopback-entitlement",
    instrument=_NEW_INSTRUMENT,
    channel="quotes",
)
_KEYS = {_OLD_KEY.stream_id: _OLD_KEY, _NEW_KEY.stream_id: _NEW_KEY}


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


def _component(instance_id: str, bind_ports: tuple[str, ...], options: dict[str, Any]):
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": list(bind_ports),
            "options": options,
        },
    }


def _market_service_config(rpc_bind: str, market_bind: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _MARKET_SERVICE_ID,
        "scope_ref": _MARKET_SERVICE_SCOPE_REF,
        "transport_ref": "dim08-market-listener-v1",
        "registration_policy_ref": "dim08-market-registration-v1",
        "service_requirements_ref": "dim08-market-requirements-v1",
        "components": {
            "gateway": _component(
                _GATEWAY_INSTANCE,
                ("service.market_source",),
                {"role": "market_hub"},
            ),
            "monitor": _component(
                _INGRESS_INSTANCE,
                ("service.telemetry_ingress",),
                {
                    "role": "durable_ingress",
                    "max_events": 32,
                    "max_bytes": 65_536,
                    "max_event_bytes": 16_384,
                },
            ),
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "dim08-market-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": rpc_bind, "market_bind": market_bind},
                    "security_profile_ref": _MARKET_SECURITY_REF,
                    "options": {
                        "high_water_mark": 64,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _relay_runtime_config(rpc_endpoint: str, market_endpoint: str) -> dict[str, Any]:
    """A normal parsed service config with two exact upstream resource mounts."""

    def binding(mount: str, instance_id: str, scope_ref: str, transport_ref: str):
        return {
            "mount": mount,
            "service_id": _MARKET_SERVICE_ID,
            "instance_id": instance_id,
            "scope_ref": scope_ref,
            "transport_ref": transport_ref,
        }

    common_options = {
        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
        "heartbeat_interval_seconds": 5,
    }
    profile_options = {
        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
        "high_water_mark": 64,
        "max_message_bytes": _MAX_MESSAGE_BYTES,
    }
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "dim08-relay-client",
        "scope_ref": "dim08-relay-host-v1",
        "transport_ref": "dim08-relay-listener-v1",
        "registration_policy_ref": "dim08-relay-registration-v1",
        "service_requirements_ref": "dim08-relay-requirements-v1",
        "components": {
            "monitor": _component(
                "dim08-relay-local-monitor",
                ("service.monitor_query",),
                {},
            )
        },
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        binding(
                            "upstream.market",
                            _GATEWAY_INSTANCE,
                            _GATEWAY_SCOPE_REF,
                            "dim08-market-client-v1",
                        )
                    ],
                    "options": dict(common_options),
                },
            },
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        binding(
                            "upstream.telemetry",
                            _INGRESS_INSTANCE,
                            _INGRESS_SCOPE_REF,
                            "dim08-monitor-client-v1",
                        )
                    ],
                    "options": dict(common_options),
                },
            },
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "dim08-relay-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:9"},
                    "security_profile_ref": _MARKET_SECURITY_REF,
                    "options": {
                        "high_water_mark": 64,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                },
                "dim08-market-client-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {
                        "rpc_connect": rpc_endpoint,
                        "market_connect": market_endpoint,
                    },
                    "security_profile_ref": _MARKET_SECURITY_REF,
                    "options": dict(profile_options),
                },
                "dim08-monitor-client-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": rpc_endpoint},
                    "security_profile_ref": _MARKET_SECURITY_REF,
                    "options": dict(profile_options),
                },
            },
        },
    }


def _account_observation(
    account_ref: str,
    sequence: int,
    account_key: AccountKey,
    instrument: MarketInstrumentKey,
) -> AccountObservation:
    """Synthetic account source keeps the old contract on every refresh."""

    observed_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    balance = AccountBalance(
        resource=_RESOURCE,
        currency="USD",
        total="250.00",
        available="200.00",
        locked="50.00",
        observed_at_utc=observed_at,
    )
    position = AccountPosition(
        resource=_RESOURCE,
        instrument=instrument,
        side="long",
        quantity="2",
        qty_unit="contract",
        average_price="100.25",
        price_currency="USD",
        observed_at_utc=observed_at,
    )
    return AccountObservation(
        account_key=account_key,
        observation_id=f"dim08-{account_ref}-snapshot-{sequence}",
        coverage=(_RESOURCE,),
        balances=(balance,),
        positions=(position,),
        valuation=AccountValuation(
            currency="USD",
            available_cash="200.00",
            equity="250.00",
            observed_at_utc=observed_at,
        ),
        started_at_utc=observed_at,
        completed_at_utc=observed_at,
        consistency="provider_atomic",
        balances_complete=True,
        positions_complete=True,
    )


def _account_host_process_with_old_position(control, *args) -> None:
    """Reuse the accepted real AccountSessionHost fixture with a future resource."""

    account_fixture._RESOURCE = _RESOURCE
    account_fixture._REQUIRED_COVERAGE = (_RESOURCE,)
    account_fixture._observation = _account_observation
    account_fixture._service_host_process(control, *args)


class _RollProvider:
    """Two exact synthetic contract streams owned by the local Gateway hub."""

    def __init__(self) -> None:
        self.hub: GatewayMarketHub | None = None
        self.active: dict[MarketSubscriptionKey, tuple[str, str]] = {}
        self.starts = 0
        self.stops = 0

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        if key not in _KEYS.values() or not generation or key in self.active:
            raise AssertionError("unexpected or duplicate synthetic contract subscription")
        self.starts += 1
        handle = (key.stream_id, generation)
        self.active[key] = handle
        return handle

    def unsubscribe(self, handle: object) -> bool:
        if type(handle) is not tuple or len(handle) != 2:
            raise AssertionError("Gateway released an unknown synthetic handle")
        key = next((item for item in self.active if item.stream_id == handle[0]), None)
        if key is None or self.active[key] != handle:
            raise AssertionError("Gateway released a stale synthetic generation")
        del self.active[key]
        self.stops += 1
        return True

    def poll(self, handle: object, *, limit: int) -> tuple[MarketProviderEvent, ...]:
        """Implement the provider contract without inventing quote events."""
        if (
            type(handle) is not tuple
            or handle not in self.active.values()
            or type(limit) is not int
            or limit != 1
        ):
            raise AssertionError("Gateway polled an unknown handle or invalid bound")
        return ()

    def publish(self, key: MarketSubscriptionKey, data: Mapping[str, Any]) -> int:
        if self.hub is None or key not in self.active:
            raise AssertionError("synthetic quote has no active contract subscription")
        handle = self.active[key]
        sent_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        frame = self.hub.publish(key, dict(data), sent_at, generation=handle[1])
        return frame.header.sequence


def _market_host_process(
    control,
    raw_config,
    registry_path: str,
    outbox_path: str,
    server_keys: tuple[bytes, bytes],
    relay_public_key: bytes,
) -> None:
    host: ServiceHost | None = None
    provider = _RollProvider()
    hub: GatewayMarketHub | None = None
    try:
        config = parse_service_config(raw_config)
        hub = GatewayMarketHub(
            provider,
            key_universe=frozenset({_OLD_KEY, _NEW_KEY}),
            principal_allowed_keys={_RELAY_PRINCIPAL: frozenset({_OLD_KEY, _NEW_KEY})},
            service_identity=_MARKET_SERVICE_IDENTITY,
            service_scope=_SYSTEM_SCOPE,
        )
        provider.hub = hub
        outbox = DurableOutbox(outbox_path)
        ingress_targets = {
            _SYSTEM_SCOPE: MonitorIngressTarget(
                _OUTBOX_SCOPE, (GATEWAY_MARKET_OBSERVATION_EVENT_TYPE,)
            )
        }
        market_grant = ResourceGrant(
            _GATEWAY_INSTANCE,
            "service.market_source",
            _GATEWAY_SCOPE_REF,
            _SYSTEM_SCOPE,
            _MARKET_OPERATIONS,
        )
        ingress_grant = ResourceGrant(
            _INGRESS_INSTANCE,
            "service.telemetry_ingress",
            _INGRESS_SCOPE_REF,
            _SYSTEM_SCOPE,
            frozenset({"events.append"}),
        )

        def resources(component, _instance, _options, _scope):
            if component == "gateway":
                return ComponentResources.owned(
                    {"hub": hub}, ready=lambda: hub.ready, close=hub.close
                )
            if component == "monitor":
                return ComponentResources.borrowed(
                    {"outbox": outbox, "targets": ingress_targets}, ready=lambda: True
                )
            raise AssertionError("unexpected DIM08 service component")

        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=_MARKET_SERVICE_IDENTITY,
                scope=_SYSTEM_SCOPE,
                scope_ref=_MARKET_SERVICE_SCOPE_REF,
                scope_version="dim08-market-scope-v1",
                security_ref=_MARKET_SECURITY_REF,
                security_version="curve-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="dim08-market-registration-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="dim08-market-requirements-v1",
                curve_public_key=server_keys[0],
                curve_secret_key=server_keys[1],
                principals={relay_public_key: _RELAY_PRINCIPAL},
                grants={
                    _RELAY_PRINCIPAL: (
                        PrincipalGrant(_RELAY_IDENTITY, (market_grant, ingress_grant)),
                    )
                },
            ),
            registry_path,
            resources,
        )
        host.start()
        control.send(("ready", host.endpoint, host.market_endpoint))
        while True:
            host.serve_once(timeout_ms=10)
            if not control.poll():
                continue
            command = control.recv()
            if command[0] == "status":
                outbox_rows, outbox_has_more = outbox.read_page(
                    _OUTBOX_SCOPE,
                    GATEWAY_MARKET_OBSERVATION_EVENT_TYPE,
                    after_sequence=0,
                    limit=32,
                )
                control.send(
                    (
                        "status",
                        {
                            "readiness": dict(hub.readiness),
                            "provider_starts": provider.starts,
                            "provider_stops": provider.stops,
                            "active_stream_ids": tuple(
                                sorted(key.stream_id for key in provider.active)
                            ),
                            "outbox_sequences": tuple(row.sequence for row in outbox_rows),
                            "outbox_has_more": outbox_has_more,
                        },
                    )
                )
            elif command[0] == "publish":
                key = _KEYS[command[1]]
                sequence = provider.publish(
                    key,
                    {
                        "bid": command[2],
                        "ask": command[3],
                        "synthetic_marker": command[4],
                    },
                )
                control.send(("published", key.stream_id, sequence, command[4]))
            elif command[0] == "close":
                host.close()
                control.send(
                    (
                        "closed",
                        host.state,
                        provider.starts,
                        provider.stops,
                        tuple(sorted(key.stream_id for key in provider.active)),
                    )
                )
                return
            else:
                raise AssertionError("unsupported DIM08 market-host command")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _runtime(config_raw, curve_keys, *, account_scoped: bool = False):
    config = parse_service_config(config_raw)
    market_scope = (
        WireScope(_DOMAIN, account_ref="dim08-forbidden-account")
        if account_scoped
        else _SYSTEM_SCOPE
    )
    return ServiceClientRuntime(
        config,
        identity=_RELAY_IDENTITY,
        scope_resolver=lambda _scope_ref: market_scope,
        peer_resolver=lambda _service_id: _MARKET_SERVICE_IDENTITY,
        transport_factory=ZmqTransportFactory({_MARKET_SECURITY_REF: curve_keys}),
        requested_operations={
            "upstream.market": _MARKET_OPERATIONS,
            "upstream.telemetry": frozenset({"events.append"}),
        },
    )


def _market_client(key, runtime, curve_keys) -> ServiceMarketClient:
    return ServiceMarketClient(
        runtime,
        "upstream.market",
        key,
        subscriber_factory=ZmqMarketTransportFactory(
            {_MARKET_SECURITY_REF: curve_keys}
        ),
        stream_scope=_SYSTEM_SCOPE,
        head_check_interval_seconds=0.02,
    )


def _relay(market_client, runtime) -> ServiceMarketMonitorRelay:
    return ServiceMarketMonitorRelay(
        market_client,
        runtime,
        monitor_scope_ref=_INGRESS_SCOPE_REF,
        system_ref=_SYSTEM_REF,
        process_ref=_PROCESS_REF,
        max_pending_events=4,
        max_pending_bytes=16_384,
    )


def _market_command(record, *command):
    assert record["process"].is_alive(), "DIM08 market host exited before command"
    record["pipe"].send(command)
    result = _receive(record["pipe"])
    assert result[0] != "error", result
    return result


def _status(record):
    result = _market_command(record, "status")
    assert result[0] == "status", result
    return result[1]


def _pump_until(relay, timeout_seconds: float = 5.0):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        frame = relay.pump_once(timeout_ms=50)
        if frame is not None:
            return frame
    raise AssertionError("DIM08 market relay did not receive its synthetic quote")


def _close_market_host(record) -> tuple[Any, ...]:
    record["pipe"].send(("close",))
    result = _receive(record["pipe"], timeout=10.0)
    assert result[0] == "closed" and result[1] == "closed", result
    process = record["process"]
    process.join(10)
    assert not process.is_alive(), "DIM08 market host did not exit normally"
    assert process.exitcode == 0, f"DIM08 market host exit={process.exitcode}"
    record["pipe"].close()
    process.close()
    record["closed"] = True
    return result


def _attach_cleanup_failure(primary_error, failures: list[str]) -> None:
    if not failures:
        return
    if primary_error is not None:
        with suppress(Exception):
            setattr(primary_error, "dim08_cleanup_failures", tuple(failures))
        return
    raise AssertionError(f"DIM08 spawned service cleanup failed: {tuple(failures)}")


def test_expiry_roll_keeps_account_position_on_old_key_and_replays_monitor_outbox(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    market_rpc, market_pub, account_endpoint = _free_endpoints(3)
    market_server_keys = zmq.curve_keypair()
    market_client_public, market_client_secret = zmq.curve_keypair()
    market_curve_keys = ClientCurveKeys(
        market_server_keys[0], market_client_public, market_client_secret
    )
    raw_market_config = _market_service_config(market_rpc, market_pub)
    raw_relay_config = _relay_runtime_config(market_rpc, market_pub)
    market_outbox_path = tmp_path / "dim08-market-monitor.sqlite"

    market_host = None
    account_host = None
    runtime = None
    old_client = None
    new_client = None
    account_runtime = None
    account_client = None
    forced_cleanup: list[str] = []
    cleanup_failures: list[str] = []
    account_cleanup_failures: list[str] = []

    account_service = account_fixture._account_service_info(
        "account-a", account_endpoint, "curve-account-a-v1"
    )
    account_server_keys = zmq.curve_keypair()
    account_service["server_key"] = account_server_keys[0]
    account_client_keys = zmq.curve_keypair()
    account_audience = (
        (
            account_client_keys[0],
            "dim08-account-reader",
            "dim08-account-strategy",
            True,
        ),
    )
    account_services = {"account-a": account_service}
    account_client_config = account_fixture._client_config(
        "dim08-account-strategy", account_services, ("account-a",)
    )

    try:
        (tmp_path / "dim08-account-guard").mkdir()
        account_host = account_fixture._start_process(
            context,
            _account_host_process_with_old_position,
            (
                "account-a",
                _ACCOUNT_KEY,
                _OLD_INSTRUMENT,
                account_fixture._service_config(account_service),
                str(tmp_path / "dim08-account-registry.sqlite"),
                str(tmp_path / "dim08-account-journal.sqlite"),
                str(tmp_path / "dim08-account-guard"),
                account_server_keys,
                account_audience,
            ),
            "dim08-account-service",
        )
        account_ready = account_fixture._receive(account_host["pipe"])
        assert account_ready[0:2] == ("ready", "account-a"), account_ready

        account_identity = WireIdentity(
            WireRole.STRATEGY_CLIENT,
            "dim08-account-reader",
            "dim08-account-strategy",
        )
        account_runtime = account_fixture._direct_runtime(
            account_client_config,
            account_identity,
            account_service,
            (account_client_keys[0], account_client_keys[1]),
        )
        account_registration = account_runtime.start()
        assert account_registration.degraded_count == 0
        account_client = ServiceAccountClient(
            account_runtime,
            "store.account",
            account_ref="account-a",
            expected_account=_ACCOUNT_KEY,
            required_coverage=(_RESOURCE,),
        )
        initial_account_view = account_client.refresh()
        assert initial_account_view.observation.positions_complete is True
        assert len(initial_account_view.observation.positions) == 1
        assert initial_account_view.observation.positions[0].instrument == _OLD_INSTRUMENT

        market_host = _start_process(
            context,
            _market_host_process,
            (
                raw_market_config,
                str(tmp_path / "dim08-market-registry.sqlite"),
                str(market_outbox_path),
                market_server_keys,
                market_client_public,
            ),
            "dim08-market-monitor-host",
        )
        market_ready = _receive(market_host["pipe"])
        assert market_ready[0] == "ready", market_ready
        assert market_ready[1:3] == (market_rpc, market_pub), market_ready

        # The registry grants only the system-scoped public-market and
        # Monitor-ingress bindings. An account-scoped registration is denied
        # before either synthetic provider or outbox receives a call.
        denied_runtime = _runtime(raw_relay_config, market_curve_keys, account_scoped=True)
        try:
            with pytest.raises(ServiceRuntimeError) as denied:
                denied_runtime.start()
            assert denied.value.code == "SCOPE_DENIED"
        finally:
            denied_runtime.close()
        assert denied_runtime.cleanup_failures == 0
        assert denied_runtime.unregister_uncertainties == 0
        assert denied_runtime.state == "closed"
        preauthorized_state = _status(market_host)
        assert preauthorized_state["provider_starts"] == 0
        assert preauthorized_state["provider_stops"] == 0
        assert preauthorized_state["active_stream_ids"] == ()
        assert preauthorized_state["outbox_sequences"] == ()
        assert preauthorized_state["outbox_has_more"] is False

        runtime = _runtime(raw_relay_config, market_curve_keys)
        registration = runtime.start()
        assert registration.degraded_count == 0
        bindings_by_mount = {binding.mount: binding for binding in runtime.bindings}
        assert set(bindings_by_mount) == {
            "upstream.market",
            "upstream.telemetry",
        }
        assert all(binding.scope == _SYSTEM_SCOPE for binding in bindings_by_mount.values())
        assert bindings_by_mount["upstream.market"].operations == _MARKET_OPERATIONS
        assert bindings_by_mount["upstream.telemetry"].operations == frozenset(
            {"events.append"}
        )

        old_client = _market_client(_OLD_KEY, runtime, market_curve_keys)
        old_relay = _relay(old_client, runtime)
        old_bootstrap = old_relay.start()
        assert old_bootstrap.snapshot_kind == "latest_event"
        time.sleep(0.4)  # bounded local CURVE/SUB join, matching the existing integration path

        old_quote = {"bid": "100.00", "ask": "100.25", "synthetic_marker": "old-contract"}
        old_published = _market_command(
            market_host,
            "publish",
            _OLD_KEY.stream_id,
            old_quote["bid"],
            old_quote["ask"],
            old_quote["synthetic_marker"],
        )
        assert old_published == ("published", _OLD_KEY.stream_id, 1, "old-contract")
        old_frame = _pump_until(old_relay)
        assert MarketSubscriptionKey.from_dict(old_frame.payload["key"]) == _OLD_KEY
        assert dict(old_frame.payload["data"]) == old_quote
        old_relay_status = old_relay.status
        assert old_relay_status.received_frames == 1
        assert old_relay_status.appended_observations == 1
        assert old_relay_status.lost_observations == 0
        assert old_relay_status.pending_events == 0

        # The normal relay already appended this exact projected event. A
        # second identical accepted ingress call is an idempotent replay, not
        # a second durable observation or threshold count.
        projector = GatewaySystemTelemetryProjector(
            _MARKET_SERVICE_IDENTITY,
            _SYSTEM_SCOPE,
            system_ref=_SYSTEM_REF,
            process_ref=_PROCESS_REF,
            event_type=GATEWAY_MARKET_OBSERVATION_EVENT_TYPE,
            observation_schema="bt_api.monitor.market_observation.v1",
        )
        old_projection = projector.project(old_frame)
        repeated_receipt = runtime.call(
            "upstream.telemetry",
            "events.append",
            old_projection,
            scope_ref=_INGRESS_SCOPE_REF,
        )
        assert repeated_receipt["source_event_id"] == old_projection["event_id"]
        assert type(repeated_receipt["sequence"]) is int and repeated_receipt["sequence"] == 1

        # Subscribe the next expiry key before releasing the old one. Gateway
        # reference counts are exact-key based; the roll cannot rename a stream.
        new_client = _market_client(_NEW_KEY, runtime, market_curve_keys)
        new_relay = _relay(new_client, runtime)
        new_bootstrap = new_relay.start()
        assert new_bootstrap.snapshot_kind == "latest_event"
        overlap_state = _status(market_host)
        assert overlap_state["provider_starts"] == 2
        assert overlap_state["provider_stops"] == 0
        assert set(overlap_state["active_stream_ids"]) == {
            _OLD_KEY.stream_id,
            _NEW_KEY.stream_id,
        }
        time.sleep(0.4)

        new_quote = {"bid": "101.00", "ask": "101.25", "synthetic_marker": "new-contract"}
        new_published = _market_command(
            market_host,
            "publish",
            _NEW_KEY.stream_id,
            new_quote["bid"],
            new_quote["ask"],
            new_quote["synthetic_marker"],
        )
        assert new_published == ("published", _NEW_KEY.stream_id, 1, "new-contract")
        new_frame = _pump_until(new_relay)
        assert MarketSubscriptionKey.from_dict(new_frame.payload["key"]) == _NEW_KEY
        assert dict(new_frame.payload["data"]) == new_quote
        assert new_relay.status.appended_observations == 1
        new_projection = projector.project(new_frame)
        assert new_projection["data"]["market"]["instrument"] == _NEW_INSTRUMENT.to_dict()
        assert set(new_projection["data"]) == {
            "schema",
            "scope_type",
            "system_ref",
            "process_ref",
            "observation_kind",
            "source",
            "market",
        }

        old_client.close()
        old_client = None
        post_roll_state = _status(market_host)
        assert post_roll_state["provider_starts"] == 2
        assert post_roll_state["provider_stops"] == 1
        assert post_roll_state["active_stream_ids"] == (_NEW_KEY.stream_id,)

        # A new authoritative account snapshot after the quote roll retains
        # the exact old MarketInstrumentKey; latest market symbol is not an
        # account-position migration rule.
        refreshed = account_fixture._host_command(account_host, "refresh")
        assert refreshed[0:2] == ("refreshed", "account-a"), refreshed
        rolled_account_view = account_client.refresh()
        assert rolled_account_view.cursor > initial_account_view.cursor
        assert rolled_account_view.observation.positions_complete is True
        assert len(rolled_account_view.observation.positions) == 1
        held_position = rolled_account_view.observation.positions[0]
        assert type(held_position.instrument) is MarketInstrumentKey
        assert held_position.instrument == _OLD_INSTRUMENT
        assert held_position.instrument != _NEW_INSTRUMENT
        assert held_position.quantity == "2"

        new_client.close()
        new_client = None
        runtime.close()
        assert runtime.cleanup_failures == 0
        assert runtime.unregister_uncertainties == 0
        assert runtime.state == "closed"
        runtime = None

        final_market_state = _status(market_host)
        assert final_market_state["provider_starts"] == 2
        assert final_market_state["provider_stops"] == 2
        assert final_market_state["active_stream_ids"] == ()
        assert final_market_state["outbox_sequences"] == (1, 2)
        assert final_market_state["outbox_has_more"] is False
        closed_market = _close_market_host(market_host)
        assert closed_market[2:5] == (2, 2, ())

        account_client.close()
        account_client = None
        account_runtime.close()
        assert account_runtime.cleanup_failures == 0
        assert account_runtime.unregister_uncertainties == 0
        account_runtime = None
        closed_account = account_fixture._close_normally(account_host, expected_tag="closed")
        assert closed_account[1] == "account-a"
        assert closed_account[2]["host_state"] == "closed"
        assert closed_account[2]["close_calls"] == 1

        # The durable rows are consumed only after the producer Host has
        # exited. The old duplicate request shared its original sequence.
        outbox = DurableOutbox(market_outbox_path)
        pending = outbox.read_pending("dim08-inspector", _OUTBOX_SCOPE, limit=10)
        assert [item.sequence for item in pending] == [1, 2]
        assert len(pending) == 2
        assert len({item.event.event_id for item in pending}) == 2
        old_row, new_row = pending
        assert old_row.event.data["source_event_id"] == old_projection["event_id"]
        assert new_row.event.data["source_event_id"] == new_projection["event_id"]
        assert old_row.event.data["payload"]["market"]["instrument"] == _OLD_INSTRUMENT.to_dict()
        assert new_row.event.data["payload"]["market"]["instrument"] == _NEW_INSTRUMENT.to_dict()
        assert old_row.event.scope == new_row.event.scope == _OUTBOX_SCOPE
        assert old_row.event.event_type == new_row.event.event_type == (
            GATEWAY_MARKET_OBSERVATION_EVENT_TYPE
        )

        # Existing bounded rules use exact scope/event-type dimensions and a
        # fixed window. Restarting the loop keeps its durable cursor, while a
        # wrong scope or event type cannot count the two market observations.
        rules = (
            ThresholdRule(
                rule_id="dim08.market-roll-window",
                outbox_scope=_OUTBOX_SCOPE,
                event_type=GATEWAY_MARKET_OBSERVATION_EVENT_TYPE,
                window_seconds=60.0,
                threshold_count=2,
            ),
            ThresholdRule(
                rule_id="dim08.other-scope",
                outbox_scope="dim08:other-scope",
                event_type=GATEWAY_MARKET_OBSERVATION_EVENT_TYPE,
                window_seconds=60.0,
                threshold_count=1,
            ),
            ThresholdRule(
                rule_id="dim08.other-event-type",
                outbox_scope=_OUTBOX_SCOPE,
                event_type="strategy.threshold_observation",
                window_seconds=60.0,
                threshold_count=1,
            ),
        )
        emitted = []
        loop = ScopedThresholdRuleLoop(
            outbox,
            rules,
            emitted.append,
            consumer_id="dim08.market-roll-rules.v1",
            clock=lambda: 100.0,
        )
        assert loop.configured_scopes == (_OUTBOX_SCOPE, "dim08:other-scope")
        alerts = loop.run_once(limit=10)
        assert len(alerts) == 1 and alerts[0] is emitted[0]
        assert alerts[0].rule_id == "dim08.market-roll-window"
        assert alerts[0].event_type == GATEWAY_MARKET_OBSERVATION_EVENT_TYPE
        assert alerts[0].outbox_scope == _OUTBOX_SCOPE
        assert alerts[0].observed_count == 2
        assert alerts[0].triggering_sequence == 2
        assert alerts[0].triggering_event_id == new_row.event.event_id
        assert alerts[0].source_event_id == new_projection["event_id"]
        assert alerts[0].source_stream_epoch == new_frame.header.stream_epoch
        assert alerts[0].source_sequence == new_frame.header.sequence == 1
        assert alerts[0].source_completeness == "received_event_only"
        assert loop.rule_status("dim08.other-scope").counted_events == 0
        assert loop.rule_status("dim08.other-event-type").counted_events == 0
        assert loop.checkpoint(_OUTBOX_SCOPE) == 2
        assert loop.checkpoint("dim08:other-scope") == 0

        restarted_outbox = DurableOutbox(market_outbox_path)
        restarted_alerts = []
        restarted = ScopedThresholdRuleLoop(
            restarted_outbox,
            rules,
            restarted_alerts.append,
            consumer_id="dim08.market-roll-rules.v1",
            clock=lambda: 101.0,
        )
        assert restarted.run_once(limit=10) == ()
        assert restarted_alerts == []
        assert restarted.checkpoint(_OUTBOX_SCOPE) == 2
        assert restarted.checkpoint("dim08:other-scope") == 0
    finally:
        primary_error = sys.exc_info()[1]
        if old_client is not None:
            try:
                old_client.close()
            except Exception as exc:
                cleanup_failures.append(f"old_market_client:{type(exc).__name__}")
        if new_client is not None:
            try:
                new_client.close()
            except Exception as exc:
                cleanup_failures.append(f"new_market_client:{type(exc).__name__}")
        if runtime is not None:
            try:
                runtime.close()
                if runtime.cleanup_failures:
                    cleanup_failures.append(
                        f"market_runtime:cleanup_failures:{runtime.cleanup_failures}"
                    )
                if runtime.unregister_uncertainties:
                    cleanup_failures.append("market_runtime:unregister_uncertain")
            except Exception as exc:
                cleanup_failures.append(f"market_runtime:{type(exc).__name__}")
        if account_client is not None:
            try:
                account_client.close()
            except Exception as exc:
                account_cleanup_failures.append(f"account_client:{type(exc).__name__}")
        if account_runtime is not None:
            try:
                account_runtime.close()
                if account_runtime.cleanup_failures:
                    account_cleanup_failures.append(
                        f"account_runtime:cleanup_failures:{account_runtime.cleanup_failures}"
                    )
                if account_runtime.unregister_uncertainties:
                    account_cleanup_failures.append("account_runtime:unregister_uncertain")
            except Exception as exc:
                account_cleanup_failures.append(f"account_runtime:{type(exc).__name__}")
        for record, label in (
            (market_host, "market-host"),
            (account_host, "account-host"),
        ):
            if record is None or record.get("closed"):
                continue
            try:
                if label == "market-host":
                    _close_market_host(record)
                else:
                    _close_normally(record, expected_tag="closed")
            except Exception as exc:
                cleanup_failures.append(f"{label}:normal_close:{type(exc).__name__}")
            if not record.get("closed"):
                try:
                    if not record["process"].is_alive():
                        exitcode = record["process"].exitcode
                        if exitcode != 0:
                            cleanup_failures.append(f"{label}:exit:{exitcode}")
                except Exception as exc:
                    cleanup_failures.append(f"{label}:exit_status:{type(exc).__name__}")
            if label == "account-host":
                _bounded_reap_spawned_process(record, forced_cleanup, account_cleanup_failures)
            else:
                _bounded_reap_spawned_process(record, forced_cleanup, cleanup_failures)
        cleanup_failures.extend(
            f"forced_cleanup:{label}" for label in forced_cleanup
        )
        _attach_cleanup_failure(primary_error, cleanup_failures + account_cleanup_failures)

