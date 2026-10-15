"""Real ServiceHost and CURVE market publisher topic-version integration."""

from __future__ import annotations

import queue
import socket
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import zmq
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.market_hub import GatewayMarketHub

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

_SERVICE_ID = "synthetic-topic-market-service"
_SERVICE_INSTANCE = "synthetic-topic-market-hub"
_SERVICE_SCOPE_REF = "synthetic-topic-market-scope-v1"
_DOMAIN_REF = "paper:topic-v2-integration"
_ACCOUNT_REF = "account:synthetic-topic-market"
_STRATEGY_REF = "strategy:synthetic-topic-market"
_PRINCIPAL_REF = "principal:synthetic-topic-market"
_TRANSPORT_REF = "market-topic-v1"
_SECURITY_REF = "market-topic-curve-v1"
_MARKET_OPERATIONS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
_KEY_A = MarketSubscriptionKey(
    source_ref="synthetic-topic-source-a",
    entitlement_ref="synthetic-topic-entitlement",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="loopback",
        environment="paper",
        product_type="spot",
        instrument_id="topic-instrument-a",
        native_symbol="TOPIC-A-USD",
    ),
    channel="trades",
)
_KEY_B = MarketSubscriptionKey(
    source_ref="synthetic-topic-source-b",
    entitlement_ref="synthetic-topic-entitlement",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="loopback",
        environment="paper",
        product_type="spot",
        instrument_id="topic-instrument-b",
        native_symbol="TOPIC-B-USD",
    ),
    channel="trades",
)
_KEYS = (_KEY_A, _KEY_B)


def _free_endpoints(count: int = 2) -> tuple[str, ...]:
    probes: list[socket.socket] = []
    try:
        endpoints = []
        for _ in range(count):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            probes.append(probe)
            endpoints.append(f"tcp://127.0.0.1:{probe.getsockname()[1]}")
        return tuple(endpoints)
    finally:
        for probe in probes:
            probe.close()


def _service_config(rpc_bind: str, market_bind: str, wire_version: int) -> dict[str, Any]:
    options: dict[str, Any] = {
        "high_water_mark": 128,
        "max_message_bytes": 262_144,
    }
    if wire_version == 2:
        options["market_wire_version"] = 2
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": _TRANSPORT_REF,
        "registration_policy_ref": "synthetic-topic-registration-v1",
        "service_requirements_ref": "synthetic-topic-requirements-v1",
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
                    "options": options,
                }
            },
        },
    }


def _client_config(
    rpc_connect: str,
    market_connect: str,
    wire_version: int,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "request_timeout_ms": 2_000,
        "high_water_mark": 128,
        "max_message_bytes": 262_144,
    }
    if wire_version == 2:
        options["market_wire_version"] = 2
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _STRATEGY_REF},
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
                            "transport_ref": _TRANSPORT_REF,
                        }
                    ],
                    "options": {
                        "request_timeout_ms": 2_000,
                        "heartbeat_interval_seconds": 5,
                    },
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
                        "rpc_connect": rpc_connect,
                        "market_connect": market_connect,
                    },
                    "security_profile_ref": _SECURITY_REF,
                    "options": options,
                }
            },
        },
    }


class _SyntheticProvider:
    def __init__(self) -> None:
        self.hub: GatewayMarketHub | None = None
        self.active: dict[MarketSubscriptionKey, str] = {}
        self.starts = 0
        self.stops = 0

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        if key not in _KEYS or not generation or key in self.active:
            raise AssertionError("gateway requested an undeclared synthetic stream")
        self.active[key] = generation
        self.starts += 1
        return (key, generation)

    def unsubscribe(self, handle: object) -> bool:
        if (
            not isinstance(handle, tuple)
            or len(handle) != 2
            or self.active.get(handle[0]) != handle[1]
        ):
            raise AssertionError("gateway released an unknown stream handle")
        self.active.pop(handle[0])
        self.stops += 1
        return True

    def publish(self, key: MarketSubscriptionKey, marker: str) -> int:
        if self.hub is None or key not in self.active:
            raise AssertionError("synthetic stream has not been subscribed")
        sent_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        event = self.hub.publish(
            key,
            {"marker": marker},
            sent_at,
            generation=self.active[key],
        )
        return event.header.sequence


