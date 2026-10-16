"""Two spawned market legs over one real chain prove leg-skew ready transitions.

The service host publishes each leg with an explicit provider source time; one
spawned strategy client holds both leg subscriptions and answers leg-group
reviews through the new read-only head-composition module with an injected
deterministic clock.
"""

from __future__ import annotations

import multiprocessing
import socket
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import zmq
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.market_hub import GatewayMarketHub

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_market import ServiceMarketClient, ZmqMarketTransportFactory
from bt_api_py.service_market_legs import MarketLegBinding, ServiceMarketLegGroup
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)

_SERVICE_ID = "synthetic-market-service"
_SERVICE_INSTANCE = "synthetic-market-hub"
_SERVICE_SCOPE_REF = "synthetic-market-public-scope-v1"
_DOMAIN_REF = "paper:dim02-legs"
_ACCOUNT_REF = "account:synthetic-market-dim02-legs"
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
_MAX_TIME_SKEW = 5.0
_MOUNT = "store.market"

_LEG_KEY_A = MarketSubscriptionKey(
    source_ref="synthetic-market-source",
    entitlement_ref="synthetic-public-entitlement",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="loopback",
        environment="paper",
        product_type="spot",
        instrument_id="synthetic-leg-a",
        native_symbol="SYNTH-LEG-A",
    ),
    channel="trades",
)
_LEG_KEY_B = MarketSubscriptionKey(
    source_ref="synthetic-market-source",
    entitlement_ref="synthetic-public-entitlement",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="loopback",
        environment="paper",
        product_type="spot",
        instrument_id="synthetic-leg-b",
        native_symbol="SYNTH-LEG-B",
    ),
    channel="trades",
)
_LEG_KEYS = {"leg-a": _LEG_KEY_A, "leg-b": _LEG_KEY_B}
_LEG_ORDER = ("leg-a", "leg-b")
_LEG_NAMES_BY_STREAM = {key.stream_id: name for name, key in _LEG_KEYS.items()}


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


def _client_config(rpc_connect: str, market_connect: str, strategy_ref: str) -> dict[str, Any]:
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
                            "mount": _MOUNT,
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


