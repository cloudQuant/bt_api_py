"""OKX order mapping golden tests (Task 2.2)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py._feed_adapter import FeedAdapter
from bt_api_py._venue_mappers.okx import map_order_request


def _order(**overrides: object) -> OrderRequest:
    kwargs: dict[str, object] = {
        "symbol": "BTC-USDT",
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("0.001"),
        "price": Decimal("50000"),
        "account_id": "paper",
        "client_order_id": "cid-1",
    }
    kwargs.update(overrides)
    return OrderRequest(**kwargs)  # type: ignore[arg-type]


def test_buy_limit_maps_side_and_type() -> None:
    result = map_order_request(_order())
    assert result["order_type"] == "buy-limit"
    assert result["symbol"] == "BTC-USDT"


def test_sell_market_reduce_only_maps_offset_close() -> None:
    result = map_order_request(
        _order(side=Side.SELL, order_type=OrderType.MARKET, price=None, reduce_only=True)
    )
    assert result["order_type"] == "sell-market"
    assert result["offset"] == "close"


def test_price_never_leaks_into_side() -> None:
    result = map_order_request(_order(price=Decimal("50000")))
    assert result["order_type"] == "buy-limit"


def test_post_only_request_reaches_feed_with_native_flags_and_position_intent() -> None:
    calls: list[dict[str, object]] = []

    class _SpyFeed:
        def make_order(self, *args: object, **kwargs: object) -> str:
            calls.append({"args": args, "kwargs": kwargs})
            return "order-1"

    adapter = FeedAdapter(_SpyFeed(), map_order_request)
    request = _order(
        time_in_force="post_only",
        position_mode="dual_side",
        position_side="long",
        offset="open",
    )

    assert adapter.make_order(request) == "order-1"
    assert len(calls) == 1
    args = calls[0]["args"]
    kwargs = calls[0]["kwargs"]
    assert isinstance(args, tuple)
    assert args[3] == "buy-limit"
    assert isinstance(kwargs, dict)
    assert kwargs["post_only"] is True
    assert kwargs["time_in_force"] == "GTC"
    assert kwargs["offset"] == "open"
    assert kwargs["position_side"] == "long"
    assert kwargs["reduce_only"] is False


@pytest.mark.parametrize("time_in_force", ["GTC", "IOC", "FOK"])
def test_regular_time_in_force_values_keep_existing_mapping(time_in_force: str) -> None:
    result = map_order_request(_order(time_in_force=time_in_force))

    assert result["post_only"] is False
    assert result["time_in_force"] == time_in_force
