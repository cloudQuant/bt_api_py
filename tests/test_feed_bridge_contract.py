"""FeedBrokerAdapter contract tests (Task 3.2)."""

from __future__ import annotations

import pytest

from bt_api_py import BtApi
from bt_api_py.brokers.feed_bridge import FeedBrokerAdapter
from bt_api_py.brokers.types import OrderRequest as BrokerOrderRequest


def _api_with_spy_feed() -> tuple[BtApi, dict]:
    api = BtApi(None, debug=False)
    calls: dict[str, list] = {"make_order": [], "get_account": []}

    class _SpyFeed:
        def make_order(self, *args, **kwargs):
            calls["make_order"].append((args, kwargs))
            return {"order_id": "o1", "symbol": args[0], "status": "submitted"}

        def get_account(self, symbol="ALL", **kwargs):
            calls["get_account"].append(symbol)
            return {"account_id": "paper", "cash": 100.0, "equity": 200.0}

        def get_position(self, symbol=None, **kwargs):
            return [{"symbol": "BTCUSDT", "quantity": 0.5, "average_price": 42000.0}]

        def get_open_orders(self, symbol=None, **kwargs):
            return [{"order_id": "o1", "symbol": "BTCUSDT", "status": "submitted"}]

        def get_tick(self, symbol, **kwargs):
            return {"symbol": symbol, "price": 42000.0}

    api.exchange_feeds["MOCK___SPOT"] = _SpyFeed()
    api.data_queues["MOCK___SPOT"] = object()
    return api, calls


def _adapter(api: BtApi) -> FeedBrokerAdapter:
    return FeedBrokerAdapter(api, exchange_name="MOCK___SPOT", account_id="paper")


@pytest.mark.asyncio
async def test_feed_broker_adapter_gets_account_from_bt_api() -> None:
    api, calls = _api_with_spy_feed()
    adapter = _adapter(api)

    account = await adapter.get_account("paper")

    assert calls["get_account"] == ["ALL"]
    assert account.account_id == "paper"
    assert account.cash == 100.0


@pytest.mark.asyncio
async def test_feed_broker_adapter_place_order_routes_through_bt_api() -> None:
    api, calls = _api_with_spy_feed()
    adapter = _adapter(api)

    request = BrokerOrderRequest(
        account_id="paper",
        symbol="BTCUSDT",
        side="buy",
        quantity=0.001,
        order_type="limit",
        price=50000.0,
        client_order_id="c1",
    )
    result = await adapter.place_order(request)

    assert result.order_id == "o1"
    assert len(calls["make_order"]) == 1
    args, _ = calls["make_order"][0]
    assert args[3] == "buy-limit", "broker order must map side-type through BtApi"


@pytest.mark.asyncio
async def test_feed_broker_adapter_capabilities_support_orders() -> None:
    api, _ = _api_with_spy_feed()
    adapter = _adapter(api)

    caps = adapter.capabilities()

    assert caps.supports_order_submit is True
    assert caps.supports_order_cancel is True
