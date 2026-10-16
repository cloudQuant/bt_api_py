"""Binance public Demo ticker provider in the normal Gateway ServiceHost path."""

from __future__ import annotations

import queue
import threading
import time
from contextlib import suppress
from pathlib import Path

import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_binance.containers.tickers.binance_ticker import BinanceWssTickerData
from bt_api_binance.market_provider import (
    BinanceUsdMDemoMarketProvider,
    BoundedBinanceTickerQueue,
)

from bt_api_py.gateway_operator import GatewayMarketBinding, GatewayOperatorDeployment
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost
from bt_api_py.service_market import ServiceMarketClient, ZmqMarketTransportFactory
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from tests.integration.test_gateway_operator_deployment import (
    _ACCOUNT_REF,
    _DOMAIN_REF,
    _MARKET_OPERATIONS,
    _PRINCIPAL_REF,
    _SECURITY_REF,
    _STRATEGY_REF,
    _free_endpoint,
    _market_authority,
    _market_client_config,
    _market_service_config,
)

_BINANCE_KEY = MarketSubscriptionKey(
    source_ref="binance-usdm-demo-market-r1",
    entitlement_ref="public-ticker-r1",
    instrument=MarketInstrumentKey(
        provider="binance",
        venue="BINANCE",
        environment="demo",
        product_type="USDM_PERPETUAL",
        instrument_id="BTCUSDT",
        native_symbol="BTCUSDT",
    ),
    channel="tickers",
)


class _SyntheticBinanceStream:
    def __init__(self, sink: BoundedBinanceTickerQueue) -> None:
        self.sink = sink
        self.started = False
        self.stop_calls = 0

    def start(self, *, connect_timeout: float) -> None:
        assert connect_timeout == 1.0
        self.started = True

    def emit(self) -> None:
        self.sink.put(
            BinanceWssTickerData(
                {
                    "e": "24hrTicker",
                    "E": int(time.time() * 1_000),
                    "s": "BTCUSDT",
                    "c": "100.25",
                    "b": "100.00",
                    "a": "100.50",
                    "B": "2.0",
                    "A": "3.0",
                    "v": "500.0",
                    "q": "50000.0",
                    "o": "90.0",
                    "h": "110.0",
                    "l": "80.0",
                    "x": "95.0",
                    "Q": "1.0",
                    "p": "10.25",
                    "P": "11.3",
                },
                "BTCUSDT",
                "SWAP",
                has_been_json_encoded=True,
            )
        )

    def stop(self) -> None:
        self.stop_calls += 1


@pytest.mark.integration
def test_binance_demo_ticker_uses_generic_operator_binding_and_recovers_subscription(
    tmp_path: Path,
) -> None:
    config = parse_service_config(_market_service_config(_free_endpoint(), _free_endpoint()))
    server_keys = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    authority, service_identity = _market_authority(config, server_keys, client_public)
    streams: list[_SyntheticBinanceStream] = []

    def stream_factory(_key: MarketSubscriptionKey, sink: BoundedBinanceTickerQueue):
        stream = _SyntheticBinanceStream(sink)
        streams.append(stream)
        return stream

    binding = GatewayMarketBinding(
        key_universe=(_BINANCE_KEY,),
        principal_allowed_keys={_PRINCIPAL_REF: frozenset({_BINANCE_KEY})},
        provider_factory=lambda: BinanceUsdMDemoMarketProvider(
            stream_factory=stream_factory, connect_timeout=1.0
        ),
    )
    deployment = GatewayOperatorDeployment(
        config,
        authority,
        tmp_path / "operator-binance-market-registry.sqlite3",
        resource_provider=lambda *_args: pytest.fail("market role must use operator factory"),
        market_binding=binding,
    )
    assert isinstance(deployment.service_host, ServiceHost)

    startup: queue.Queue[tuple[str, object, object]] = queue.Queue()
    stop = threading.Event()

    def serve_host() -> None:
        try:
            deployment.start()
            startup.put(("started", deployment.endpoint, deployment.market_endpoint))
            while not stop.is_set():
                deployment.serve_once(10)
        except BaseException as exc:
            startup.put(("error", getattr(exc, "code", "HOST_START_FAILED"), None))
        finally:
            if deployment.state not in {"closed", "failed"}:
                with suppress(BaseException):
                    deployment.close()

    owner = threading.Thread(target=serve_host, name="gateway-binance-demo-host", daemon=True)
    owner.start()
    runtime: ServiceClientRuntime | None = None
    market: ServiceMarketClient | None = None
    try:
        startup_result = startup.get(timeout=10)
        assert startup_result[0] == "started", startup_result
        rpc_endpoint, market_endpoint = startup_result[1], startup_result[2]
        assert isinstance(rpc_endpoint, str) and isinstance(market_endpoint, str)

        client_config = parse_client_config(_market_client_config(rpc_endpoint, market_endpoint))
        client_security = ClientCurveKeys(server_keys[0], client_public, client_secret)
        runtime = ServiceClientRuntime(
            client_config,
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF),
            scope_resolver=lambda _ref: WireScope(
                _DOMAIN_REF, account_ref=_ACCOUNT_REF, strategy_ref=_STRATEGY_REF
            ),
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory({_SECURITY_REF: client_security}),
            requested_operations={"store.market": _MARKET_OPERATIONS},
        )
        runtime.start()

        def start_market_client() -> ServiceMarketClient:
            client = ServiceMarketClient(
                runtime,
                "store.market",
                _BINANCE_KEY,
                subscriber_factory=ZmqMarketTransportFactory({_SECURITY_REF: client_security}),
                stream_scope=WireScope(_DOMAIN_REF),
                head_check_interval_seconds=1.0,
            )
            client.start()
            return client

        market = start_market_client()
        assert len(streams) == 1 and streams[0].started
        streams[0].emit()
        event = _poll_market_event(market)
        assert event.header.schema_id == "sdk.market.event"
        assert event.header.operation == "market.event"
        assert event.payload["key"] == _BINANCE_KEY.to_dict()
        assert event.payload["data"]["exchange_name"] == "BINANCE"
        assert event.payload["data"]["environment"] == "demo"
        assert event.payload["data"]["product_type"] == "USDM_PERPETUAL"
        assert event.payload["data"]["symbol"] == "BTCUSDT"
        assert event.payload["data"]["last_price"] == 100.25

        market.close()
        market = None
        assert streams[0].stop_calls == 1
        market = start_market_client()
        assert len(streams) == 2 and streams[1].started
        streams[1].emit()
        recovered_event = _poll_market_event(market)
        assert recovered_event.payload["key"] == _BINANCE_KEY.to_dict()
        assert recovered_event.payload["data"]["last_price"] == 100.25
        assert deployment.summary["market"]["active_registrations"] == 1
    finally:
        if market is not None:
            with suppress(Exception):
                market.close()
        if runtime is not None:
            with suppress(Exception):
                runtime.close()
        stop.set()
        owner.join(10)
        assert not owner.is_alive(), "operator Host owner thread did not close"

    assert deployment.state == "closed"
    assert [stream.stop_calls for stream in streams] == [1, 1]


def _poll_market_event(market: ServiceMarketClient):
    deadline = time.monotonic() + 5.0
    event = None
    while time.monotonic() < deadline and event is None:
        event = market.poll(timeout_ms=25)
    assert event is not None
    return event
