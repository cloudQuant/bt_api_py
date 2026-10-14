"""Ten spawned strategy clients share one CURVE-authenticated market source."""

from __future__ import annotations

import multiprocessing
import os
import socket
import time
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import zmq
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.market_hub import GatewayMarketHub
from bt_api_transport_zmq.market_v2 import ZmqMarketSubscriber
from bt_api_transport_zmq.server import CurveClientCredentials

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_market import ServiceMarketClient, ZmqMarketTransportFactory
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceCallOutcomeUnknownError,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_SERVICE_ID = "synthetic-market-service"
_SERVICE_INSTANCE = "synthetic-market-hub"
_SERVICE_SCOPE_REF = "synthetic-market-public-scope-v1"
_DOMAIN_REF = "paper:market-integration"
_TRANSPORT_REF = "market-listener-v1"
_SECURITY_REF = "market-curve-v1"
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
_HEARTBEAT_SECONDS = 5
_CLIENT_COUNT = 10
_MARKET_KEY = MarketSubscriptionKey(
    source_ref="synthetic-market-source",
    entitlement_ref="synthetic-public-entitlement",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="loopback",
        environment="paper",
        product_type="spot",
        instrument_id="synthetic-instrument-1",
        native_symbol="SYNTH-USD",
    ),
    channel="trades",
)


def _free_endpoints(count: int) -> tuple[str, ...]:
    sockets: list[socket.socket] = []
    try:
        endpoints = []
        for _ in range(count):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            sockets.append(probe)
            endpoints.append(f"tcp://127.0.0.1:{probe.getsockname()[1]}")
        return tuple(endpoints)
    finally:
        for probe in sockets:
            probe.close()


def _service_config(rpc_bind: str, market_bind: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": _TRANSPORT_REF,
        "registration_policy_ref": "synthetic-market-registration-v1",
        "service_requirements_ref": "synthetic-market-requirements-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _SERVICE_INSTANCE,
                    "bind_ports": ["service.market_source"],
                    "options": {"role": "market_hub"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": rpc_bind, "market_bind": market_bind},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "high_water_mark": 128,
                        "max_message_bytes": 262_144,
                    },
                }
            },
        },
    }


def _client_config(
    rpc_connect: str,
    market_connect: str,
    strategy_ref: str,
) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": strategy_ref},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "store.market",
                            "service_id": _SERVICE_ID,
                            "instance_id": _SERVICE_INSTANCE,
                            "scope_ref": _SERVICE_SCOPE_REF,
                            "transport_ref": "market-client-v1",
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
                "market-client-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {
                        "rpc_connect": rpc_connect,
                        "market_connect": market_connect,
                    },
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "high_water_mark": 128,
                        "max_message_bytes": 262_144,
                    },
                }
            },
        },
    }


class _CountedSyntheticProvider:
    """A deterministic source whose acquire/release calls are externally counted."""

    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0
        self.hub: GatewayMarketHub | None = None
        self.generation: str | None = None

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        if key != _MARKET_KEY:
            raise AssertionError("only the declared synthetic key is supported")
        if not generation:
            raise AssertionError("the hub must provide a nonempty source generation")
        self.starts += 1
        self.generation = generation
        return ("synthetic-handle", self.starts, key.stream_id, generation)

    def unsubscribe(self, handle: object) -> bool:
        if (
            not isinstance(handle, tuple)
            or handle[0] != "synthetic-handle"
            or handle[3] != self.generation
        ):
            raise AssertionError("provider received an unknown synthetic handle")
        self.stops += 1
        self.generation = None
        return True

    def publish(self, marker: str) -> int:
        if self.hub is None or self.generation is None:
            raise AssertionError("synthetic hub is not attached")
        sent_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        event = self.hub.publish(
            _MARKET_KEY,
            {"marker": marker, "source": "counted-synthetic-provider"},
            sent_at,
            generation=self.generation,
        )
        return event.header.sequence


