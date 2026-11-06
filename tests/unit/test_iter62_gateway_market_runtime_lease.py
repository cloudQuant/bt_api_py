"""Iteration 62 (62.08): ``GatewayMarketTickRuntimeFactory`` makes its runtime fit.

Before building each ``ServiceMarketClient`` the factory, on its owner thread,
starts a ``"new"`` runtime, maintains a ``"registered"`` one (due renewals),
and recovers an expired required lease (``maintain()`` raising
``REQUIRED_LEASE_EXPIRED`` or a ``"failed"`` runtime) only while it has not
created any client. Every other state or runtime error propagates and no
client is created. The integration counterpart with a real host is
``tests/integration/test_iter62_gateway_market_runtime_lease.py``.

UNIT/FAKE: a real (never started) ``ServiceClientRuntime`` whose ``start``,
``maintain`` and ``recover`` are replaced by recorders; no transport, host,
CTP front or network.
"""

from __future__ import annotations

from typing import Any

import pytest

zmq = pytest.importorskip("zmq")

from bt_api_base.market_protocol import MarketSubscriptionKey  # noqa: E402
from bt_api_base.market_tick_provider_protocol import (  # noqa: E402
    MarketTickSourceBinding,
    SelectedMarketTickRoute,
)

from bt_api_py.ctp_gateway_composition import (  # noqa: E402
    GatewayClientConnection,
    create_gateway_market_runtime,
    ctp_gateway_instrument_key,
)
from bt_api_py.gateway_market_runtime import GatewayMarketRuntimeError  # noqa: E402
from bt_api_py.service_market import ServiceMarketClient  # noqa: E402
from bt_api_py.service_runtime import ServiceRuntimeError  # noqa: E402


class _Adapter:
    def create_subscription_key(
        self, instrument: Any, *, source_ref: str, entitlement_ref: str, channel: str
    ) -> MarketSubscriptionKey:
        return MarketSubscriptionKey(
            source_ref=source_ref,
            entitlement_ref=entitlement_ref,
            instrument=instrument,
            channel=channel,
        )


def _connection() -> GatewayClientConnection:
    server_public, _server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    return GatewayClientConnection(
        rpc_endpoint="tcp://127.0.0.1:1",
        market_endpoint="tcp://127.0.0.1:2",
        source_ref="source:unit-gateway-market",
        entitlement_ref="entitlement:unit-gateway-market",
        server_public_key=server_public,
        client_public_key=client_public,
        client_secret_key=client_secret,
    )


def _route_and_binding(symbol: str = "rb2701") -> tuple[Any, Any]:
    instrument = ctp_gateway_instrument_key(symbol, "SHFE")
    route = SelectedMarketTickRoute(
        exchange_name="CTP___FUTURE", client=object(), instrument=instrument, environment="demo"
    )
    return route, MarketTickSourceBinding(instrument, "tick", _Adapter())


