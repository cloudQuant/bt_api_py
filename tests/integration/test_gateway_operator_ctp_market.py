"""Synthetic CTP native callback through the normal ServiceMarket path."""

from __future__ import annotations

import queue
import threading
import time
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_ctp.ctp.client import CtpNativeStopReceipt
from bt_api_ctp.feeds import live_ctp_feed
from bt_api_ctp.feeds.live_ctp_feed import CtpMarketStream
from bt_api_ctp.market_provider import BoundedCtpQuoteV2Queue, CtpQuoteV2MarketProvider

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

_SOURCE_REF = "ctp-native-market-r1"
_ENTITLEMENT_REF = "ctp-quote-v2-entitlement-r1"
_CTP_KEY = MarketSubscriptionKey(
    source_ref=_SOURCE_REF,
    entitlement_ref=_ENTITLEMENT_REF,
    instrument=MarketInstrumentKey(
        provider="ctp",
        venue="CFFEX",
        environment="simnow",
        product_type="future",
        instrument_id="IF2612",
        native_symbol="IF2612",
    ),
    channel="ctp.quote.v2",
)
_CTP_KEY_SECOND = MarketSubscriptionKey(
    source_ref=_SOURCE_REF,
    entitlement_ref=_ENTITLEMENT_REF,
    instrument=MarketInstrumentKey(
        provider="ctp",
        venue="CFFEX",
        environment="simnow",
        product_type="future",
        instrument_id="IH2612",
        native_symbol="IH2612",
    ),
    channel="ctp.quote.v2",
)


class _SyntheticMdClient:
    instances: list[_SyntheticMdClient] = []

    def __init__(self, *_args: object) -> None:
        self.connection_generation = 1
        self.on_login = None
        self.on_tick = None
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.stop_calls = 0
        self.stop_wait_calls = 0
        self.__class__.instances.append(self)

    def subscribe(self, instruments: list[str]) -> None:
        self.subscribed.extend(instruments)

    def subscribe_additional(self, instruments: list[str]) -> tuple[str, ...]:
        additions = tuple(item for item in instruments if item not in self.subscribed)
        self.subscribed.extend(additions)
        return additions

    def unsubscribe(self, instruments: list[str]) -> bool:
        self.unsubscribed.extend(instruments)
        self.subscribed = [item for item in self.subscribed if item not in instruments]
        return True

    def start(self, *, block: bool = False) -> None:
        assert block is False
        assert callable(self.on_login)
        self.on_login(SimpleNamespace())

    def emit_tick(self, symbol: str = "IF2612") -> None:
        assert callable(self.on_tick)
        self.on_tick(
            SimpleNamespace(
                InstrumentID=symbol,
                ExchangeID="CFFEX",
                LastPrice=4_001.0,
                PreSettlementPrice=3_990.0,
                OpenPrice=3_995.0,
                HighestPrice=4_010.0,
                LowestPrice=3_980.0,
                BidPrice1=4_000.0,
                BidVolume1=2,
                AskPrice1=4_002.0,
                AskVolume1=3,
                OpenInterest=1_000.0,
                Volume=123,
                Turnover=492_000.0,
                UpperLimitPrice=4_200.0,
                LowerLimitPrice=3_800.0,
                TradingDay="20261017",
                ActionDay="20261017",
                UpdateTime="09:30:01",
                UpdateMillisec=250,
            )
        )

    def stop(self) -> None:
        self.stop_calls += 1

    def stop_and_wait(self, *, timeout: float) -> CtpNativeStopReceipt:
        assert timeout > 0
        self.stop_wait_calls += 1
        return CtpNativeStopReceipt(
            connection_generation=self.connection_generation,
            join_required=False,
            join_completed=True,
            native_released=True,
            thread_alive=False,
            timed_out=False,
        )


def _synthetic_stream(
    _key: MarketSubscriptionKey, callback_queue: BoundedCtpQuoteV2Queue
) -> CtpMarketStream:
    # The patched MD client is the only connection leaf. Explicit synthetic
    # settings also prevent process-level CTP config from being consulted.
    return CtpMarketStream(
        callback_queue,
        broker_id="synthetic-broker",
        user_id="synthetic-user",
        password="synthetic-password",
        auth_code="synthetic-auth-code",
        app_id="synthetic-app",
        td_front="tcp://127.0.0.1:1",
        md_front="tcp://127.0.0.1:1",
        ctp_env_profile="config_front_pair",
        ctp_environment="simnow",
    )


def _poll_market_event(market: ServiceMarketClient):
    deadline = time.monotonic() + 5.0
    event = None
    while time.monotonic() < deadline and event is None:
        event = market.poll(timeout_ms=25)
    assert event is not None
    return event


