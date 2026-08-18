"""Feed adapter contract tests (Task 2.2)."""

from __future__ import annotations

from decimal import Decimal

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py._feed_adapter import FeedAdapter
from bt_api_py._venue_mappers.binance import map_order_request


def test_feed_adapter_routes_mapped_args_to_feed() -> None:
    calls: list[dict] = []

    class _SpyFeed:
        def make_order(self, *args, **kwargs) -> str:
            calls.append({"args": args, "kwargs": kwargs})
            return "order-1"

    feed = _SpyFeed()
    adapter = FeedAdapter(feed, map_order_request)
    request = OrderRequest(
        symbol="BTCUSDT",
        side=Side.SELL,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.5"),
        price=Decimal("42000"),
        account_id="paper",
        client_order_id="cid-9",
    )

    result = adapter.make_order(request)

    assert result == "order-1"
    assert len(calls) == 1
    symbol, vol, price, order_type = calls[0]["args"]
    assert symbol == "BTCUSDT"
    assert vol == 0.5
    assert price == 42000.0
    assert order_type == "sell-limit"
    assert calls[0]["kwargs"]["client_order_id"] == "cid-9"


def test_feed_adapter_preserves_mapper_side() -> None:
    calls: list[dict] = []

    class _SpyFeed:
        def make_order(self, *args, **kwargs) -> None:
            calls.append({"args": args})

    adapter = FeedAdapter(_SpyFeed(), map_order_request)
    request = OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        price=None,
        account_id="paper",
        client_order_id="cid-1",
    )

    adapter.make_order(request)

    order_type = calls[0]["args"][3]
    assert order_type == "buy-market"
