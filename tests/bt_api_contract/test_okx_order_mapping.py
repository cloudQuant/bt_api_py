"""OKX order mapping golden tests (Task 2.2)."""

from __future__ import annotations

from decimal import Decimal

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
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