class _Lifecycle:
    """Recorders for ``start``/``maintain``/``recover`` on a real runtime object."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, runtime: Any) -> None:
        self.runtime = runtime
        self.calls: list[str] = []
        self.maintain_error: str | None = None
        self.recover_error: str | None = None
        monkeypatch.setattr(runtime, "start", self._start)
        monkeypatch.setattr(runtime, "maintain", self._maintain)
        monkeypatch.setattr(runtime, "recover", self._recover)

    def _start(self) -> None:
        self.calls.append("start")
        assert self.runtime._state == "new"
        self.runtime._state = "registered"

    def _maintain(self) -> None:
        self.calls.append("maintain")
        if self.runtime._state != "registered":
            raise ServiceRuntimeError("SESSION_NOT_REGISTERED")
        if self.maintain_error is not None:
            self.runtime._state = "failed"
            raise ServiceRuntimeError(self.maintain_error)

    def _recover(self) -> None:
        self.calls.append("recover")
        assert self.runtime._state in {"registered", "failed", "close_failed"}
        if self.recover_error is not None:
            self.runtime._state = "close_failed"
            raise ServiceRuntimeError(self.recover_error)
        self.runtime._state = "registered"
        self.maintain_error = None


def _factory(monkeypatch: pytest.MonkeyPatch, state: str) -> tuple[Any, _Lifecycle]:
    runtime, factory = create_gateway_market_runtime(_connection(), start=False)
    lifecycle = _Lifecycle(monkeypatch, runtime)
    runtime._state = state
    return factory, lifecycle


def test_new_runtime_is_started_by_the_first_client(monkeypatch) -> None:
    factory, lifecycle = _factory(monkeypatch, "new")

    client = factory.create_market_client(*_route_and_binding())

    assert type(client) is ServiceMarketClient
    assert lifecycle.calls == ["start"]
    assert lifecycle.runtime.state == "registered"


def test_registered_runtime_is_maintained_before_each_client(monkeypatch) -> None:
    factory, lifecycle = _factory(monkeypatch, "registered")

    factory.create_market_client(*_route_and_binding("rb2701"))
    factory.create_market_client(*_route_and_binding("hc2701"))

    assert lifecycle.calls == ["maintain", "maintain"]


def test_expired_lease_before_the_first_client_is_recovered(monkeypatch) -> None:
    factory, lifecycle = _factory(monkeypatch, "registered")
    lifecycle.maintain_error = "REQUIRED_LEASE_EXPIRED"

    client = factory.create_market_client(*_route_and_binding())

    assert type(client) is ServiceMarketClient
    assert lifecycle.calls == ["maintain", "recover"]
    assert lifecycle.runtime.state == "registered"


def test_failed_runtime_before_the_first_client_is_recovered(monkeypatch) -> None:
    factory, lifecycle = _factory(monkeypatch, "failed")

    factory.create_market_client(*_route_and_binding())

    assert lifecycle.calls == ["recover"]


@pytest.mark.parametrize(
    "code",
    ["REQUIRED_RENEWAL_FAILED", "LEASE_EXPIRED_DURING_RENEWAL", "NOT_REGISTERED", "STALE_EPOCH"],
)
def test_other_maintenance_failures_propagate(monkeypatch, code: str) -> None:
    factory, lifecycle = _factory(monkeypatch, "registered")
    lifecycle.maintain_error = code

    with pytest.raises(ServiceRuntimeError) as raised:
        factory.create_market_client(*_route_and_binding())

    assert raised.value.code == code
    assert lifecycle.calls == ["maintain"]
    assert factory._clients == {}


def test_expired_lease_after_a_client_exists_is_not_recovered(monkeypatch) -> None:
    factory, lifecycle = _factory(monkeypatch, "registered")
    factory.create_market_client(*_route_and_binding("rb2701"))
    lifecycle.maintain_error = "REQUIRED_LEASE_EXPIRED"

    with pytest.raises(ServiceRuntimeError) as raised:
        factory.create_market_client(*_route_and_binding("hc2701"))
    assert raised.value.code == "REQUIRED_LEASE_EXPIRED"
    assert lifecycle.runtime.state == "failed"
    # A retry on the now failed runtime is not recovered either.
    with pytest.raises(ServiceRuntimeError) as retried:
        factory.create_market_client(*_route_and_binding("hc2701"))

    assert retried.value.code == "SESSION_NOT_REGISTERED"
    assert lifecycle.calls == ["maintain", "maintain", "maintain"]
    assert len(factory._clients) == 1


def test_client_created_flag_survives_a_closed_client(monkeypatch) -> None:
    """The first-client rule does not depend on the client still being tracked."""

    factory, lifecycle = _factory(monkeypatch, "registered")
    factory.create_market_client(*_route_and_binding("rb2701"))
    factory._clients.clear()
    lifecycle.runtime._state = "failed"

    with pytest.raises(ServiceRuntimeError):
        factory.create_market_client(*_route_and_binding("rb2701"))

    assert "recover" not in lifecycle.calls


@pytest.mark.parametrize("state", ["close_failed", "closed", "starting"])
def test_other_runtime_states_propagate_without_recovery(monkeypatch, state: str) -> None:
    factory, lifecycle = _factory(monkeypatch, state)

    with pytest.raises(ServiceRuntimeError) as raised:
        factory.create_market_client(*_route_and_binding())

    assert raised.value.code == "SESSION_NOT_REGISTERED"
    assert lifecycle.calls == ["maintain"]
    assert factory._clients == {}


def test_failed_recovery_propagates_and_creates_no_client(monkeypatch) -> None:
    factory, lifecycle = _factory(monkeypatch, "registered")
    lifecycle.maintain_error = "REQUIRED_LEASE_EXPIRED"
    lifecycle.recover_error = "CLOSE_FAILED"

    with pytest.raises(ServiceRuntimeError) as raised:
        factory.create_market_client(*_route_and_binding())
    assert raised.value.code == "CLOSE_FAILED"
    # Retained transports (close_failed) are never recovered by the factory.
    with pytest.raises(ServiceRuntimeError):
        factory.create_market_client(*_route_and_binding())

    assert lifecycle.calls == ["maintain", "recover", "maintain"]
    assert factory._clients == {}


def test_invalid_binding_is_rejected_before_any_runtime_call(monkeypatch) -> None:
    factory, lifecycle = _factory(monkeypatch, "new")
    route, _binding = _route_and_binding("rb2701")
    _other_route, other_binding = _route_and_binding("hc2701")

    with pytest.raises(GatewayMarketRuntimeError) as raised:
        factory.create_market_client(route, other_binding)

    assert raised.value.code == "GATEWAY_MARKET_BINDING_INVALID"
    assert lifecycle.calls == []
    assert lifecycle.runtime.state == "new"


def test_composition_start_flag_is_an_exact_bool() -> None:
    runtime, _factory_ = create_gateway_market_runtime(_connection(), start=False)
    assert runtime.state == "new"
    with pytest.raises(TypeError):
        create_gateway_market_runtime(_connection(), start=1)  # type: ignore[arg-type]
