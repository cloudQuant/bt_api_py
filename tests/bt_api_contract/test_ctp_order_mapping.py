"""CTP order mapping golden tests (Task 2.2).

The projection is provider-owned (``bt_api_ctp.order_request_mapping``); the
SDK only discovers it through the ``bt_api.order_request_projections`` entry
point.  CTP is a Chinese futures market with limit orders only, so market
orders and non-day validity must be rejected before any exchange call.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from bt_api_ctp.order_request_mapping import map_order_request

from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._contracts.models import OrderRequest, OrderType, Side


def _order(**overrides: object) -> OrderRequest:
    kwargs: dict[str, object] = {
        "symbol": "rb2510",
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("1"),
        "price": Decimal("3500"),
        "account_id": "paper",
        "client_order_id": "cid-1",
        # CTP accepts good-for-day validity only; the provider projects an
        # unspecified or DAY validity to the native GFD default.
        "time_in_force": "GFD",
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


def test_managed_ctp_identity_stays_on_the_request_not_native_arguments() -> None:
    """Managed CTP identity is provider-owned and not a ``make_order`` argument.

    The old SDK-local mapper serialized ``managed_intent_id``,
    ``runtime_order_id`` and ``hedge_flag`` into the native mapping.  That
    projection now belongs to the CTP provider's reservation/direct-write
    path, so the owner mapper must emit only feed arguments while the typed
    request keeps its identity labels.
    """
    request = _order(
        managed_intent_id="managed-intent-7",
        runtime_order_id="runtime-order-7",
        hedge_flag="2",
    )
    result = map_order_request(request)
    assert result["symbol"] == "rb2510"
    assert "managed_intent_id" not in result
    assert "runtime_order_id" not in result
    assert "hedge_flag" not in result
    assert request.managed_intent_id == "managed-intent-7"
    assert request.runtime_order_id == "runtime-order-7"
    assert request.hedge_flag == "2"


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
