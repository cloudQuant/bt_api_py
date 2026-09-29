"""CTP order mapping golden tests (Task 2.2).

CTP is a Chinese futures market with limit orders only; market orders must be
rejected before any exchange call.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py._venue_mappers.ctp import map_order_request


def _order(**overrides: object) -> OrderRequest:
    kwargs: dict[str, object] = {
        "symbol": "rb2510",
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("1"),
        "price": Decimal("3500"),
        "account_id": "paper",
        "client_order_id": "cid-1",
    }
    kwargs.update(overrides)
    return OrderRequest(**kwargs)  # type: ignore[arg-type]


def test_buy_limit_maps_side_and_type() -> None:
    result = map_order_request(_order())
    assert result["order_type"] == "buy-limit"
    assert result["symbol"] == "rb2510"


def test_market_order_rejected_before_exchange_call() -> None:
    with pytest.raises(CapabilityNotSupportedError):
        map_order_request(_order(order_type=OrderType.MARKET, price=None))


def test_reduce_only_maps_offset_close() -> None:
    result = map_order_request(_order(side=Side.SELL, reduce_only=True))
    assert result["offset"] == "close"


def test_managed_ctp_identity_is_serialized_as_typed_fields() -> None:
    result = map_order_request(
        _order(
            managed_intent_id="managed-intent-7",
            runtime_order_id="runtime-order-7",
            hedge_flag="2",
        )
    )
    assert result["managed_intent_id"] == "managed-intent-7"
    assert result["runtime_order_id"] == "runtime-order-7"
    assert result["hedge_flag"] == "2"


@pytest.mark.parametrize(
    "overrides",
    [
        {"managed_intent_id": "managed-intent-7"},
        {
            "managed_intent_id": "managed-intent-7",
            "runtime_order_id": "runtime-order-7",
        },
        {
            "managed_intent_id": "managed-intent-7",
            "runtime_order_id": "runtime-order-7",
            "hedge_flag": "5",
        },
    ],
    ids=["missing-runtime-order-id", "missing-hedge-flag", "unsupported-managed-hedge"],
)
def test_managed_ctp_order_rejects_incomplete_or_unsupported_identity(
    overrides: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        _order(**overrides)
