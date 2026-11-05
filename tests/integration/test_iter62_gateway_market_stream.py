"""Iteration 62 (62.08): ``BtApi.market_stream()`` serves CTP ticks from the Gateway.

With ``BtApi(market_runtime_factory=GatewayMarketTickRuntimeFactory(...))`` the
facade's stream owner must take ticks from the Gateway MarketHub (one exact
``ServiceMarketClient`` per instrument) and never open an in-process CTP market
session. Before this change three defects made the path unusable:

* the stream owner preferred the in-process CTP provider whenever one was
  registered, so the configured Gateway runtime was never used;
* ``ServiceMarketTickProjector`` compared the Base payload's freshness value
  (``unavailable``) with the service projection's (``UNAVAILABLE``) and
  rejected every real Gateway tick;
* its stream identity passed the inner bootstrap as the start receipt, so no
  AVAILABLE identity (required by the stream owner) could ever be issued.

INTEGRATION_SIM: Gateway host on a daemon thread, loopback CURVE, synthetic
native MD client; the facade's own CTP route points at a closed loopback port.
"""

from __future__ import annotations

import queue
import threading
import time
from contextlib import suppress
from pathlib import Path

import pytest

pytest.importorskip("zmq")

from bt_api_ctp.feeds import live_ctp_feed  # noqa: E402

from bt_api_py.bt_api import BtApi  # noqa: E402
from bt_api_py.ctp_gateway_composition import (  # noqa: E402
    build_ctp_gateway_market_host,
    create_gateway_market_runtime,
    ctp_gateway_instrument_key,
)
from bt_api_py.market_stream_contracts import (  # noqa: E402
    MarketStreamRequest,
    MarketStreamState,
    market_stream_identity_matches,
    market_stream_poll_result_valid,
)
from tests.integration.test_gateway_operator_ctp_market import _SyntheticMdClient  # noqa: E402
from tests.integration.test_iter62_gateway_ctp_market_profile import (  # noqa: E402
    _RecordingMdClient,
)

_ROUTE = "CTP___FUTURE"


@pytest.mark.integration
def test_facade_market_stream_serves_gateway_ticks(monkeypatch, tmp_path: Path) -> None:
    _SyntheticMdClient.instances.clear()
    _RecordingMdClient.fronts = []
    monkeypatch.setattr(live_ctp_feed.ctp_client, "MdClient", _RecordingMdClient)
    monkeypatch.setenv("CTP_USER_ID", "synthetic-user")
    monkeypatch.setenv("CTP_BROKER_ID", "9999")
    monkeypatch.setenv("CTP_PASSWORD", "synthetic-password")

    profile, connection = build_ctp_gateway_market_host(
        ctp_profile="set1_group1_vpn",
        instruments=[("rb2701", "SHFE")],
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

    owner = threading.Thread(target=serve, name="iter62-gateway-stream", daemon=True)
    owner.start()
    runtime = api = None
    try:
        assert started.get(timeout=15) == "started"
        runtime, factory = create_gateway_market_runtime(connection)
        api = BtApi(
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
        qualified: list = []

        def record_qualification(exchange_name, event):
            # The SDK clock qualification hook sees every Gateway tick (here it
            # has no verified clock evidence and returns the tick unchanged).
            qualified.append(exchange_name)
            return event

        monkeypatch.setattr(api, "_qualify_market_tick", record_qualification)
        stream = api.market_stream()
        instrument = ctp_gateway_instrument_key("rb2701", "SHFE")
        request = MarketStreamRequest(
            exchange_name=_ROUTE,
            instrument=instrument,
            channel="tick",
            depth=None,
            queue_capacity=64,
        )
        subscription = stream.subscribe(request)
        assert subscription.key.channel == "tick"
        assert subscription.key.source_ref == connection.source_ref
        identity = stream.stream_identity(subscription)
        assert market_stream_identity_matches(identity, subscription, "tick")

        # The only native MD session is the Gateway hub's, on the SimNow
        # profile's front; the facade opened no in-process market session.
        assert _RecordingMdClient.fronts == ["tcp://182.254.243.31:30011"]
        (client,) = _SyntheticMdClient.instances
        client.emit_tick("rb2701")
        deadline = time.monotonic() + 5.0
        result = None
        while time.monotonic() < deadline:
            result = stream.poll(subscription, timeout_ms=50)
            if result.events:
                break
        assert result is not None and result.events, result
        assert market_stream_poll_result_valid(result, subscription, "tick", pump_only=False)
        assert result.state is MarketStreamState.ACTIVE
        assert qualified and set(qualified) == {_ROUTE}
        (event,) = result.events
        assert event.data.instrument == instrument
        assert event.data.last_price == 3_050.0
        # Clock facts are kept: unverified, not execution eligible.
        assert event.data.source_clock_quality.value == "unknown"
        assert event.data.execution_eligible is False
        assert stream.unsubscribe(subscription) is True
    finally:
        if api is not None:
            with suppress(Exception):
                api.close()
        if runtime is not None:
            with suppress(Exception):
                runtime.close()
        stop.set()
        owner.join(10)
    assert not owner.is_alive()
    assert deployment.state == "closed"
