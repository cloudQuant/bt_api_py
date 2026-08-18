"""BtApi typed order contract tests (Task 2.3)."""

from __future__ import annotations

from decimal import Decimal

from bt_api_py import BtApi
from bt_api_py._contracts.models import OrderRequest, OrderType, Side


def _api_with_spy_feed() -> tuple[BtApi, list[tuple]]:
    api = BtApi(None, debug=False)
    calls: list[tuple] = []

    class _SpyFeed:
        def make_order(self, *args, **kwargs):
            calls.append((args, kwargs))
            return "order-1"

    api.exchange_feeds["MOCK___SPOT"] = _SpyFeed()
    api.data_queues["MOCK___SPOT"] = object()
    return api, calls


def test_make_order_accepts_typed_request() -> None:
    api, calls = _api_with_spy_feed()
    request = OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.001"),
        price=Decimal("50000"),
        account_id="paper",
        client_order_id="cid-1",
    )
    result = api.make_order("MOCK___SPOT", request)
    assert result == "order-1"
    assert len(calls) == 1
    args, _ = calls[0]
    assert args[3] == "buy-limit"


def test_typed_sell_market_maps_side() -> None:
    api, calls = _api_with_spy_feed()
    request = OrderRequest(
        symbol="BTCUSDT",
        side=Side.SELL,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        price=None,
        account_id="paper",
        client_order_id="cid-2",
    )
    api.make_order("MOCK___SPOT", request)
    args, _ = calls[0]
    assert args[3] == "sell-market"
