"""The retired ZMQ backend cannot serve market snapshots."""

from __future__ import annotations

import pytest

from bt_api_py._contracts.errors import LegacyForwardingRetiredError
from bt_api_py._contracts.models import ForwardingConfig
from bt_api_py.forwarding.btapi_backend import ZmqBtApiBackend
from bt_api_py.forwarding.client import ForwardingClient
from bt_api_py.forwarding.hub import MarketDataHub
from bt_api_py.forwarding.memory import InMemoryForwardingBus


def test_in_memory_event_peek_does_not_consume_poll_queue() -> None:
    bus = InMemoryForwardingBus()
    client = ForwardingClient(bus=bus, exchange="SIM", market_type="SPOT")
    hub = MarketDataHub(bus)
    client.subscribe("BTC-USDT")
    hub.publish_tick(
        exchange="SIM",
        market_type="SPOT",
        symbol="BTC-USDT",
        price=101.25,
        volume=2,
    )

    latest = client.peek_tick_event("BTC-USDT")

    assert latest is not None
    assert latest.payload["price"] == 101.25
    assert client.poll_tick("BTC-USDT").price == 101.25


def test_market_snapshot_backend_requires_direct_or_managed_service_route() -> None:
    config = ForwardingConfig(
        command_endpoint="inproc://commands",
        market_endpoint="inproc://market",
        private_endpoint="inproc://private",
        account_id="acct-1",
        strategy_id="strategy-1",
    )

    with pytest.raises(LegacyForwardingRetiredError) as excinfo:
        ZmqBtApiBackend(config)

    assert "direct transport" in excinfo.value.detail
    assert "service.market_source" in excinfo.value.detail
