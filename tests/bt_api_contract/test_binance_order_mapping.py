"""Binance order mapping golden tests (Task 2.2)."""

from __future__ import annotations

from decimal import Decimal

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py._venue_mappers.binance import map_order_request


def _order(**overrides: object) -> OrderRequest:
    kwargs: dict[str, object] = {
        "symbol": "BTCUSDT",
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
    assert result["symbol"] == "BTCUSDT"
    assert result["vol"] == 0.001
    assert result["price"] == 50000.0


def test_sell_market_reduce_only_maps_offset_close() -> None:
    result = map_order_request(
        _order(side=Side.SELL, order_type=OrderType.MARKET, price=None, reduce_only=True)
    )
    assert result["order_type"] == "sell-market"
    assert result["offset"] == "close"
    assert result["price"] is None


def test_price_never_leaks_into_side() -> None:
    result = map_order_request(_order(price=Decimal("50000")))
    assert "50000" not in result["order_type"]
    assert result["order_type"] == "buy-limit"


def test_client_order_id_is_preserved() -> None:
    result = map_order_request(_order(client_order_id="my-id"))
    assert result["client_order_id"] == "my-id"