@pytest.mark.integration
def test_ctp_quote_v2_callback_publishes_through_operator_host_and_service_market(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _SyntheticMdClient.instances.clear()
    monkeypatch.setattr(live_ctp_feed.ctp_client, "MdClient", _SyntheticMdClient)

    config = parse_service_config(_market_service_config(_free_endpoint(), _free_endpoint()))
    server_keys = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    authority, service_identity = _market_authority(config, server_keys, client_public)
    streams: list[CtpMarketStream] = []

    def stream_factory(key: MarketSubscriptionKey, callback_queue: BoundedCtpQuoteV2Queue):
        stream = _synthetic_stream(key, callback_queue)
        streams.append(stream)
        return stream

    binding = GatewayMarketBinding(
        key_universe=(_CTP_KEY,),
        principal_allowed_keys={_PRINCIPAL_REF: frozenset({_CTP_KEY})},
        provider_factory=lambda: CtpQuoteV2MarketProvider(
            stream_factory=stream_factory,
            source_ref=_SOURCE_REF,
            entitlement_ref=_ENTITLEMENT_REF,
            connect_timeout_seconds=1.0,
            stop_timeout_seconds=3.0,
        ),
    )
    deployment = GatewayOperatorDeployment(
        config,
        authority,
        tmp_path / "operator-ctp-market-registry.sqlite3",
        resource_provider=lambda *_args: pytest.fail("market role must use operator factory"),
        market_binding=GatewayMarketBinding(
            key_universe=(_CTP_KEY, _CTP_KEY_SECOND),
            principal_allowed_keys={
                _PRINCIPAL_REF: frozenset({_CTP_KEY, _CTP_KEY_SECOND})
            },
            provider_factory=binding.provider_factory,
        ),
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

    owner = threading.Thread(target=serve_host, name="gateway-ctp-market-host", daemon=True)
    owner.start()
    runtime: ServiceClientRuntime | None = None
    market: ServiceMarketClient | None = None
    market_second: ServiceMarketClient | None = None
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
        market = ServiceMarketClient(
            runtime,
            "store.market",
            _CTP_KEY,
            subscriber_factory=ZmqMarketTransportFactory({_SECURITY_REF: client_security}),
            stream_scope=WireScope(_DOMAIN_REF),
            head_check_interval_seconds=1.0,
            require_source_descriptor=True,
        )
        first_bootstrap = market.start()
        assert first_bootstrap.source_descriptor is not None

        market_second = ServiceMarketClient(
            runtime,
            "store.market",
            _CTP_KEY_SECOND,
            subscriber_factory=ZmqMarketTransportFactory({_SECURITY_REF: client_security}),
            stream_scope=WireScope(_DOMAIN_REF),
            head_check_interval_seconds=1.0,
            require_source_descriptor=True,
        )
        second_bootstrap = market_second.start()
        assert second_bootstrap.source_descriptor is not None

        assert len(streams) == 1
        (client,) = _SyntheticMdClient.instances
        assert client.subscribed == ["IF2612", "IH2612"]
        scope = streams[0].get_quote_clock_source_scope()
        assert scope is not None
        first_descriptor = market.source_descriptor
        second_descriptor = market_second.source_descriptor
        assert first_descriptor is not None and second_descriptor is not None
        assert first_bootstrap.source_descriptor == first_descriptor
        assert first_descriptor.key == _CTP_KEY
        assert second_descriptor.key == _CTP_KEY_SECOND
        assert first_descriptor.provider == second_descriptor.provider == "ctp"
        assert first_descriptor.source == second_descriptor.source == "ctp.native.md"
        assert first_descriptor.stream_id == _CTP_KEY.stream_id
        assert second_descriptor.stream_id == _CTP_KEY_SECOND.stream_id
        assert first_descriptor.epoch == first_bootstrap.cursor_epoch
        assert second_descriptor.epoch == second_bootstrap.cursor_epoch
        assert first_descriptor.clock_domain_id == second_descriptor.clock_domain_id
        assert (
            first_descriptor.clock_source_session_id
            == second_descriptor.clock_source_session_id
        )
        assert first_descriptor.connection_generation == second_descriptor.connection_generation
        client.emit_tick()
        client.emit_tick("IH2612")
        event = _poll_market_event(market)
        event_second = _poll_market_event(market_second)

        assert event.header.schema_id == "sdk.market.event"
        assert event.header.operation == "market.event"
        assert event.payload["key"] == _CTP_KEY.to_dict()
        assert event.payload["data"]["schema_version"] == "ctp.quote.v2"
        assert event.payload["data"]["exchange_id"] == "CFFEX"
        assert event.payload["data"]["instrument_id"] == "IF2612"
        assert event.payload["data"]["symbol"] == "IF2612"
        assert event.payload["data"]["asset_type"] == "future"
        assert event.payload["data"]["last_price"] == 4_001.0
        assert event.payload["data"]["source"] == "ctp.native.md"
        assert event.payload["data"]["clock_domain_id"] == scope.clock_domain_id
        assert (
            event.payload["data"]["clock_source_session_id"]
            == scope.clock_source_session_id
        )
        assert event.payload["data"]["clock_source"] == scope.clock_source
        assert event.payload["data"]["source_clock_quality"] == "unknown"
        assert event.payload["data"]["receive_clock_quality"] == "unknown"
        assert event.payload["data"]["freshness_verified"] is False
        assert event.payload["data"]["execution_eligible"] is False
        assert event.payload["data"]["execution_qualified"] is False
        assert event_second.payload["key"] == _CTP_KEY_SECOND.to_dict()
        assert event_second.payload["data"]["symbol"] == "IH2612"
        assert event_second.payload["data"]["clock_domain_id"] == scope.clock_domain_id

        market.close()
        market = None
        assert client.stop_calls == 0
        assert client.unsubscribed == ["IF2612"]
        assert market_second.healthy is True
        market_second.close()
        market_second = None
        assert client.stop_calls == 1
        assert client.stop_wait_calls == 1
        runtime.close()
        runtime = None
        deadline = time.monotonic() + 2.0
        while (
            deployment.summary["market"]["active_registrations"] != 0
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert deployment.summary["market"]["active_registrations"] == 0
    finally:
        if market is not None:
            with suppress(Exception):
                market.close()
        if market_second is not None:
            with suppress(Exception):
                market_second.close()
        if runtime is not None:
            with suppress(Exception):
                runtime.close()
        stop.set()
        owner.join(10)
        assert not owner.is_alive(), "operator Host owner thread did not close"

    assert deployment.state == "closed"
    assert _SyntheticMdClient.instances[0].stop_calls == 1
    assert _SyntheticMdClient.instances[0].stop_wait_calls == 1
