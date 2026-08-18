"""Order validation boundary tests for the v1 contract (Task 1.1).

These lock the pre-trade validation that must fail *before* any exchange
adapter is called (FR-02).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bt_api_py._contracts.models import OrderRequest, OrderType, Side


def test_order_request_requires_side() -> None:
    with pytest.raises(TypeError):
        OrderRequest(  # type: ignore[call-arg]
            symbol="BTCUSDT",
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("1"),
            account_id="paper",
            client_order_id="cid-1",
        )


def test_order_request_rejects_non_positive_quantity() -> None:
    with pytest.raises(ValueError):
        OrderRequest(
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0"),
            price=Decimal("1"),
            account_id="paper",
            client_order_id="cid-1",
        )


def test_order_request_rejects_limit_without_price() -> None:
    with pytest.raises(ValueError):
        OrderRequest(
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=None,
            account_id="paper",
            client_order_id="cid-1",
        )


def test_order_request_rejects_market_with_price() -> None:
    with pytest.raises(ValueError):
        OrderRequest(
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
            price=Decimal("50000"),
            account_id="paper",
            client_order_id="cid-1",
        )


def test_order_request_rejects_float_quantity() -> None:
    with pytest.raises(TypeError):
        OrderRequest(
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.001,  # type: ignore[arg-type]
            price=Decimal("1"),
            account_id="paper",
            client_order_id="cid-1",
        )


def test_order_request_rejects_float_price() -> None:
    with pytest.raises(TypeError):
        OrderRequest(
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=50000.0,  # type: ignore[arg-type]
            account_id="paper",
            client_order_id="cid-1",
        )


def test_order_request_rejects_empty_account_id() -> None:
    with pytest.raises(ValueError):
        OrderRequest(
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("1"),
            account_id="",
            client_order_id="cid-1",
        )


def test_order_request_rejects_empty_client_order_id() -> None:
    with pytest.raises(ValueError):
        OrderRequest(
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("1"),
            account_id="paper",
            client_order_id="",
        )


def test_market_order_allows_none_price() -> None:
    req = OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        price=None,
        account_id="paper",
        client_order_id="cid-1",
    )
    assert req.price is None
