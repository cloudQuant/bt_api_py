"""Real ServiceHost/CURVE proof that publisher audience changes revoke a peer.

The market provider is deterministic and local. The test keeps one authorized
SUB socket alive while replacing the ServiceHost publisher on the same loopback
endpoints with a narrower trusted audience. It observes libzmq's public socket
monitor authentication result, rather than inferring revocation from a quiet
sleep window alone.
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
from bt_api_transport_zmq.market_v2 import ZmqMarketSubscriber
from bt_api_transport_zmq.server import CurveClientCredentials
from zmq.utils.monitor import recv_monitor_message

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_market import ServiceMarketClient, ZmqMarketTransportFactory
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_SERVICE_ID = "trn03-market-service"
_SERVICE_INSTANCE = "trn03-market-hub"
_SERVICE_SCOPE_REF = "trn03-market-scope-v1"
_DOMAIN = "paper:trn03-market"
_ACCOUNT = "account:trn03-synthetic"
_TRANSPORT_REF = "trn03-market-listener-v1"
_SECURITY_REF = "trn03-market-curve-v1"
_SERVICE_PRINCIPAL = "trn03-market-service-principal"
_MARKET_OPERATIONS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
_MONITORED_EVENTS = zmq.EVENT_HANDSHAKE_SUCCEEDED | zmq.EVENT_HANDSHAKE_FAILED_AUTH
_REQUEST_TIMEOUT_MS = 1_000
_MAX_MESSAGE_BYTES = 262_144

_MARKET_KEY = MarketSubscriptionKey(
    source_ref="trn03-synthetic-market",
    entitlement_ref="trn03-public-entitlement",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="loopback",
        environment="paper",
        product_type="spot",
        instrument_id="trn03-instrument-a",
        native_symbol="TRN03-A-USD",
    ),
    channel="trades",
)
_SERVICE_IDENTITY = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
_SERVICE_SCOPE = WireScope(_DOMAIN)

_PRINCIPAL_A = "trn03-client-a"
_STRATEGY_A = "trn03-strategy-a"
_PRINCIPAL_B = "trn03-client-b"
_STRATEGY_B = "trn03-strategy-b"


def _free_endpoints() -> tuple[str, str]:
    probes: list[socket.socket] = []
    try:
        endpoints: list[str] = []
        for _ in range(2):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            probes.append(probe)
            endpoints.append(f"tcp://127.0.0.1:{probe.getsockname()[1]}")
        return endpoints[0], endpoints[1]
    finally:
        for probe in probes:
            probe.close()


def _service_config(rpc_endpoint: str, market_endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": _TRANSPORT_REF,
        "registration_policy_ref": "trn03-registration-v1",
        "service_requirements_ref": "trn03-requirements-v1",
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
                    "endpoints": {
                        "rpc_bind": rpc_endpoint,
                        "market_bind": market_endpoint,
                    },
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "high_water_mark": 64,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _client_config(rpc_endpoint: str, market_endpoint: str, strategy_ref: str):
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
                            "transport_ref": "trn03-market-client-v1",
                        }
                    ],
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "heartbeat_interval_seconds": 10,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "trn03-market-client-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {
                        "rpc_connect": rpc_endpoint,
                        "market_connect": market_endpoint,
                    },
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "high_water_mark": 64,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


class _SyntheticProvider:
    def __init__(self) -> None:
        self.hub: GatewayMarketHub | None = None
        self.generation: str | None = None
        self.starts = 0
        self.stops = 0

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        if key != _MARKET_KEY or not generation:
            raise AssertionError("unexpected synthetic market subscription")
        self.starts += 1
        self.generation = generation
        return ("trn03-synthetic-handle", self.starts, generation)

    def unsubscribe(self, handle: object) -> bool:
        if (
            not isinstance(handle, tuple)
            or len(handle) != 3
            or handle[0] != "trn03-synthetic-handle"
            or handle[2] != self.generation
        ):
            raise AssertionError("unexpected synthetic market handle")
        self.stops += 1
        self.generation = None
        return True

    def publish(self, marker: str) -> int:
        if self.hub is None or self.generation is None:
            raise AssertionError("synthetic market stream is not active")
        event = self.hub.publish(
            _MARKET_KEY,
            {"marker": marker, "source": "trn03-local-synthetic"},
            datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            generation=self.generation,
        )
        return event.header.sequence


def _market_host_process(
    control,
    raw_config,
    registry_path,
    server_keys,
    audience,
) -> None:
    config = parse_service_config(raw_config)
    principals = {public_key: principal_ref for public_key, principal_ref, _ in audience}
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
                            _DOMAIN,
                            account_ref=_ACCOUNT,
                            strategy_ref=strategy_ref,
                        ),
                        _MARKET_OPERATIONS,
                    ),
                ),
            ),
        )
        for _public_key, principal_ref, strategy_ref in audience
    }
    authority = TrustedServiceAuthority(
        identity=_SERVICE_IDENTITY,
        scope=_SERVICE_SCOPE,
        scope_ref=config.scope_ref,
        scope_version="trn03-market-scope-v1",
        security_ref=_SECURITY_REF,
        security_version="curve-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="trn03-registration-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="trn03-requirements-v1",
        curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],
        principals=principals,
        grants=grants,
    )
    provider = _SyntheticProvider()
    hub = GatewayMarketHub(
        provider,
        key_universe=frozenset({_MARKET_KEY}),
        principal_allowed_keys={
            principal_ref: frozenset({_MARKET_KEY})
            for _public_key, principal_ref, _strategy_ref in audience
        },
        service_identity=_SERVICE_IDENTITY,
        service_scope=_SERVICE_SCOPE,
    )
    provider.hub = hub

    def resources(component, _instance_id, _options, _scope):
        if component != "gateway":
            raise AssertionError("the test host must contain only the market hub")
        return ComponentResources.owned({"hub": hub}, ready=lambda: hub.ready, close=hub.close)

    host = ServiceHost(config, authority, registry_path, resources)
    monitor = None
    monitor_events: list[int] = []
    try:
        host.start()
        publisher = host._market_publisher
        publisher_socket = getattr(publisher, "_socket", None)
        if publisher_socket is None:
            raise AssertionError("ServiceHost did not retain its market publisher socket")
        monitor = publisher_socket.get_monitor_socket(events=_MONITORED_EVENTS)
        control.send(("ready", host.endpoint, host.market_endpoint))
        while True:
            host.serve_once(5)
            while monitor.poll(0, zmq.POLLIN):
                event = recv_monitor_message(monitor, flags=zmq.NOBLOCK)
                event_code = event.get("event")
                if event_code in {
                    zmq.EVENT_HANDSHAKE_SUCCEEDED,
                    zmq.EVENT_HANDSHAKE_FAILED_AUTH,
                }:
                    monitor_events.append(event_code)
            if not control.poll(0):
                continue
            command = control.recv()
            action = command[0]
            if action == "publish":
                control.send(("published", command[1], provider.publish(command[1])))
            elif action == "monitor-events":
                control.send(("monitor-events", tuple(monitor_events)))
                monitor_events.clear()
            elif action == "close":
                monitor.close(linger=0)
                monitor = None
                host.close()
                control.send(("closed", host.state, provider.starts, provider.stops))
                return
            else:
                raise AssertionError("unknown ServiceHost test command")
    except BaseException as error:
        if monitor is not None:
            with suppress(BaseException):
                monitor.close(linger=0)
        if host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(error, "code", type(error).__name__)))
        raise
    finally:
        control.close()


def _start_host(context, raw_config, registry_path, server_keys, audience):
    parent, child = context.Pipe(duplex=True)
    process = context.Process(
        target=_market_host_process,
        args=(child, raw_config, registry_path, server_keys, audience),
    )
    process.start()
    child.close()
    return {"process": process, "pipe": parent, "closed": False}


def _receive(pipe, timeout: float = 12.0):
    assert pipe.poll(timeout), "spawned TRN03 ServiceHost did not respond within its bound"
    return pipe.recv()


def _host_command(host_record, *command):
    pipe = host_record["pipe"]
    pipe.send(command)
    reply = _receive(pipe)
    if reply[0] == "error":
        raise AssertionError(f"spawned ServiceHost failed with finite code {reply[1]}")
    return reply


def _stop_host(host_record, shutdown_results: list[tuple[int | None, bool]]) -> tuple[int, int]:
    process = host_record["process"]
    response = _host_command(host_record, "close")
    assert response[0] == "closed", response
    assert response[1] == "closed", response
    process.join(12)
    exitcode = process.exitcode
    reaped = not process.is_alive()
    host_record["exitcode"] = exitcode
    host_record["reaped"] = reaped
    shutdown_results.append((exitcode, reaped))
    assert exitcode == 0, exitcode
    assert reaped
    host_record["pipe"].close()
    process.close()
    host_record["closed"] = True
    return response[2], response[3]


def _new_client(
    rpc_endpoint: str,
    market_endpoint: str,
    server_public: bytes,
    client_public: bytes,
    client_secret: bytes,
    principal_ref: str,
    strategy_ref: str,
):
    config = parse_client_config(_client_config(rpc_endpoint, market_endpoint, strategy_ref))
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)
    keys = ClientCurveKeys(server_public, client_public, client_secret)
    factory = ZmqTransportFactory({_SECURITY_REF: keys})
    runtime = ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=lambda _scope_ref: WireScope(
            _DOMAIN, account_ref=_ACCOUNT, strategy_ref=strategy_ref
        ),
        peer_resolver=lambda _service_id: _SERVICE_IDENTITY,
        transport_factory=factory,
        requested_operations={"store.market": _MARKET_OPERATIONS},
    )
    receipt = runtime.start()
    assert receipt.degraded_count == 0
    assert len(receipt.bindings) == 1
    market = ServiceMarketClient(
        runtime,
        "store.market",
        _MARKET_KEY,
        subscriber_factory=ZmqMarketTransportFactory({_SECURITY_REF: keys}),
        stream_scope=_SERVICE_SCOPE,
        head_check_interval_seconds=60.0,
    )
    bootstrap = market.start()
    return runtime, market, bootstrap


def _receive_marker(market: ServiceMarketClient, marker: str, timeout: float = 4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = market.poll(timeout_ms=25)
        if event is not None and event.payload["data"].get("marker") == marker:
            return event
    raise AssertionError(f"SDK market client did not receive marker {marker!r}")


def _wait_for_monitor_event(
    host_record,
    event_code: int,
    timeout: float = 4.0,
    min_count: int = 1,
) -> int:
    if min_count < 1:
        raise ValueError("min_count must be positive")
    deadline = time.monotonic() + timeout
    observed = 0
    while time.monotonic() < deadline:
        response = _host_command(host_record, "monitor-events")
        assert response[0] == "monitor-events", response
        observed += sum(1 for item in response[1] if item == event_code)
        if observed >= min_count:
            return observed
        time.sleep(0.02)
    return observed


def _close_client(market, runtime, failures: list[str]) -> None:
    if market is not None:
        try:
            market.close()
        except Exception as error:
            failures.append(f"market_close:{getattr(error, 'code', type(error).__name__)}")
    if runtime is not None:
        try:
            runtime.close()
            if runtime.state != "closed" or runtime.cleanup_failures:
                failures.append("runtime_close_incomplete")
        except Exception as error:
            failures.append(f"runtime_close:{getattr(error, 'code', type(error).__name__)}")


def _cleanup_host(
    host_record,
    forced_cleanup: list[str],
    shutdown_results: list[tuple[int | None, bool]],
) -> None:
    if host_record is None or host_record["closed"]:
        return
    process, pipe = host_record["process"], host_record["pipe"]
    try:
        if process.is_alive():
            pipe.send(("close",))
            if pipe.poll(5):
                response = pipe.recv()
                if response[0] == "closed":
                    process.join(10)
    except Exception as error:
        forced_cleanup.append(f"service_host_cleanup_error:{type(error).__name__}")
    if process.is_alive():
        forced_cleanup.append("service_host_terminate")
        process.terminate()
        process.join(5)
    exitcode = process.exitcode
    reaped = not process.is_alive()
    host_record["exitcode"] = exitcode
    host_record["reaped"] = reaped
    shutdown_results.append((exitcode, reaped))
    if exitcode != 0:
        forced_cleanup.append("service_host_nonzero_exit")
    if not reaped:
        forced_cleanup.append("service_host_not_reaped")
    with suppress(Exception):
        pipe.close()
    if reaped and exitcode is not None:
        with suppress(Exception):
            process.close()
        host_record["closed"] = True


def test_service_host_market_audience_revoke_reconnect_blocks_existing_subscriber(
    tmp_path,
) -> None:
    """A removed CURVE key cannot reconnect to the replacement market endpoint."""

    context = multiprocessing.get_context("spawn")
    rpc_endpoint, market_endpoint = _free_endpoints()
    server_public, server_secret = zmq.curve_keypair()
    client_a_public, client_a_secret = zmq.curve_keypair()
    client_b_public, client_b_secret = zmq.curve_keypair()
    audience_ab = (
        (client_a_public, _PRINCIPAL_A, _STRATEGY_A),
        (client_b_public, _PRINCIPAL_B, _STRATEGY_B),
    )
    audience_b = ((client_b_public, _PRINCIPAL_B, _STRATEGY_B),)
    raw_service_config = _service_config(rpc_endpoint, market_endpoint)
    registry_path = str(tmp_path / "trn03-market-registry.sqlite3")

    first_host = second_host = None
    runtime_a = market_a = runtime_b = market_b = None
    revoked_subscriber = None
    fresh_b_runtime = fresh_b_market = None
    rejected_a_runtime = None
    forced_cleanup: list[str] = []
    cleanup_failures: list[str] = []
    shutdown_results: list[tuple[int | None, bool]] = []
    old_epoch = new_epoch = None
    try:
        first_host = _start_host(
            context,
            raw_service_config,
            registry_path,
            (server_public, server_secret),
            audience_ab,
        )
        startup = _receive(first_host["pipe"])
        assert startup == ("ready", rpc_endpoint, market_endpoint), startup

        runtime_a, market_a, bootstrap_a = _new_client(
            rpc_endpoint,
            market_endpoint,
            server_public,
            client_a_public,
            client_a_secret,
            _PRINCIPAL_A,
            _STRATEGY_A,
        )
        runtime_b, market_b, bootstrap_b = _new_client(
            rpc_endpoint,
            market_endpoint,
            server_public,
            client_b_public,
            client_b_secret,
            _PRINCIPAL_B,
            _STRATEGY_B,
        )
        old_epoch = bootstrap_a.cursor_epoch
        assert old_epoch == bootstrap_b.cursor_epoch

        revoked_subscriber = ZmqMarketSubscriber(
            market_endpoint,
            _SERVICE_IDENTITY,
            _SERVICE_SCOPE,
            credentials=CurveClientCredentials(server_public, client_a_public, client_a_secret),
            high_water_mark=64,
            max_message_bytes=_MAX_MESSAGE_BYTES,
        )
        initial_successes = _wait_for_monitor_event(
            first_host, zmq.EVENT_HANDSHAKE_SUCCEEDED, timeout=2.0, min_count=3
        )
        assert initial_successes >= 3, initial_successes

        initial_marker = "trn03-audience-before-revoke"
        _host_command(first_host, "publish", initial_marker)
        first_a = _receive_marker(market_a, initial_marker)
        first_b = _receive_marker(market_b, initial_marker)
        raw_deadline = time.monotonic() + 2.0
        raw_first = None
        while time.monotonic() < raw_deadline and raw_first is None:
            raw_first = revoked_subscriber.receive(timeout=0.025)
        assert raw_first is not None
        assert raw_first.payload["data"]["marker"] == initial_marker
        assert first_a.header.stream_epoch == old_epoch
        assert first_b.header.stream_epoch == old_epoch

        _close_client(market_a, runtime_a, cleanup_failures)
        market_a = runtime_a = None
        _close_client(market_b, runtime_b, cleanup_failures)
        market_b = runtime_b = None
        first_provider_counts = _stop_host(first_host, shutdown_results)
        assert first_provider_counts == (1, 1), first_provider_counts
        first_host = None

        # Rebind the same endpoints with the trusted audience narrowed to B.
        second_host = _start_host(
            context,
            raw_service_config,
            registry_path,
            (server_public, server_secret),
            audience_b,
        )
        startup = _receive(second_host["pipe"])
        assert startup == ("ready", rpc_endpoint, market_endpoint), startup

        # The old A SUB socket reconnects to the same endpoint and is denied by
        # the replacement host's CURVE/ZAP audience. Its monitor event is the
        # positive revocation observation; absence of a frame is only a second
        # consistency check.
        denied_handshakes = _wait_for_monitor_event(
            second_host, zmq.EVENT_HANDSHAKE_FAILED_AUTH, timeout=4.0
        )
        assert denied_handshakes >= 1, "removed CURVE key did not produce an auth denial event"

        fresh_b_runtime, fresh_b_market, fresh_b_bootstrap = _new_client(
            rpc_endpoint,
            market_endpoint,
            server_public,
            client_b_public,
            client_b_secret,
            _PRINCIPAL_B,
            _STRATEGY_B,
        )
        new_epoch = fresh_b_bootstrap.cursor_epoch
        assert new_epoch != old_epoch
        authorized_handshakes = _wait_for_monitor_event(
            second_host, zmq.EVENT_HANDSHAKE_SUCCEEDED, timeout=3.0
        )
        assert authorized_handshakes >= 1, "replacement endpoint did not authenticate B"

        post_revoke_marker = "trn03-audience-after-revoke-new-epoch"
        _host_command(second_host, "publish", post_revoke_marker)
        fresh_event = _receive_marker(fresh_b_market, post_revoke_marker)
        assert fresh_event.header.stream_epoch == new_epoch
        assert fresh_event.header.stream_epoch != old_epoch
        assert fresh_event.payload["data"]["marker"] == post_revoke_marker

        revoked_event = revoked_subscriber.receive(timeout=0.075)
        assert revoked_event is None
        # The failed-auth monitor observation above proves the old connection
        # attempted to reconnect and the new audience rejected its CURVE key.

        # The removed A key cannot reach the registration service. Startup
        # fails before sending a registration request and creates no binding.
        rejected_a_runtime = ServiceClientRuntime(
            parse_client_config(_client_config(rpc_endpoint, market_endpoint, _STRATEGY_A)),
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_A, _STRATEGY_A),
            scope_resolver=lambda _scope_ref: WireScope(
                _DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY_A
            ),
            peer_resolver=lambda _service_id: _SERVICE_IDENTITY,
            transport_factory=ZmqTransportFactory(
                {_SECURITY_REF: ClientCurveKeys(server_public, client_a_public, client_a_secret)}
            ),
            requested_operations={"store.market": _MARKET_OPERATIONS},
        )
        with pytest.raises(ServiceRuntimeError) as denied_registration:
            rejected_a_runtime.start()
        assert denied_registration.value.code == "REQUEST_NOT_SENT"
        assert rejected_a_runtime.state == "failed"
        assert rejected_a_runtime.bindings == ()
        rejected_a_runtime.close()
        assert rejected_a_runtime.state == "closed"
        assert rejected_a_runtime.cleanup_failures == 0
        rejected_a_runtime = None

        _close_client(fresh_b_market, fresh_b_runtime, cleanup_failures)
        fresh_b_market = fresh_b_runtime = None
        second_provider_counts = _stop_host(second_host, shutdown_results)
        assert second_provider_counts == (1, 1), second_provider_counts
        second_host = None
        revoked_subscriber.close()
        revoked_subscriber = None
        assert cleanup_failures == []
        assert forced_cleanup == []
        assert shutdown_results == [(0, True), (0, True)], shutdown_results
    finally:
        if rejected_a_runtime is not None:
            with suppress(Exception):
                rejected_a_runtime.close()
        _close_client(market_a, runtime_a, cleanup_failures)
        _close_client(market_b, runtime_b, cleanup_failures)
        _close_client(fresh_b_market, fresh_b_runtime, cleanup_failures)
        if revoked_subscriber is not None:
            with suppress(Exception):
                revoked_subscriber.close()
        _cleanup_host(first_host, forced_cleanup, shutdown_results)
        _cleanup_host(second_host, forced_cleanup, shutdown_results)
        assert forced_cleanup == [], forced_cleanup
        assert cleanup_failures == [], cleanup_failures
        if not forced_cleanup:
            assert all(exitcode == 0 and reaped for exitcode, reaped in shutdown_results)
