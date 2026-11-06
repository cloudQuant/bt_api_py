"""Iteration 62 (62.08): the Gateway market runtime lease before the first subscription.

``create_gateway_market_runtime`` registers the strategy client with the Gateway
host (required lease: the host registry's 30 s default). The runtime renews only
when its owner thread calls ``maintain()``, which a market client does while it
is polled; before the first subscription nothing renews. A Backtrader host
starts the store (CTP trader login, clock check) and the broker (account
reconcile) before ``data._start()`` subscribes. On SimNow (2026-11-06) that took
more than 41 s in 4 of about 17 Gateway example runs, and the first subscription
failed with ``MARKET_STREAM_PROJECTOR_UNAVAILABLE`` <- ``RUNTIME_BINDING_UNAVAILABLE``
(cause ``REQUIRED_LEASE_EXPIRED``).

``GatewayMarketTickRuntimeFactory.create_market_client`` now makes the runtime
fit for each new client on the owner thread: it starts an unstarted runtime,
performs a due renewal, and only before its first client replaces an expired
registration with a fresh session (``recover()``). After the first client an
expired lease stays a reported failure.

INTEGRATION_SIM: real ``ServiceHost``/``ServiceRegistry`` and real
``ServiceClientRuntime`` on loopback CURVE ZMQ with a temp SQLite registry and a
synthetic native MD client. Time is advanced through the host registry clock and
the client runtime's monotonic clock (no sleeping); no real CTP front, account
or network.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("zmq")

from bt_api_ctp.feeds import live_ctp_feed

from bt_api_py import service_host
from bt_api_py.bt_api import BtApi
from bt_api_py.ctp_gateway_composition import (
    build_ctp_gateway_market_host,
    create_gateway_market_runtime,
    ctp_gateway_instrument_key,
)
from bt_api_py.market_stream_contracts import (
    MarketStreamError,
    MarketStreamRequest,
    MarketStreamState,
)
from bt_api_py.service_registry import ServiceRegistry
from bt_api_py.service_runtime import ServiceRuntimeError
from tests.integration.test_gateway_operator_ctp_market import _SyntheticMdClient
from tests.integration.test_iter62_gateway_ctp_market_profile import (
    _RecordingMdClient,
)

_ROUTE = "CTP___FUTURE"
# Beyond the host registry's default 30 s lease (and the client's local
# deadline, which is anchored before the registration request was sent).
_PAST_LEASE_SECONDS = 45.0
# Past the 5 s heartbeat renewal point, well inside the 30 s lease.
_RENEWAL_DUE_SECONDS = 10.0


class _Clocks:
    """Offsets added to the host registry wall clock and the client monotonic clock."""

    def __init__(self) -> None:
        self.host_offset = 0.0
        self.client_offset = 0.0

    def advance(self, seconds: float) -> None:
        self.host_offset += seconds
        self.client_offset += seconds


def _shift_host_registry_clock(monkeypatch: pytest.MonkeyPatch, clocks: _Clocks) -> None:
    real_registry = ServiceRegistry

    def registry(*args: Any, **kwargs: Any) -> ServiceRegistry:
        assert "clock" not in kwargs
        return real_registry(*args, clock=lambda: time.time() + clocks.host_offset, **kwargs)

    monkeypatch.setattr(service_host, "ServiceRegistry", registry)


def _shift_client_clock(monkeypatch: pytest.MonkeyPatch, runtime: Any, clocks: _Clocks) -> None:
    monkeypatch.setattr(
        runtime, "_monotonic_clock", lambda: time.monotonic() + clocks.client_offset
    )


def _record_control_calls(monkeypatch: pytest.MonkeyPatch, runtime: Any) -> list[tuple]:
    """Record ``(operation, registration_id, outcome code)`` of every runtime RPC."""

    calls: list[tuple] = []
    original = runtime._rpc

    def spy(service_id, transport_ref, scope, operation, payload, **kwargs):
        registration_id = payload.get("registration_id")
        try:
            result = original(service_id, transport_ref, scope, operation, payload, **kwargs)
        except ServiceRuntimeError as error:
            calls.append((operation, registration_id, error.code))
            raise
        calls.append((operation, registration_id, "OK"))
        return result

    monkeypatch.setattr(runtime, "_rpc", spy)
    return calls


def _count_recoveries(monkeypatch: pytest.MonkeyPatch, runtime: Any) -> list[int]:
    count = [0]
    original = runtime.recover

    def recover():
        count[0] += 1
        return original()

    monkeypatch.setattr(runtime, "recover", recover)
    return count


@contextmanager
def _gateway_hub(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    clocks: _Clocks,
    symbols: tuple[str, ...] = ("rb2701",),
) -> Iterator[Any]:
    _SyntheticMdClient.instances.clear()
    _RecordingMdClient.fronts = []
    monkeypatch.setattr(live_ctp_feed.ctp_client, "MdClient", _RecordingMdClient)
    monkeypatch.setenv("CTP_USER_ID", "synthetic-user")
    monkeypatch.setenv("CTP_BROKER_ID", "9999")
    monkeypatch.setenv("CTP_PASSWORD", "synthetic-password")
    _shift_host_registry_clock(monkeypatch, clocks)

    profile, connection = build_ctp_gateway_market_host(
        ctp_profile="set1_group1_vpn",
        instruments=[(symbol, "SHFE") for symbol in symbols],
        registry_path=tmp_path / "gateway-market-registry.sqlite3",
    )
    deployment = profile.deployment_factory()
    started: queue.Queue = queue.Queue()
    stop = threading.Event()

    def serve() -> None:
        try:
            deployment.start()
            started.put("started")
            while not stop.is_set():
                deployment.serve_once(10)
        except BaseException as exc:
            started.put(getattr(exc, "code", type(exc).__name__))
        finally:
            if deployment.state not in {"closed", "failed"}:
                with suppress(BaseException):
                    deployment.close()

    owner = threading.Thread(target=serve, name="iter62-gateway-lease", daemon=True)
    owner.start()
    try:
        assert started.get(timeout=15) == "started"
        yield connection
    finally:
        stop.set()
        owner.join(10)
    assert not owner.is_alive()
    assert deployment.state == "closed"


def _facade(factory: Any) -> BtApi:
    return BtApi(
        exchange_kwargs={
            _ROUTE: {
                "broker_id": "9999",
                "user_id": "synthetic-user",
                "password": "synthetic-password",
                "td_front": "tcp://127.0.0.1:1",
                "md_front": "tcp://127.0.0.1:1",
                "ctp_env_profile": "config_front_pair",
            }
        },
        market_runtime_factory=factory,
    )


def _request(symbol: str) -> MarketStreamRequest:
    return MarketStreamRequest(
        exchange_name=_ROUTE,
        instrument=ctp_gateway_instrument_key(symbol, "SHFE"),
        channel="tick",
        depth=None,
        queue_capacity=64,
    )


def _assert_tick_delivered(stream: Any, subscription: Any, symbol: str) -> None:
    (client,) = _SyntheticMdClient.instances
    client.emit_tick(symbol)
    deadline = time.monotonic() + 5.0
    result = None
    while time.monotonic() < deadline:
        result = stream.poll(subscription, timeout_ms=50)
        if result.events:
            break
    assert result is not None and result.events, result
    assert result.state is MarketStreamState.ACTIVE
    assert result.events[-1].data.instrument == ctp_gateway_instrument_key(symbol, "SHFE")


def _close(api: Any, runtime: Any) -> None:
    if api is not None:
        with suppress(Exception):
            api.close()
    if runtime is not None:
        with suppress(Exception):
            runtime.close()


@pytest.mark.integration
def test_first_subscription_recovers_a_lease_that_expired_on_the_host(
    monkeypatch, tmp_path: Path
) -> None:
    """Case A: the lease expires (host and client) before the first subscription."""

    clocks = _Clocks()
    with _gateway_hub(monkeypatch, tmp_path, clocks) as connection:
        runtime = api = None
        try:
            runtime, factory = create_gateway_market_runtime(connection)
            assert runtime.state == "registered"
            _shift_client_clock(monkeypatch, runtime, clocks)
            (expired,) = runtime.bindings
            old_session = runtime.client_session_id
            old_transports = tuple(runtime._transports.values())
            api = _facade(factory)
            stream = api.market_stream()
            calls = _record_control_calls(monkeypatch, runtime)

            # Store and broker start outlast the lease; nothing renews it.
            clocks.advance(_PAST_LEASE_SECONDS)
            assert runtime.healthy is False

            subscription = stream.subscribe(_request("rb2701"))

            # The host had expired the registration: its unregister was
            # acknowledged as NOT_REGISTERED, then a fresh session registered.
            unregister = ("service.unregister", expired.registration_id, "NOT_REGISTERED")
            assert unregister in calls
            register = calls.index(("service.register", None, "OK"))
            assert calls.index(unregister) < register
            assert runtime.state == "registered"
            assert runtime.healthy is True
            assert runtime.client_session_id != old_session
            (fresh,) = runtime.bindings
            assert fresh.registration_id != expired.registration_id
            # recover() released cleanly: no retained transport or registration.
            assert runtime.cleanup_failures == 0
            assert runtime.unregister_uncertainties == 0
            assert len(runtime._transports) == 1
            assert not set(map(id, runtime._transports.values())) & set(map(id, old_transports))

            _assert_tick_delivered(stream, subscription, "rb2701")
            assert stream.unsubscribe(subscription) is True
        finally:
            _close(api, runtime)


@pytest.mark.integration
def test_first_subscription_starts_an_unstarted_runtime(monkeypatch, tmp_path: Path) -> None:
    """Case B: a caller may hand over an unstarted runtime."""

    clocks = _Clocks()
    with _gateway_hub(monkeypatch, tmp_path, clocks) as connection:
        runtime = api = None
        try:
            runtime, factory = create_gateway_market_runtime(connection, start=False)
            assert runtime.state == "new"
            assert runtime.bindings == ()
            api = _facade(factory)
            stream = api.market_stream()
            calls = _record_control_calls(monkeypatch, runtime)

            subscription = stream.subscribe(_request("rb2701"))

            operations = [operation for operation, _registration, _code in calls]
            assert operations.index("service.register") < operations.index("market.subscribe")
            assert ("service.register", None, "OK") in calls
            assert runtime.state == "registered"
            assert len(runtime.bindings) == 1
            _assert_tick_delivered(stream, subscription, "rb2701")
            assert stream.unsubscribe(subscription) is True
        finally:
            _close(api, runtime)


@pytest.mark.integration
def test_lease_expiry_after_the_first_client_is_not_recovered(monkeypatch, tmp_path: Path) -> None:
    """Case C: once a client exists, an expired lease is a reported failure."""

    clocks = _Clocks()
    with _gateway_hub(monkeypatch, tmp_path, clocks, symbols=("rb2701", "hc2701")) as connection:
        runtime = api = None
        try:
            runtime, factory = create_gateway_market_runtime(connection)
            _shift_client_clock(monkeypatch, runtime, clocks)
            api = _facade(factory)
            stream = api.market_stream()
            first = stream.subscribe(_request("rb2701"))
            (binding,) = runtime.bindings
            session = runtime.client_session_id
            recoveries = _count_recoveries(monkeypatch, runtime)
            factory_errors: list[BaseException] = []
            create = factory.create_market_client

            def recording_create(route, source_binding):
                try:
                    return create(route, source_binding)
                except BaseException as error:
                    factory_errors.append(error)
                    raise

            monkeypatch.setattr(factory, "create_market_client", recording_create)

            clocks.advance(_PAST_LEASE_SECONDS)
            with pytest.raises(MarketStreamError) as raised:
                stream.subscribe(_request("hc2701"))

            assert raised.value.code == "MARKET_STREAM_PROJECTOR_UNAVAILABLE"
            (error,) = factory_errors
            assert type(error) is ServiceRuntimeError
            assert error.code == "REQUIRED_LEASE_EXPIRED"
            assert recoveries[0] == 0
            assert runtime.state == "failed"
            assert runtime.client_session_id == session
            assert runtime.bindings[0].registration_id == binding.registration_id
            # The existing stream observes the failure instead of a new binding.
            failed = stream.poll(first, timeout_ms=0)
            assert failed.state is not MarketStreamState.ACTIVE
            # A retry is still not recovered.
            with pytest.raises(MarketStreamError):
                stream.subscribe(_request("hc2701"))
            assert recoveries[0] == 0
            assert runtime.state == "failed"
        finally:
            _close(api, runtime)


@pytest.mark.integration
def test_due_renewal_is_performed_before_a_new_client(monkeypatch, tmp_path: Path) -> None:
    """Case D: a renewal that is due when a client is created is performed first."""

    clocks = _Clocks()
    with _gateway_hub(monkeypatch, tmp_path, clocks) as connection:
        runtime = api = None
        try:
            runtime, factory = create_gateway_market_runtime(connection)
            _shift_client_clock(monkeypatch, runtime, clocks)
            (before,) = runtime.bindings
            session = runtime.client_session_id
            api = _facade(factory)
            stream = api.market_stream()
            calls = _record_control_calls(monkeypatch, runtime)
            windows: list[tuple[int, int, Any]] = []
            create = factory.create_market_client

            def recording_create(route, source_binding):
                start = len(calls)
                client = create(route, source_binding)
                windows.append((start, len(calls), runtime.bindings))
                return client

            monkeypatch.setattr(factory, "create_market_client", recording_create)

            clocks.advance(_RENEWAL_DUE_SECONDS)
            assert runtime.next_maintenance_delay() == 0.0
            assert runtime.healthy is True

            subscription = stream.subscribe(_request("rb2701"))

            ((start, end, bindings),) = windows
            # The renewal ran inside create_market_client, before the client
            # existed (a client renews only while it is polled).
            assert calls[start:end] == [("service.renew", before.registration_id, "OK")]
            (renewed,) = bindings
            assert renewed.registration_id == before.registration_id
            assert renewed.lease_deadline > before.lease_deadline
            assert renewed.renewal_due_at > before.renewal_due_at
            assert runtime.client_session_id == session
            _assert_tick_delivered(stream, subscription, "rb2701")
            assert stream.unsubscribe(subscription) is True
        finally:
            _close(api, runtime)