class _LocalMarketHost:
    """Run ServiceHost and all of its socket operations on one owner thread."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        wire_version: int,
        server_keys: tuple[bytes, bytes],
        client_public_key: bytes,
    ) -> None:
        self._commands: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._results: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._terminal_results: queue.Queue[tuple[Any, ...]] = queue.Queue(maxsize=1)
        self._rpc_bind, self._market_bind = _free_endpoints()
        self._config = _service_config(self._rpc_bind, self._market_bind, wire_version)
        self._server_keys = server_keys
        self._client_public_key = client_public_key
        self._registry_path = str(tmp_path / f"registry-v{wire_version}.sqlite3")
        self._thread = threading.Thread(
            target=self._run,
            name="topic-market-service-host",
        )
        self._thread.start()
        try:
            ready = self._receive(timeout=10)
            if ready[0] != "ready":
                startup_error = ready[1] if len(ready) > 1 else None
                raise AssertionError(f"ServiceHost startup failed: {ready!r}") from startup_error
        except BaseException:
            self._commands.put(("close",))
            terminal = self._join_and_read_terminal()
            if terminal[0] == "closed":
                assert terminal[2] == "closed", terminal
                assert terminal[3] == terminal[4], terminal
                assert terminal[5] == {}, terminal
            else:
                assert terminal[0] == "error", terminal
                assert isinstance(terminal[1], BaseException), terminal
            raise
        self.endpoint, self.market_endpoint = ready[1], ready[2]

    def _receive(self, timeout: float = 5.0) -> tuple[Any, ...]:
        try:
            return self._results.get(timeout=timeout)
        except queue.Empty as exc:
            raise AssertionError("ServiceHost owner thread did not answer") from exc

    def _run(self) -> None:
        host: ServiceHost | None = None
        provider = _SyntheticProvider()
        failure: BaseException | None = None
        try:
            config = parse_service_config(self._config)
            service_identity = WireIdentity(WireRole.SERVICE, "topic-market-service", _SERVICE_ID)
            service_scope = WireScope(_DOMAIN_REF)
            client_identity = WireIdentity(
                WireRole.STRATEGY_CLIENT,
                _PRINCIPAL_REF,
                _STRATEGY_REF,
            )
            authority = TrustedServiceAuthority(
                identity=service_identity,
                scope=service_scope,
                scope_ref=config.scope_ref,
                scope_version="synthetic-topic-market-scope-v1",
                security_ref=_SECURITY_REF,
                security_version="curve-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="policy-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="requirements-v1",
                curve_public_key=self._server_keys[0],
                curve_secret_key=self._server_keys[1],
                principals={self._client_public_key: _PRINCIPAL_REF},
                grants={
                    _PRINCIPAL_REF: (
                        PrincipalGrant(
                            client_identity,
                            (
                                ResourceGrant(
                                    _SERVICE_INSTANCE,
                                    "service.market_source",
                                    _SERVICE_SCOPE_REF,
                                    WireScope(
                                        _DOMAIN_REF,
                                        account_ref=_ACCOUNT_REF,
                                        strategy_ref=_STRATEGY_REF,
                                    ),
                                    _MARKET_OPERATIONS,
                                ),
                            ),
                        ),
                    )
                },
            )
            hub = GatewayMarketHub(
                provider,
                key_universe=frozenset(_KEYS),
                principal_allowed_keys={_PRINCIPAL_REF: frozenset(_KEYS)},
                service_identity=service_identity,
                service_scope=service_scope,
            )
            provider.hub = hub

            def resources(component, _instance_id, _options, _scope):
                if component != "gateway":
                    raise AssertionError("unexpected synthetic component")
                return ComponentResources.owned(
                    {"hub": hub},
                    ready=lambda: hub.ready,
                    close=hub.close,
                )

            host = ServiceHost(config, authority, self._registry_path, resources)
            host.start()
            self._results.put(("ready", host.endpoint, host.market_endpoint))
            while True:
                host.serve_once(2)
                try:
                    command = self._commands.get_nowait()
                except queue.Empty:
                    continue
                if command[0] == "publish":
                    sequence = provider.publish(command[1], command[2])
                    self._results.put(("published", command[2], sequence))
                elif command[0] == "close":
                    host.close()
                    return
                else:
                    raise AssertionError("unknown ServiceHost test command")
        except BaseException as exc:
            failure = exc
            self._results.put(("error", exc))
            if host is not None and host.state not in {"closed", "failed"}:
                with suppress(BaseException):
                    host.close()
        finally:
            self._terminal_results.put(
                (
                    "error" if failure is not None else "closed",
                    failure,
                    None if host is None else host.state,
                    provider.starts,
                    provider.stops,
                    dict(provider.active),
                )
            )

    def publish(self, key: MarketSubscriptionKey, marker: str) -> int:
        self._commands.put(("publish", key, marker))
        result = self._receive()
        assert result[0] == "published" and result[1] == marker, result
        return result[2]

    def _join_and_read_terminal(self) -> tuple[Any, ...]:
        self._thread.join(timeout=15)
        assert not self._thread.is_alive(), "ServiceHost thread did not stop within its bound"
        try:
            return self._terminal_results.get(timeout=1)
        except queue.Empty as exc:
            raise AssertionError("ServiceHost thread did not record its terminal outcome") from exc

    def close(self) -> None:
        try:
            if self._thread.is_alive():
                self._commands.put(("close",))
        finally:
            terminal = self._join_and_read_terminal()
        assert terminal[0] == "closed", terminal
        assert terminal[2] == "closed", terminal
        assert terminal[3] == terminal[4], terminal
        assert terminal[5] == {}, terminal


def _new_client(
    host: _LocalMarketHost,
    *,
    wire_version: int,
    server_key: bytes,
    public_key: bytes,
    secret_key: bytes,
) -> ServiceClientRuntime:
    config = parse_client_config(_client_config(host.endpoint, host.market_endpoint, wire_version))
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF)
    scope = WireScope(
        _DOMAIN_REF,
        account_ref=_ACCOUNT_REF,
        strategy_ref=_STRATEGY_REF,
    )
    security = ClientCurveKeys(
        server_key=server_key,
        public_key=public_key,
        secret_key=secret_key,
    )
    return ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=lambda _scope_ref: scope,
        peer_resolver=lambda _service_id: WireIdentity(
            WireRole.SERVICE,
            "topic-market-service",
            _SERVICE_ID,
        ),
        transport_factory=ZmqTransportFactory({_SECURITY_REF: security}),
        requested_operations={"store.market": _MARKET_OPERATIONS},
    )


def _new_market(
    runtime: ServiceClientRuntime,
    key: MarketSubscriptionKey,
    *,
    server_key: bytes,
    public_key: bytes,
    secret_key: bytes,
) -> ServiceMarketClient:
    security = ClientCurveKeys(
        server_key=server_key,
        public_key=public_key,
        secret_key=secret_key,
    )
    return ServiceMarketClient(
        runtime,
        "store.market",
        key,
        subscriber_factory=ZmqMarketTransportFactory({_SECURITY_REF: security}),
        stream_scope=WireScope(_DOMAIN_REF),
        head_check_interval_seconds=0.1,
    )


def _receive_marker(
    client: ServiceMarketClient,
    marker: str,
    *,
    timeout: float = 3.0,
) -> tuple[str, str, int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = client.poll(timeout_ms=50)
        if event is not None and event.payload["data"].get("marker") == marker:
            return (
                event.header.stream_id,
                event.header.stream_epoch,
                event.header.sequence,
            )
    raise AssertionError(f"SDK market subscription did not receive {marker!r}")


def _assert_no_marker(client: ServiceMarketClient, marker: str, *, timeout: float = 0.3) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = client.poll(timeout_ms=25)
        assert event is None or event.payload["data"].get("marker") != marker, event


def test_real_host_topic_v2_filters_two_streams_and_v1_remains_compatible(tmp_path):
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    host = _LocalMarketHost(
        tmp_path,
        wire_version=2,
        server_keys=(server_public, server_secret),
        client_public_key=client_public,
    )
    runtime: ServiceClientRuntime | None = None
    market_a: ServiceMarketClient | None = None
    market_b: ServiceMarketClient | None = None
    try:
        runtime = _new_client(
            host,
            wire_version=2,
            server_key=server_public,
            public_key=client_public,
            secret_key=client_secret,
        )
        market_a = _new_market(
            runtime,
            _KEY_A,
            server_key=server_public,
            public_key=client_public,
            secret_key=client_secret,
        )
        market_b = _new_market(
            runtime,
            _KEY_B,
            server_key=server_public,
            public_key=client_public,
            secret_key=client_secret,
        )
        runtime.start()
        market_a.start()
        market_b.start()
        time.sleep(0.2)  # Let both CURVE SUB sockets join the live PUB endpoint.

        sequence_a = host.publish(_KEY_A, "v2-only-a")
        event_a = _receive_marker(market_a, "v2-only-a")
        assert event_a[0] == _KEY_A.stream_id and event_a[2] == sequence_a
        _assert_no_marker(market_b, "v2-only-a")

        sequence_b = host.publish(_KEY_B, "v2-only-b")
        event_b = _receive_marker(market_b, "v2-only-b")
        assert event_b[0] == _KEY_B.stream_id and event_b[2] == sequence_b
        _assert_no_marker(market_a, "v2-only-b")
    finally:
        failures = []
        close_actions = [market.close for market in (market_b, market_a) if market is not None]
        if runtime is not None:
            close_actions.append(runtime.close)
        close_actions.append(host.close)
        for close in close_actions:
            try:
                close()
            except BaseException as exc:
                failures.append(type(exc).__name__)
        if runtime is not None:
            assert runtime.state == "closed"
        assert not failures, tuple(failures)

    # A second real service uses the legacy profile shape with no version field.
    # Both parsing and transport factory construction therefore exercise v1.
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    legacy_host = _LocalMarketHost(
        tmp_path,
        wire_version=1,
        server_keys=(server_public, server_secret),
        client_public_key=client_public,
    )
    legacy_runtime: ServiceClientRuntime | None = None
    legacy_market: ServiceMarketClient | None = None
    mismatch_runtime: ServiceClientRuntime | None = None
    try:
        legacy_runtime = _new_client(
            legacy_host,
            wire_version=1,
            server_key=server_public,
            public_key=client_public,
            secret_key=client_secret,
        )
        legacy_market = _new_market(
            legacy_runtime,
            _KEY_A,
            server_key=server_public,
            public_key=client_public,
            secret_key=client_secret,
        )
        mismatch_runtime = _new_client(
            legacy_host,
            wire_version=2,
            server_key=server_public,
            public_key=client_public,
            secret_key=client_secret,
        )
        legacy_runtime.start()
        legacy_market.start()
        time.sleep(0.2)
        legacy_sequence = legacy_host.publish(_KEY_A, "v1-compatible")
        legacy_event = _receive_marker(legacy_market, "v1-compatible")
        assert legacy_event[0] == _KEY_A.stream_id
        assert legacy_event[2] == legacy_sequence

        try:
            mismatch_runtime.start()
        except ServiceRuntimeError as exc:
            assert exc.code == "MARKET_WIRE_VERSION_MISMATCH"
        else:
            raise AssertionError("a v2 SDK binding must reject a v1 market service")
        assert mismatch_runtime.state == "failed"
    finally:
        failures = []
        close_actions = []
        if mismatch_runtime is not None:
            close_actions.append(mismatch_runtime.close)
        if legacy_market is not None:
            close_actions.append(legacy_market.close)
        if legacy_runtime is not None:
            close_actions.append(legacy_runtime.close)
        close_actions.append(legacy_host.close)
        for close in close_actions:
            try:
                close()
            except BaseException as exc:
                failures.append(type(exc).__name__)
        if mismatch_runtime is not None:
            assert mismatch_runtime.state == "closed"
        if legacy_runtime is not None:
            assert legacy_runtime.state == "closed"
        assert not failures, tuple(failures)