class _TwoLegSyntheticProvider:
    """Deterministic two-key source with per-key acquire/release counting."""

    def __init__(self) -> None:
        self.hub: GatewayMarketHub | None = None
        self.starts: dict[str, int] = {}
        self.stops: dict[str, int] = {}
        self.generations: dict[MarketSubscriptionKey, str] = {}

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        if key not in _LEG_KEYS.values():
            raise AssertionError("only the declared synthetic leg keys are supported")
        if not generation:
            raise AssertionError("the hub must provide a nonempty source generation")
        name = _LEG_NAMES_BY_STREAM[key.stream_id]
        self.starts[name] = self.starts.get(name, 0) + 1
        self.generations[key] = generation
        return ("synthetic-handle", name, generation)

    def unsubscribe(self, handle: object) -> bool:
        if not isinstance(handle, tuple) or handle[0] != "synthetic-handle":
            raise AssertionError("provider received an unknown synthetic handle")
        name, generation = handle[1], handle[2]
        key = _LEG_KEYS[name]
        if self.generations.get(key) != generation:
            raise AssertionError("provider received a stale synthetic handle")
        self.stops[name] = self.stops.get(name, 0) + 1
        del self.generations[key]
        return True

    def publish(self, name: str, sent_at_utc: str, marker: str) -> int:
        if self.hub is None:
            raise AssertionError("synthetic hub is not attached")
        key = _LEG_KEYS[name]
        event = self.hub.publish(
            key,
            {"marker": marker, "source": "dim02-legs-provider"},
            sent_at_utc,
            generation=self.generations[key],
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
                        WireScope(
                            _DOMAIN_REF,
                            account_ref=_ACCOUNT_REF,
                            strategy_ref=strategy_ref,
                        ),
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
    provider = _TwoLegSyntheticProvider()
    hub = GatewayMarketHub(
        provider,
        key_universe=frozenset(_LEG_KEYS.values()),
        principal_allowed_keys={
            principal_ref: frozenset(_LEG_KEYS.values())
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
                control.send(
                    (
                        "status",
                        dict(hub.readiness),
                        dict(provider.starts),
                        dict(provider.stops),
                    )
                )
            elif action == "publish":
                _action, name, sent_at_utc, marker = command
                sequence = provider.publish(name, sent_at_utc, marker)
                control.send(("published", name, sent_at_utc, marker, sequence))
            elif action == "close":
                host.close()
                control.send(("closed", host.state, dict(provider.starts), dict(provider.stops)))
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


def _legs_client_process(
    control, principal_ref, strategy_ref, raw_config, client_keys, service_identity
):
    config = parse_client_config(raw_config)
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)

    def scope_resolver(_scope_ref: str) -> WireScope:
        return WireScope(
            _DOMAIN_REF,
            account_ref=_ACCOUNT_REF,
            strategy_ref=strategy_ref,
        )

    security = ClientCurveKeys(client_keys[0], client_keys[1], client_keys[2])
    runtime = ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=scope_resolver,
        peer_resolver=lambda _service_id: service_identity,
        transport_factory=ZmqTransportFactory({_SECURITY_REF: security}),
        requested_operations={_MOUNT: _MARKET_OPERATIONS},
    )
    markets: dict[str, ServiceMarketClient] = {}
    holder = [datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC)]
    try:
        runtime.start()
        for name in _LEG_ORDER:
            markets[name] = ServiceMarketClient(
                runtime,
                _MOUNT,
                _LEG_KEYS[name],
                subscriber_factory=ZmqMarketTransportFactory({_SECURITY_REF: security}),
                stream_scope=WireScope(_DOMAIN_REF),
                head_check_interval_seconds=2.0,
            )
        for name in _LEG_ORDER:
            markets[name].start()
        group = ServiceMarketLegGroup(
            runtime,
            tuple(MarketLegBinding(_MOUNT, _LEG_KEYS[name]) for name in _LEG_ORDER),
            max_time_skew_seconds=_MAX_TIME_SKEW,
            utc_clock=lambda: holder[0],
        )
        registration_id = runtime.bindings[0].registration_id
        control.send(
            (
                "ready",
                registration_id,
                tuple(_LEG_KEYS[name].stream_id for name in _LEG_ORDER),
                markets["leg-a"].last_bootstrap.snapshot_kind,
                markets["leg-a"].last_bootstrap.snapshot_complete,
                markets["leg-a"].last_bootstrap.full_baseline_available,
            )
        )

        def service_once() -> None:
            delay = runtime.next_maintenance_delay()
            if delay is not None and delay <= 0:
                runtime.maintain()
            for name in _LEG_ORDER:
                markets[name].poll(timeout_ms=0)

        while True:
            service_once()
            if not control.poll(0.005):
                continue
            command = control.recv()
            action = command[0]
            if action == "set-now":
                text = command[1]
                holder[0] = datetime.fromisoformat(
                    text[:-1] + "+00:00" if text.endswith("Z") else text
                )
                control.send(("now", holder[0].isoformat()))
            elif action == "review":
                status = group.review()
                control.send(
                    (
                        "reviewed",
                        status.ready,
                        tuple(
                            (
                                leg.key.stream_id,
                                leg.head,
                                leg.observed_at_utc,
                                leg.age_seconds,
                                leg.no_data,
                                leg.ready,
                            )
                            for leg in status.legs
                        ),
                    )
                )
            elif action == "release":
                for name in _LEG_ORDER:
                    markets[name].close()
                healthy = tuple(markets[name].healthy for name in _LEG_ORDER)
                runtime.close()
                control.send(("closed", runtime.state, healthy))
                return
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


def _review(client, now_utc: str):
    result = _client_command(client, "set-now", now_utc)
    assert result[0] == "now", result
    result = _client_command(client, "review")
    assert result[0] == "reviewed", result
    return result[1], result[2]


