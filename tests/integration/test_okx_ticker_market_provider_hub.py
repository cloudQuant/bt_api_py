"""Synthetic OKX ticker queue through the normal service-v2 Market Hub writer."""

from __future__ import annotations

from typing import Any

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.market_hub import GatewayMarketHub
from bt_api_okx.containers.tickers import OkxTickerData
from bt_api_okx.market_provider import BoundedOkxTickerQueue, OkxTickerQueueMarketProvider


class _SyntheticOkxTickerStream:
    def __init__(self, queue: BoundedOkxTickerQueue) -> None:
        self.queue = queue
        self.start_calls: list[float] = []
        self.stop_calls = 0

    def start(self, *, connect_timeout: float) -> None:
        self.start_calls.append(connect_timeout)

    def stop(self) -> None:
        self.stop_calls += 1


def _ticker(symbol: str) -> OkxTickerData:
    return OkxTickerData(
        {
            "instType": "SWAP",
            "instId": symbol,
            "last": "30000",
            "bidPx": "29999",
            "askPx": "30001",
            "bidSz": "3",
            "askSz": "4",
            "vol24h": "12.5",
            "volCcy24h": "375000",
            "open24h": "29800",
            "high24h": "30500",
            "low24h": "29000",
            "ts": "1597026383085",
        },
        symbol,
        "SWAP",
        has_been_json_encoded=True,
    )


@pytest.mark.integration
def test_okx_ticker_queue_publishes_through_the_service_market_event_contract() -> None:
    key = MarketSubscriptionKey(
        source_ref="okx-public-market-r1",
        entitlement_ref="public-ticker-r1",
        instrument=MarketInstrumentKey(
            provider="okx",
            venue="OKX",
            environment="live",
            product_type="SWAP",
            instrument_id="BTC-USDT-SWAP",
            native_symbol="BTC-USDT-SWAP",
        ),
        channel="tickers",
    )
    streams: list[_SyntheticOkxTickerStream] = []

    def stream_factory(
        _key: MarketSubscriptionKey, queue: BoundedOkxTickerQueue
    ) -> _SyntheticOkxTickerStream:
        stream = _SyntheticOkxTickerStream(queue)
        streams.append(stream)
        return stream

    provider = OkxTickerQueueMarketProvider(stream_factory=stream_factory)
    published: list[Any] = []
    hub = GatewayMarketHub(
        provider,
        key_universe=(key,),
        principal_allowed_keys={"principal:quote": (key,)},
        service_identity=WireIdentity(
            WireRole.SERVICE,
            principal_ref="service:market",
            actor_ref="gateway:market-hub",
        ),
        service_scope=WireScope("domain:market-read"),
    )
    hub.set_publisher(published.append)
    hub.subscribe("principal:quote", key, "registration:quote-session")
    streams[0].queue.put(_ticker("BTC-USDT-SWAP"))

    hub.maintain({"registration:quote-session"})

    (event,) = published
    assert streams[0].start_calls == [30.0]
    assert event.header.schema_id == "sdk.market.event"
    assert event.header.operation == "market.event"
    assert event.header.sent_at_utc == "2020-08-10T02:26:23.085000Z"
    assert event.payload["key"] == key.to_dict()
    assert event.payload["data"] == {
        "exchange_name": "OKX",
        "symbol": "BTC-USDT-SWAP",
        "instrument_id": "BTC-USDT-SWAP",
        "asset_type": "SWAP",
        "timestamp": 1597026383.085,
        "price": 30000.0,
        "last_price": 30000.0,
        "bid_price": 29999.0,
        "ask_price": 30001.0,
        "bid_volume": 3.0,
        "ask_volume": 4.0,
        "volume": 12.5,
        "turnover": 375000.0,
        "high": 30500.0,
        "low": 29000.0,
        "open": 29800.0,
    }
    assert "generation" not in event.payload
    assert dict(hub.source_diagnostics)["provider_events_published"] == 1
    hub.unsubscribe("principal:quote", key, "registration:quote-session")
    assert streams[0].stop_calls == 1
