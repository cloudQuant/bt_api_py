"""Order validation boundary tests for the v1 contract (Task 1.1).

These lock the pre-trade validation that must fail *before* any exchange
adapter is called (FR-02).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from bt_api_py._contracts.models import OrderRequest, OrderType, Side

_replace_order_request = replace


def _valid_order_request() -> OrderRequest:
    return OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Decimal("1"),
        account_id="paper",
        client_order_id="cid-1",
    )


def _assert_order_request_rejected(
    overrides: dict[str, object],
    exception_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(exception_type) as exc_info:
        _replace_order_request(_valid_order_request(), **overrides)
    assert type(exc_info.value) is exception_type
    assert str(exc_info.value) == message


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


@pytest.mark.parametrize(
    "quantity",
    [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")],
    ids=["zero", "negative", "nan", "infinity"],
)
def test_order_request_rejects_non_positive_or_non_finite_quantity(quantity: Decimal) -> None:
    _assert_order_request_rejected(
        {"quantity": quantity},
        ValueError,
        "quantity must be > 0",
    )


def test_order_request_rejects_limit_without_price() -> None:
    _assert_order_request_rejected({"price": None}, ValueError, "limit order requires a price")


def test_order_request_rejects_market_with_price() -> None:
    _assert_order_request_rejected(
        {"order_type": OrderType.MARKET},
        ValueError,
        "market order must not carry a price",
    )


def test_order_request_rejects_float_quantity() -> None:
    _assert_order_request_rejected({"quantity": 0.001}, TypeError, "quantity must be a Decimal")


def test_order_request_rejects_float_price() -> None:
    _assert_order_request_rejected({"price": 50000.0}, TypeError, "price must be a Decimal or None")


def test_order_request_rejects_empty_account_id() -> None:
    _assert_order_request_rejected(
        {"account_id": ""}, ValueError, "account_id must be a non-empty string"
    )


def test_order_request_rejects_empty_client_order_id() -> None:
    _assert_order_request_rejected(
        {"client_order_id": ""},
        ValueError,
        "client_order_id must be a non-empty string",
    )


@pytest.mark.parametrize(
    "overrides, exception_type, message",
    [
        pytest.param({"symbol": ""}, ValueError, "symbol must be a non-empty string", id="symbol"),
        pytest.param({"side": "buy"}, TypeError, "side must be a Side enum", id="side"),
        pytest.param(
            {"order_type": "limit"},
            TypeError,
            "order_type must be an OrderType enum",
            id="order-type",
        ),
        pytest.param({"price": "1"}, TypeError, "price must be a Decimal or None", id="price-type"),
        pytest.param(
            {"quantity_unit": "units"},
            ValueError,
            "quantity_unit must be base, contracts, lots or native",
            id="quantity-unit",
        ),
        pytest.param(
            {"offset": "close_invalid"},
            ValueError,
            "offset must be open, close, close_today or close_yesterday",
            id="offset",
        ),
        pytest.param(
            {"position_mode": "hedge"},
            ValueError,
            "position_mode must be net or dual_side",
            id="position-mode",
        ),
        pytest.param(
            {"execution_role": "recovery"},
            ValueError,
            "execution_role must be entry, exit, recovery_exit or None",
            id="execution-role",
        ),
    ],
)
def test_order_request_rejects_invalid_enum_like_fields(
    overrides: dict[str, object], exception_type: type[Exception], message: str
) -> None:
    _assert_order_request_rejected(overrides, exception_type, message)


@pytest.mark.parametrize(
    "price",
    [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")],
    ids=["zero", "negative", "nan", "infinity"],
)
def test_order_request_rejects_non_positive_or_non_finite_price(price: Decimal) -> None:
    _assert_order_request_rejected({"price": price}, ValueError, "price must be finite and > 0")


@pytest.mark.parametrize(
    "execution_cycle_id",
    [3, "", " ", " cycle-1 ", "x" * 129],
    ids=["non-string", "empty", "whitespace", "padded", "too-long"],
)
def test_order_request_rejects_invalid_execution_cycle_id(execution_cycle_id: object) -> None:
    _assert_order_request_rejected(
        {"execution_cycle_id": execution_cycle_id},
        ValueError,
        "execution_cycle_id must be a bounded non-empty string or None",
    )


@pytest.mark.parametrize(
    "strategy_identity_sha256",
    [123, "a" * 63, "A" * 64, "g" * 64],
    ids=["non-string", "wrong-length", "uppercase", "non-hex"],
)
def test_order_request_rejects_invalid_strategy_identity_digest(
    strategy_identity_sha256: object,
) -> None:
    _assert_order_request_rejected(
        {"strategy_identity_sha256": strategy_identity_sha256},
        ValueError,
        "strategy_identity_sha256 must be a lowercase SHA-256 hex digest or None",
    )


@pytest.mark.parametrize(
    "overrides, message",
    [
        pytest.param(
            {"symbol": "", "offset": "invalid"},
            "symbol must be a non-empty string",
            id="symbol-before-offset",
        ),
        pytest.param(
            {"quantity_unit": "invalid", "account_id": ""},
            "quantity_unit must be base, contracts, lots or native",
            id="quantity-unit-before-account",
        ),
        pytest.param(
            {"strategy_identity_sha256": "invalid", "price": None},
            "strategy_identity_sha256 must be a lowercase SHA-256 hex digest or None",
            id="digest-before-limit-price",
        ),
    ],
)
def test_order_request_validation_preserves_field_priority(
    overrides: dict[str, object], message: str
) -> None:
    _assert_order_request_rejected(overrides, ValueError, message)


def test_order_request_preserves_native_type_error_for_unhashable_offset() -> None:
    _assert_order_request_rejected({"offset": []}, TypeError, "unhashable type: 'list'")


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