def test_dim02_leg_group_time_skew_ready_transitions_over_real_market_chain(tmp_path):
    context = multiprocessing.get_context("spawn")
    rpc_endpoint, market_endpoint = _free_endpoints(2)
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    principal_ref = "synthetic-legs-principal"
    strategy_ref = "synthetic-legs-strategy"
    audience = ((client_public, principal_ref, strategy_ref),)
    raw_service_config = _service_config(rpc_endpoint, market_endpoint)
    raw_client_config = _client_config(rpc_endpoint, market_endpoint, strategy_ref)
    registry_path = str(tmp_path / "dim02-legs-registry.sqlite3")
    service_identity = WireIdentity(WireRole.SERVICE, "synthetic-market-service", _SERVICE_ID)
    service_process, service_pipe = _start_process(
        context,
        _service_host_process,
        (raw_service_config, registry_path, (server_public, server_secret), audience),
    )
    client = None
    client_closed = False
    client_process_closed = False
    host_closed = False
    service_process_closed = False
    forced_cleanup = []
    cleanup_failures = []
    stream_a, stream_b = _LEG_KEY_A.stream_id, _LEG_KEY_B.stream_id
    try:
        service_ready = _receive(service_pipe)
        assert service_ready[0] == "ready" and service_ready[1] and service_ready[2], service_ready
        client_process, client_pipe = _start_process(
            context,
            _legs_client_process,
            (
                principal_ref,
                strategy_ref,
                raw_client_config,
                (server_public, client_public, client_secret),
                service_identity,
            ),
        )
        client = {"process": client_process, "pipe": client_pipe}
        ready = _receive(client_pipe)
        assert ready[0] == "ready" and ready[1], ready
        assert ready[2] == (stream_a, stream_b), ready
        assert ready[3:6] == ("latest_event", False, False), ready

        # Both legs subscribe through one registration, which holds one
        # registration ref per subscribed key; the provider acquires exactly
        # one upstream handle per declared leg key.
        status = _host_command(service_pipe, "status")
        assert status[0] == "status", status
        readiness, starts, stops = status[1], status[2], status[3]
        assert readiness["active_registrations"] == 2, readiness
        assert readiness["active_upstreams"] == 2
        assert starts == {"leg-a": 1, "leg-b": 1}
        assert stops == {}

        # Both legs are silent: explicit no_data, no age, group not ready.
        ready_flag, legs = _review(client, "2026-10-16T10:00:02Z")
        assert ready_flag is False
        assert legs == (
            (stream_a, 0, None, None, True, False),
            (stream_b, 0, None, None, True, False),
        ), legs

        # Leg A reports at T0; leg B is 30 s behind, beyond the declared 5 s
        # skew, so the group stays not ready although A is fresh.
        published = _host_command(service_pipe, "publish", "leg-a", "2026-10-16T10:00:00Z", "a-1")
        assert published == ("published", "leg-a", "2026-10-16T10:00:00Z", "a-1", 1), published
        published = _host_command(service_pipe, "publish", "leg-b", "2026-10-16T09:59:30Z", "b-1")
        assert published[0] == "published" and published[4] == 1, published

        ready_flag, legs = _review(client, "2026-10-16T10:00:04Z")
        assert ready_flag is False
        assert legs == (
            (stream_a, 1, "2026-10-16T10:00:00Z", 4.0, False, True),
            (stream_b, 1, "2026-10-16T09:59:30Z", 34.0, False, False),
        ), legs

        # The fresh leg updates again; the stale leg alone keeps the group not
        # ready, and its judged age still comes from its own source time.
        published = _host_command(service_pipe, "publish", "leg-a", "2026-10-16T10:00:08Z", "a-2")
        assert published[0] == "published" and published[4] == 2, published
        ready_flag, legs = _review(client, "2026-10-16T10:00:08Z")
        assert ready_flag is False
        assert legs == (
            (stream_a, 2, "2026-10-16T10:00:08Z", 0.0, False, True),
            (stream_b, 1, "2026-10-16T09:59:30Z", 38.0, False, False),
        ), legs

        # Leg B catches up within the declared skew: the group turns ready.
        published = _host_command(service_pipe, "publish", "leg-b", "2026-10-16T10:00:07Z", "b-2")
        assert published[0] == "published" and published[4] == 2, published
        ready_flag, legs = _review(client, "2026-10-16T10:00:09Z")
        assert ready_flag is True, legs
        assert legs == (
            (stream_a, 2, "2026-10-16T10:00:08Z", 1.0, False, True),
            (stream_b, 2, "2026-10-16T10:00:07Z", 2.0, False, True),
        ), legs

        # As the reviewed instant advances past the skew, both legs expire and
        # the group falls back to not ready without any new publishes.
        ready_flag, legs = _review(client, "2026-10-16T10:00:40Z")
        assert ready_flag is False
        assert legs == (
            (stream_a, 2, "2026-10-16T10:00:08Z", 32.0, False, False),
            (stream_b, 2, "2026-10-16T10:00:07Z", 33.0, False, False),
        ), legs

        closed = _client_command(client, "release")
        assert closed == ("closed", "closed", (False, False)), closed
        client_closed = True
        _join_normally(client["process"], client["pipe"])
        client_process_closed = True

        status = _host_command(service_pipe, "status")
        assert status[0] == "status", status
        readiness, starts, stops = status[1], status[2], status[3]
        assert readiness["active_registrations"] == 0
        assert readiness["active_upstreams"] == 0
        assert readiness["pending_releases"] == 0
        assert starts == {"leg-a": 1, "leg-b": 1}
        assert stops == {"leg-a": 1, "leg-b": 1}

        closed = _host_command(service_pipe, "close")
        assert closed == ("closed", "closed", {"leg-a": 1, "leg-b": 1}, {"leg-a": 1, "leg-b": 1}), (
            closed
        )
        host_closed = True
        _join_normally(service_process, service_pipe)
        service_process_closed = True
    finally:
        if client is not None and not client_process_closed:
            if not client_closed:
                with suppress(Exception):
                    if client["process"].is_alive():
                        client["pipe"].send(("release",))
                        response = _receive(client["pipe"], 4.0)
                        if response[0] == "closed":
                            client_closed = True
            _bounded_reap_spawned_process(
                client["process"],
                client["pipe"],
                f"legs-client-{client['process'].name}",
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
                "dim02-legs-service-host",
                forced_cleanup,
                cleanup_failures,
            )
        _assert_no_fallback_cleanup(forced_cleanup, cleanup_failures)
