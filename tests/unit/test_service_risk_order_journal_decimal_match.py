"""Strict decimal-value matching for immutable Risk order journal evidence."""

from decimal import Decimal

import pytest

from bt_api_py.service_risk_order_journal import (
    _decimal_text_matches,
    canonical_json,
    sha256_text,
)


def test_decimal_text_comparison_accepts_scale_without_rewriting_payload() -> None:
    payload = {"quantity": "1", "filled_quantity": "1.0"}
    before = canonical_json(payload)
    before_sha256 = sha256_text(before)

    assert _decimal_text_matches(payload["quantity"], Decimal("1.0"))
    assert _decimal_text_matches(payload["filled_quantity"], Decimal("1"))

    assert canonical_json(payload) == before
    assert sha256_text(canonical_json(payload)) == before_sha256


def test_decimal_text_comparison_requires_exact_numeric_equality() -> None:
    assert _decimal_text_matches("1.000", Decimal("1"))
    assert not _decimal_text_matches("1.0000000000000001", Decimal("1"))
    assert not _decimal_text_matches("0.9999999999999999", Decimal("1"))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("not-a-number", Decimal("1")),
        ("1_0", Decimal("10")),
        ("NaN", Decimal("NaN")),
        ("Infinity", Decimal("1")),
        ("-Infinity", Decimal("-Infinity")),
        (True, Decimal("1")),
        (1, Decimal("1")),
        ("1", True),
        ("1", Decimal("NaN")),
        ("1", Decimal("Infinity")),
    ],
)
def test_decimal_text_comparison_rejects_malformed_nonfinite_and_boolean_values(
    value: object, expected: object
) -> None:
    assert not _decimal_text_matches(value, expected)
