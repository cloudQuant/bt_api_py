"""Private event cache snapshots preserve scope and freshness metadata."""

from __future__ import annotations

import pytest

from bt_api_py._contracts.errors import StaleDataUnavailableError
from bt_api_py._contracts.models import (
    AccountSnapshot,
    Consistency,
    FillSnapshot,
    ForwardingConfig,
    OrderSnapshot,
    PositionSnapshot,
)
from bt_api_py.forwarding.btapi_backend import ZmqBtApiBackend
from bt_api_py.forwarding.client import ForwardingClient
from bt_api_py.forwarding.memory import InMemoryForwardingBus
from bt_api_py.forwarding.schema import PrivateEvent


def _backend(bus: InMemoryForwardingBus) -> tuple[ZmqBtApiBackend, ForwardingClient]:
    backend = ZmqBtApiBackend(
        ForwardingConfig(
            command_endpoint="inproc://commands",
            market_endpoint="inproc://market",
            private_endpoint="inproc://private",
            account_id="acct-1",
            strategy_id="strategy-1",
            max_cache_age_ms=500,
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
    return backend, client


def test_cache_ok_private_events_map_to_contract_snapshots() -> None:
    bus = InMemoryForwardingBus()
    backend, client = _backend(bus)
    client.connect()
    bus.publish_private(
        PrivateEvent(
            event_type="balances",
            account_id="acct-1",
            strategy_id="strategy-1",
            payload={"kind": "account", "cash": 10, "equity": 12, "currency": "USDT"},
        )
    )
    bus.publish_private(
        PrivateEvent(
            event_type="positions",
            account_id="acct-1",
            strategy_id="strategy-1",
            payload={"kind": "position", "symbol": "BTC-USDT", "quantity": 2, "average_price": 100},
        )
    )
    bus.publish_private(
        PrivateEvent(
            event_type="orders",
            account_id="acct-1",
            strategy_id="strategy-1",
            payload={
                "kind": "order",
                "order_id": "o1",
                "symbol": "BTC-USDT",
                "side": "buy",
                "order_type": "limit",
                "size": 2,
                "price": 100,
                "filled": 1,
                "status": "submitted",
            },
        )
    )
    bus.publish_private(
        PrivateEvent(
            event_type="trades",
            account_id="acct-1",
            strategy_id="strategy-1",
            payload={
                "kind": "trade",
                "trade_id": "t1",
                "order_id": "o1",
                "symbol": "BTC-USDT",
                "side": "buy",
                "size": 1,
                "price": 100,
                "fee": 0.1,
            },
        )
    )

    account = backend.get_account("SIM___SPOT", consistency=Consistency.CACHE_OK)
    positions = backend.get_position("SIM___SPOT", consistency=Consistency.CACHE_OK)
    orders = backend.get_open_orders("SIM___SPOT", consistency=Consistency.CACHE_OK)
    deals = backend.get_deals("SIM___SPOT", consistency=Consistency.CACHE_OK)

    assert isinstance(account, AccountSnapshot)
    assert account.freshness.source == "cache"
    assert account.freshness.stale is True
    assert isinstance(positions[0], PositionSnapshot)
    assert isinstance(orders[0], OrderSnapshot)
    assert isinstance(deals[0], FillSnapshot)
    assert str(deals[0].fee) == "0.1"


def test_private_cache_miss_never_fabricates_an_empty_result() -> None:
    backend, _client = _backend(InMemoryForwardingBus())

    with pytest.raises(StaleDataUnavailableError, match="get_open_orders"):
        backend.get_open_orders("SIM___SPOT", consistency=Consistency.CACHE_OK)
