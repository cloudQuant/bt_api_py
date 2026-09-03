"""Bounded, non-consuming ZMQ market snapshot contract tests."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from bt_api_py._contracts.errors import LiveQueryFailedError, StaleDataUnavailableError
from bt_api_py._contracts.models import (
    Consistency,
    DepthSnapshot,
    ForwardingConfig,
    KlineSnapshot,
    TickerSnapshot,
)
from bt_api_py.forwarding.btapi_backend import ZmqBtApiBackend
from bt_api_py.forwarding.client import ForwardingClient
from bt_api_py.forwarding.hub import MarketDataHub
from bt_api_py.forwarding.memory import InMemoryForwardingBus
from bt_api_py.forwarding.schema import MarketEvent, now_ms


def _backend(
    bus: InMemoryForwardingBus, *, timeout_ms: int = 80, max_age_ms: int = 500
) -> tuple[ZmqBtApiBackend, ForwardingClient, MarketDataHub]:
    backend = ZmqBtApiBackend(
        ForwardingConfig(
            command_endpoint="inproc://commands",
            market_endpoint="inproc://market",
            private_endpoint="inproc://private",
            account_id="acct-1",
            strategy_id="strategy-1",
            market_read_timeout_ms=timeout_ms,
            max_cache_age_ms=max_age_ms,
        )
    )
    client = ForwardingClient(
        bus=bus,
        exchange="SIM",
        market_type="SPOT",
        account_id="acct-1",
        strategy_id="strategy-1",
    )
    backend._client = client
    return backend, client, MarketDataHub(bus)


def test_peek_latest_market_event_does_not_consume_poll_queue() -> None:
    bus = InMemoryForwardingBus()
    _backend_instance, client, hub = _backend(bus)
    client.subscribe("BTC-USDT")
    hub.publish_tick(exchange="SIM", market_type="SPOT", symbol="BTC-USDT", price=101.25, volume=2)

    latest = client.peek_tick_event("BTC-USDT")

    assert latest is not None
    assert latest.payload["price"] == 101.25
    assert client.poll_tick("BTC-USDT").price == 101.25


def test_cache_ok_maps_latest_tick_depth_and_bar_to_typed_snapshots() -> None:
    bus = InMemoryForwardingBus()
    backend, client, hub = _backend(bus)
    client.subscribe("BTC-USDT")
    hub.publish_tick(
        exchange="SIM",
        market_type="SPOT",
        symbol="BTC-USDT",
        price=101.25,
        volume=2,
        payload={"vendor": "fixture"},
    )
    hub.publish_orderbook(
        exchange="SIM",
        market_type="SPOT",
        symbol="BTC-USDT",
        bids=[(101.0, 3.0)],
        asks=[(102.0, 4.0)],
    )
    hub.publish_bar(
        exchange="SIM",
        market_type="SPOT",
        symbol="BTC-USDT",
        open_price=100.0,
        high=103.0,
        low=99.0,
        close=101.0,
        volume=8.0,
        payload={"period": "1m"},
    )

    tick = backend.get_tick("SIM___SPOT", "BTC-USDT", consistency=Consistency.CACHE_OK)
    depth = backend.get_depth("SIM___SPOT", "BTC-USDT", consistency=Consistency.CACHE_OK)
    bar = backend.get_kline("SIM___SPOT", "BTC-USDT", "1m", consistency=Consistency.CACHE_OK)

    assert isinstance(tick, TickerSnapshot)
    assert str(tick.last_price) == "101.25"
    assert tick.freshness.source == "cache"
    assert tick.freshness.stale is True
    assert tick.raw["vendor"] == "fixture"
    assert isinstance(depth, DepthSnapshot)
    assert depth.bids[0] == (Decimal("101"), Decimal("3"))
    assert isinstance(bar, KlineSnapshot)
    assert bar.period == "1m"
    assert str(bar.close) == "101.0"


def test_live_requires_an_event_published_after_the_call() -> None:
    bus = InMemoryForwardingBus()
    backend, _client, hub = _backend(bus, timeout_ms=150)
    hub.publish_tick(exchange="SIM", market_type="SPOT", symbol="BTC-USDT", price=100)

    timer = threading.Timer(
        0.03,
        lambda: hub.publish_tick(exchange="SIM", market_type="SPOT", symbol="BTC-USDT", price=102),
    )
    timer.start()
    try:
        snapshot = backend.get_tick("SIM___SPOT", "BTC-USDT", consistency=Consistency.LIVE)
    finally:
        timer.join()

    assert isinstance(snapshot, TickerSnapshot)
    assert str(snapshot.last_price) == "102"
    assert snapshot.freshness.source == "live"
    assert snapshot.freshness.stale is False


def test_live_timeout_and_stale_cache_are_distinct_domain_errors() -> None:
    bus = InMemoryForwardingBus()
    backend, client, hub = _backend(bus, timeout_ms=10, max_age_ms=1)
    client.subscribe("BTC-USDT")
    hub.publish(
        MarketEvent(
            event_type="tick",
            exchange="SIM",
            market_type="SPOT",
            symbol="BTC-USDT",
            payload={"price": 100},
            event_time=now_ms() - 10_000,
            receive_time=now_ms() - 10_000,
        )
    )

    with pytest.raises(StaleDataUnavailableError, match="get_tick"):
        backend.get_tick("SIM___SPOT", "BTC-USDT", consistency=Consistency.CACHE_OK)
    with pytest.raises(LiveQueryFailedError, match=r"get_tick.*BTC-USDT.*timeout"):
        backend.get_tick("SIM___SPOT", "BTC-USDT", consistency=Consistency.LIVE)