def _service_host_process(control, raw_config, registry_path, server_keys, client_audience):
    config = parse_service_config(raw_config)
    service_identity = WireIdentity(WireRole.SERVICE, "synthetic-market-service", _SERVICE_ID)
    service_scope = WireScope(_DOMAIN_REF)
    principals = {public_key: principal_ref for public_key, principal_ref, _ in client_audience}
    grants = {
        principal_ref: (
            PrincipalGrant(
                WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref),
                (
                    ResourceGrant(
                        _SERVICE_INSTANCE,
                        "service.market_source",
                        _SERVICE_SCOPE_REF,
                        WireScope(_DOMAIN_REF, strategy_ref=strategy_ref),
                        _MARKET_OPERATIONS,
                    ),
                ),
            ),
        )
        for _public_key, principal_ref, strategy_ref in client_audience
    }
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=service_scope,
        scope_ref=config.scope_ref,
        scope_version="synthetic-market-scope-v1",
        security_ref=_SECURITY_REF,
        security_version="curve-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="requirements-v1",
        curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],
        principals=principals,
        grants=grants,
    )
    provider = _CountedSyntheticProvider()
    hub = GatewayMarketHub(
        provider,
        key_universe=frozenset({_MARKET_KEY}),
        principal_allowed_keys={
            principal_ref: frozenset({_MARKET_KEY})
            for _public_key, principal_ref, _strategy_ref in client_audience
        },
        service_identity=service_identity,
        service_scope=service_scope,
    )
    provider.hub = hub

    def resources(component, _instance_id, _options, _scope):
        if component != "gateway":
            raise AssertionError("only the trusted market gateway component is expected")
        return ComponentResources.owned(
            {"hub": hub},
            ready=lambda: hub.ready,
            close=hub.close,
        )

    host = ServiceHost(config, authority, registry_path, resources)
    try:
        host.start()
        control.send(("ready", host.endpoint, host.market_endpoint))
        while True:
            host.serve_once(10)
            if not control.poll():
                continue
            command = control.recv()
            action = command[0]
            if action == "status":
                control.send(("status", dict(hub.readiness), provider.starts, provider.stops))
            elif action == "publish":
                sequence = provider.publish(command[1])
                control.send(("published", command[1], sequence))
            elif action == "reset-epoch":
                epoch = hub.reset_stream_epoch(_MARKET_KEY)
                control.send(("epoch-reset", epoch))
            elif action == "close":
                host.close()
                control.send(("closed", host.state, provider.starts, provider.stops))
                return
            else:
                raise AssertionError("unknown host command")
    except BaseException as exc:
        if host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _event_tuple(event) -> tuple[str, str, int, dict[str, Any], dict[str, Any]]:
    def plain(value):
        if isinstance(value, Mapping):
            return {key: plain(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return tuple(plain(item) for item in value)
        return value

    return (
        event.header.stream_id,
        event.header.stream_epoch,
        event.header.sequence,
        plain(event.payload["key"]),
        plain(event.payload["data"]),
    )


def _strategy_client_process(
    control,
    principal_ref,
    strategy_ref,
    raw_config,
    client_keys,
    service_identity,
):
    config = parse_client_config(raw_config)
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)

    def scope_resolver(_scope_ref: str) -> WireScope:
        return WireScope(_DOMAIN_REF, strategy_ref=strategy_ref)

    security = ClientCurveKeys(client_keys[0], client_keys[1], client_keys[2])
    runtime = ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=scope_resolver,
        peer_resolver=lambda _service_id: service_identity,
        transport_factory=ZmqTransportFactory({_SECURITY_REF: security}),
        requested_operations={"store.market": _MARKET_OPERATIONS},
    )
    market = None
    pending_events: list[tuple[str, str, int, dict[str, Any], dict[str, Any]]] = []
    pause_market = False
    try:
        runtime.start()
        market = ServiceMarketClient(
            runtime,
            "store.market",
            _MARKET_KEY,
            subscriber_factory=ZmqMarketTransportFactory({_SECURITY_REF: security}),
            stream_scope=WireScope(_DOMAIN_REF),
            head_check_interval_seconds=2.0,
        )
        bootstrap = market.start()
        registration_id = runtime.bindings[0].registration_id
        control.send(
            (
                "ready",
                registration_id,
                bootstrap.snapshot_kind,
                bootstrap.snapshot_complete,
                bootstrap.full_baseline_available,
                bootstrap.cursor_epoch,
                bootstrap.cursor_sequence,
            )
        )

        def service_once(timeout_ms: int) -> None:
            delay = runtime.next_maintenance_delay()
            if delay is not None and delay <= 0:
                runtime.maintain()
            if not pause_market:
                event = market.poll(timeout_ms=timeout_ms)
                if event is not None:
                    pending_events.append(_event_tuple(event))

        while True:
            service_once(0)
            if not control.poll(0.005):
                continue
            command = control.recv()
            action = command[0]
            if action == "collect":
                marker = command[1]
                deadline = time.monotonic() + command[2]
                while time.monotonic() < deadline and not any(
                    item[4].get("marker") == marker for item in pending_events
                ):
                    service_once(10)
                selected = [item for item in pending_events if item[4].get("marker") == marker]
                pending_events = [
                    item for item in pending_events if item[4].get("marker") != marker
                ]
                control.send(("events", marker, tuple(selected)))
            elif action == "drain":
                pending_events.clear()
                control.send(("drained", principal_ref))
            elif action == "pause-market":
                pause_market = command[1]
                control.send(("market-polling", pause_market))
            elif action == "recover-market":
                bootstrap = market.recover()
                control.send(
                    (
                        "market-recovered",
                        bootstrap.resync_required,
                        market.healthy,
                        bootstrap.snapshot_kind,
                        bootstrap.snapshot_complete,
                        bootstrap.full_baseline_available,
                        bootstrap.snapshot_epoch,
                        bootstrap.snapshot_sequence,
                        bootstrap.cursor_epoch,
                        bootstrap.cursor_sequence,
                    )
                )
            elif action == "reject-resync":
                try:
                    market.accept_resync(acknowledge_data_loss=False)
                except Exception as exc:
                    control.send(
                        ("resync-rejected", getattr(exc, "code", "UNKNOWN"), market.healthy)
                    )
                else:
                    control.send(("resync-accepted-unexpectedly",))
            elif action == "accept-resync":
                bootstrap = market.accept_resync(acknowledge_data_loss=True)
                control.send(
                    (
                        "resync-accepted",
                        bootstrap.resync_required,
                        market.healthy,
                        bootstrap.snapshot_kind,
                        bootstrap.snapshot_complete,
                        bootstrap.full_baseline_available,
                        bootstrap.snapshot_epoch,
                        bootstrap.snapshot_sequence,
                        bootstrap.cursor_epoch,
                        bootstrap.cursor_sequence,
                    )
                )
            elif action == "denied-key":
                denied_key = MarketSubscriptionKey(
                    source_ref="unauthorized-synthetic-source",
                    entitlement_ref="synthetic-public-entitlement",
                    instrument=_MARKET_KEY.instrument,
                    channel=_MARKET_KEY.channel,
                )
                try:
                    runtime.call(
                        "store.market",
                        "market.subscribe",
                        {"key": denied_key.to_dict()},
                    )
                except (ServiceCallOutcomeUnknownError, ServiceRuntimeError) as exc:
                    control.send(("key-denied", getattr(exc, "code", type(exc).__name__)))
                else:
                    control.send(("key-accepted",))
            elif action == "release":
                market.close()
                runtime.close()
                control.send(("closed", principal_ref, runtime.state, market.healthy))
                return
            elif action == "crash-without-unregister":
                control.send(("crashing", registration_id))
                # This test-created process exits without sending unsubscribe
                # or unregister; the service must rely on registry lease expiry.
                os._exit(23)
            else:
                raise AssertionError("unknown client command")
    except BaseException as exc:
        with suppress(Exception):
            control.send(("error", principal_ref, getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _start_process(context, target, args):
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=target, args=(child, *args))
    process.start()
    child.close()
    return process, parent


def _receive(pipe, timeout: float = 15.0):
    assert pipe.poll(timeout), "spawned market process did not answer within its bound"
    return pipe.recv()


def _host_command(pipe, *command):
    pipe.send(command)
    result = _receive(pipe)
    assert result[0] != "error", result
    return result


def _client_command(record, *command, timeout: float = 10.0):
    process, pipe = record["process"], record["pipe"]
    assert process.is_alive()
    pipe.send(command)
    result = _receive(pipe, timeout)
    assert result[0] != "error", result
    return result


def _join_normally(process, pipe, *, expected_exitcode: int = 0) -> None:
    process.join(15)
    assert not process.is_alive(), "spawned market process did not exit within its bound"
    assert process.exitcode == expected_exitcode
    pipe.close()
    process.close()


def _bounded_reap_spawned_process(process, pipe, label: str, forced, failures) -> None:
    """Reap only a process created by this test, recording forced fallback."""

    def alive() -> bool:
        try:
            return process.is_alive()
        except Exception as exc:
            failures.append(f"{label}:state:{type(exc).__name__}")
            return False

    try:
        process.join(5)
    except Exception as exc:
        failures.append(f"{label}:normal_join:{type(exc).__name__}")
    if alive():
        forced.append(label)
        try:
            process.terminate()
        except Exception as exc:
            failures.append(f"{label}:terminate:{type(exc).__name__}")
        try:
            process.join(5)
        except Exception as exc:
            failures.append(f"{label}:terminate_join:{type(exc).__name__}")
    if alive():
        try:
            process.kill()
        except Exception as exc:
            failures.append(f"{label}:kill:{type(exc).__name__}")
        try:
            process.join(5)
        except Exception as exc:
            failures.append(f"{label}:kill_join:{type(exc).__name__}")

    is_alive = alive()
    try:
        pipe.close()
    except Exception as exc:
        failures.append(f"{label}:pipe_close:{type(exc).__name__}")
    if is_alive:
        failures.append(f"{label}:still_alive_after_kill")
        return
    try:
        process.close()
    except Exception as exc:
        failures.append(f"{label}:process_close:{type(exc).__name__}")


def _assert_no_fallback_cleanup(forced, failures) -> None:
    if forced or failures:
        raise AssertionError(
            f"spawned integration cleanup required fallback: forced={tuple(forced)}, "
            f"failures={tuple(failures)}"
        )


def _host_status(pipe) -> tuple[Mapping[str, Any], int, int]:
    result = _host_command(pipe, "status")
    assert result[0] == "status", result
    return result[1], result[2], result[3]


def _publish_and_collect(host_pipe, clients, marker: str, expected_client_ids) -> dict[str, tuple]:
    published = _host_command(host_pipe, "publish", marker)
    assert published[0] == "published" and published[1] == marker, published
    collected = {}
    for client_id in expected_client_ids:
        result = _client_command(clients[client_id], "collect", marker, 3.0)
        assert result[0] == "events" and result[1] == marker, result
        matching = tuple(event for event in result[2] if event[4].get("marker") == marker)
        assert len(matching) == 1, (client_id, marker, matching)
        collected[client_id] = matching[0]
    values = tuple(collected.values())
    assert len({(event[0], event[1], event[2]) for event in values}) == 1
    assert all(event[2] == published[2] for event in values)
    assert values[0][0] == _MARKET_KEY.stream_id
    assert all(event[3] == _MARKET_KEY.to_dict() for event in values)
    assert all(event[4] == values[0][4] for event in values)
    return collected


def test_ten_spawned_clients_share_market_stream_and_expired_lease_releases_only_its_ref(
    tmp_path,
):
    context = multiprocessing.get_context("spawn")
    rpc_endpoint, market_endpoint = _free_endpoints(2)
    server_public, server_secret = zmq.curve_keypair()
    audience = []
    client_material = {}
    client_raw_configs = {}
    for index in range(_CLIENT_COUNT):
        principal_ref = f"synthetic-market-principal-{index}"
        strategy_ref = f"synthetic-market-strategy-{index}"
        public_key, secret_key = zmq.curve_keypair()
        audience.append((public_key, principal_ref, strategy_ref))
        client_material[index] = (server_public, public_key, secret_key)
        client_raw_configs[index] = _client_config(rpc_endpoint, market_endpoint, strategy_ref)

    raw_service_config = _service_config(rpc_endpoint, market_endpoint)
    registry_path = str(tmp_path / "market-registry.sqlite3")
    service_identity = WireIdentity(WireRole.SERVICE, "synthetic-market-service", _SERVICE_ID)
    service_process, service_pipe = _start_process(
        context,
        _service_host_process,
        (
            raw_service_config,
            registry_path,
            (server_public, server_secret),
            tuple(audience),
        ),
    )
    clients: dict[int, dict[str, Any]] = {}
    intruder = None
    intruder_secret = None
    host_closed = False
    service_process_closed = False
    forced_cleanup = []
    cleanup_failures = []
    try:
        service_ready = _receive(service_pipe)
        assert service_ready[0] == "ready" and service_ready[1] and service_ready[2], service_ready
        client_ready = {}
        for index, (_public, principal_ref, strategy_ref) in enumerate(audience):
            process, pipe = _start_process(
                context,
                _strategy_client_process,
                (
                    principal_ref,
                    strategy_ref,
                    client_raw_configs[index],
                    client_material[index],
                    service_identity,
                ),
            )
            clients[index] = {
                "process": process,
                "pipe": pipe,
                "closed": False,
                "process_closed": False,
            }
            ready = _receive(pipe)
            assert ready[0] == "ready" and ready[2:5] == (
                "latest_event",
                False,
                False,
            ), ready
            client_ready[index] = ready[1]
        assert len(set(client_ready.values())) == _CLIENT_COUNT

        readiness, starts, stops = _host_status(service_pipe)
        assert readiness["active_registrations"] == _CLIENT_COUNT, readiness
        assert readiness["active_upstreams"] == 1
        assert (starts, stops) == (1, 0)

        # The service handler receives the complete key only after strategy
        # registration and must reject an unauthorized stream before provider
        # acquisition. The caller reports the safe unknown-outcome boundary.
        denied = _client_command(clients[0], "denied-key")
        assert denied[0] == "key-denied", denied
        readiness, starts, stops = _host_status(service_pipe)
        assert readiness["active_upstreams"] == 1 and (starts, stops) == (1, 0)

        # This raw CURVE subscriber is deliberately absent from the publisher's
        # authenticated audience map. It receives no event while all ten
        # authorized market facades share the same full-key subscription.
        intruder_public, intruder_secret = zmq.curve_keypair()
        intruder = ZmqMarketSubscriber(
            service_ready[2],
            service_identity,
            WireScope(_DOMAIN_REF),
            credentials=CurveClientCredentials(server_public, intruder_public, intruder_secret),
            high_water_mark=128,
            max_message_bytes=262_144,
        )

        # A bounded warm-up gives all local SUB sockets time to complete their
        # CURVE/ZAP slow join. Discard warm-up observations before comparing a
        # single common event sequence.
        time.sleep(0.4)
        for sequence in range(3):
            _host_command(service_pipe, "publish", f"warmup-{sequence}")
            time.sleep(0.05)
        for client_id in clients:
            drained = _client_command(clients[client_id], "drain")
            assert drained[0] == "drained"

        readiness, starts, stops = _host_status(service_pipe)
        assert readiness["active_upstreams"] == 1
        assert readiness["active_registrations"] == _CLIENT_COUNT
        assert (starts, stops) == (1, 0)

        first = _publish_and_collect(service_pipe, clients, "common-event-1", tuple(clients))
        assert len({event[2] for event in first.values()}) == 1
        assert intruder.receive(timeout_ms=150) is None

        closed = _client_command(clients[0], "release")
        assert closed == ("closed", audience[0][1], "closed", False)
        clients[0]["closed"] = True
        _join_normally(clients[0]["process"], clients[0]["pipe"])
        clients[0]["process_closed"] = True
        readiness, starts, stops = _host_status(service_pipe)
        assert readiness["active_registrations"] == _CLIENT_COUNT - 1
        assert readiness["active_upstreams"] == 1
        assert (starts, stops) == (1, 0)

        survivors_after_release = tuple(range(1, _CLIENT_COUNT))
        second = _publish_and_collect(
            service_pipe, clients, "common-event-2", survivors_after_release
        )
        assert len({event[2] for event in second.values()}) == 1

        # One process exits abruptly without market unsubscribe or registry
        # unregister. Its lease must remain authoritative until the actual
        # registry TTL expires; the other eight clients keep renewing.
        crashing = _client_command(clients[1], "crash-without-unregister")
        assert crashing == ("crashing", client_ready[1])
        clients[1]["closed"] = True
        _join_normally(clients[1]["process"], clients[1]["pipe"], expected_exitcode=23)
        clients[1]["process_closed"] = True

        readiness, starts, stops = _host_status(service_pipe)
        assert readiness["active_registrations"] == _CLIENT_COUNT - 1
        assert readiness["active_upstreams"] == 1
        assert (starts, stops) == (1, 0)

        expiry_deadline = time.monotonic() + 40.0
        while time.monotonic() < expiry_deadline:
            readiness, starts, stops = _host_status(service_pipe)
            if readiness["active_registrations"] == _CLIENT_COUNT - 2:
                break
            time.sleep(0.2)
        assert readiness["active_registrations"] == _CLIENT_COUNT - 2, readiness
        assert readiness["active_upstreams"] == 1
        assert (starts, stops) == (1, 0)

        survivors_after_expiry = tuple(range(2, _CLIENT_COUNT))
        third = _publish_and_collect(
            service_pipe, clients, "common-event-3", survivors_after_expiry
        )
        assert len({event[2] for event in third.values()}) == 1

        for client_id in survivors_after_expiry:
            closed = _client_command(clients[client_id], "release")
            assert closed == ("closed", audience[client_id][1], "closed", False), closed
            clients[client_id]["closed"] = True
            _join_normally(clients[client_id]["process"], clients[client_id]["pipe"])
            clients[client_id]["process_closed"] = True

        readiness, starts, stops = _host_status(service_pipe)
        assert readiness["active_registrations"] == 0
        assert readiness["active_upstreams"] == 0
        assert readiness["pending_releases"] == 0
        assert (starts, stops) == (1, 1)

        if intruder is not None:
            intruder.close()
            intruder = None
        closed = _host_command(service_pipe, "close")
        assert closed == ("closed", "closed", 1, 1), closed
        host_closed = True
        _join_normally(service_process, service_pipe)
        service_process_closed = True
    finally:
        if intruder is not None:
            with suppress(Exception):
                intruder.close()
        for record in clients.values():
            process, pipe = record["process"], record["pipe"]
            if record["process_closed"]:
                continue
            if not record["closed"]:
                with suppress(Exception):
                    if process.is_alive():
                        pipe.send(("release",))
                        response = _receive(pipe, 4.0)
                        if response[0] == "closed":
                            record["closed"] = True
            _bounded_reap_spawned_process(
                process,
                pipe,
                f"strategy-client-{process.name}",
                forced_cleanup,
                cleanup_failures,
            )
        if not service_process_closed:
            if not host_closed:
                with suppress(Exception):
                    if service_process.is_alive():
                        service_pipe.send(("close",))
                        response = _receive(service_pipe, 4.0)
                        host_closed = response[0] == "closed"
            _bounded_reap_spawned_process(
                service_process,
                service_pipe,
                "market-service-host",
                forced_cleanup,
                cleanup_failures,
            )
        _assert_no_fallback_cleanup(forced_cleanup, cleanup_failures)
        # Retain no private material outside this local integration fixture.
        intruder_secret = None


def test_market_epoch_change_requires_explicit_incomplete_resync(tmp_path):
    """A real SDK client keeps its prior cursor until epoch resync is acknowledged."""

    context = multiprocessing.get_context("spawn")
    rpc_endpoint, market_endpoint = _free_endpoints(2)
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    principal_ref = "synthetic-epoch-principal"
    strategy_ref = "synthetic-epoch-strategy"
    audience = ((client_public, principal_ref, strategy_ref),)
    raw_service_config = _service_config(rpc_endpoint, market_endpoint)
    raw_client_config = _client_config(rpc_endpoint, market_endpoint, strategy_ref)
    registry_path = str(tmp_path / "market-epoch-registry.sqlite3")
    service_identity = WireIdentity(WireRole.SERVICE, "synthetic-market-service", _SERVICE_ID)
    service_process, service_pipe = _start_process(
        context,
        _service_host_process,
        (
            raw_service_config,
            registry_path,
            (server_public, server_secret),
            audience,
        ),
    )
    client_process = client_pipe = None
    client_closed = False
    client_process_closed = False
    host_closed = False
    service_process_closed = False
    forced_cleanup = []
    cleanup_failures = []
    try:
        service_ready = _receive(service_pipe)
        assert service_ready[0] == "ready" and service_ready[1] and service_ready[2], service_ready
        client_process, client_pipe = _start_process(
            context,
            _strategy_client_process,
            (
                principal_ref,
                strategy_ref,
                raw_client_config,
                (server_public, client_public, client_secret),
                service_identity,
            ),
        )
        client_record = {"process": client_process, "pipe": client_pipe}
        ready = _receive(client_pipe)
        assert ready[0] == "ready" and ready[2:5] == ("latest_event", False, False), ready

        time.sleep(0.35)
        _host_command(service_pipe, "publish", "epoch-warmup")
        assert _client_command(client_record, "drain")[0] == "drained"
        first = _publish_and_collect(
            service_pipe,
            {0: client_record},
            "old-epoch-event",
            (0,),
        )
        prior_epoch = first[0][1]
        assert prior_epoch == ready[5]
        assert first[0][2] == 2

        paused = _client_command(client_record, "pause-market", True)
        assert paused == ("market-polling", True)
        reset = _host_command(service_pipe, "reset-epoch")
        assert reset[0] == "epoch-reset" and reset[1] != prior_epoch, reset

        recovered = _client_command(client_record, "recover-market")
        assert recovered[0] == "market-recovered", recovered
        assert recovered[1:6] == (True, False, "latest_event", False, False), recovered
        assert recovered[6] == reset[1]
        assert recovered[7] == 0
        assert recovered[8] == prior_epoch and recovered[9] == first[0][2]

        rejected = _client_command(client_record, "reject-resync")
        assert rejected == ("resync-rejected", "RESYNC_ACKNOWLEDGEMENT_REQUIRED", False)
        accepted = _client_command(client_record, "accept-resync")
        assert accepted[0] == "resync-accepted", accepted
        assert accepted[1:6] == (False, True, "latest_event", False, False), accepted
        assert accepted[6] == reset[1] and accepted[7] == 0
        assert accepted[8] == reset[1] and accepted[9] == 0

        resumed = _client_command(client_record, "pause-market", False)
        assert resumed == ("market-polling", False)
        one_client = {0: client_record}
        new_epoch = _publish_and_collect(service_pipe, one_client, "new-epoch-event", (0,))
        assert new_epoch[0][1] == reset[1]
        assert new_epoch[0][2] == 1

        closed = _client_command(client_record, "release")
        assert closed == ("closed", principal_ref, "closed", False)
        client_closed = True
        _join_normally(client_process, client_pipe)
        client_process_closed = True
        readiness, starts, stops = _host_status(service_pipe)
        assert readiness["active_registrations"] == 0
        assert readiness["active_upstreams"] == 0
        # Recovery recreates only the local SUB socket and repeats the
        # idempotent registration subscription; the provider stays acquired.
        assert (starts, stops) == (1, 1)

        closed = _host_command(service_pipe, "close")
        assert closed == ("closed", "closed", 1, 1), closed
        host_closed = True
        _join_normally(service_process, service_pipe)
        service_process_closed = True
    finally:
        if client_process is not None and client_pipe is not None and not client_process_closed:
            if not client_closed:
                with suppress(Exception):
                    if client_process.is_alive():
                        client_pipe.send(("release",))
                        response = _receive(client_pipe, 4.0)
                        client_closed = response[0] == "closed"
            _bounded_reap_spawned_process(
                client_process,
                client_pipe,
                "epoch-strategy-client",
                forced_cleanup,
                cleanup_failures,
            )
        if not service_process_closed:
            if not host_closed:
                with suppress(Exception):
                    if service_process.is_alive():
                        service_pipe.send(("close",))
                        response = _receive(service_pipe, 4.0)
                        host_closed = response[0] == "closed"
            _bounded_reap_spawned_process(
                service_process,
                service_pipe,
                "epoch-market-service-host",
                forced_cleanup,
                cleanup_failures,
            )
        _assert_no_fallback_cleanup(forced_cleanup, cleanup_failures)
