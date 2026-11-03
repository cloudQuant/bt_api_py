"""Iteration 60 R2-A: every accepted typed tick schema label is classified as typed."""

from __future__ import annotations

from types import SimpleNamespace

from bt_api_py.market_tick_contracts import (
    MARKET_TICK_SCHEMA,
    MARKET_TICK_SCHEMA_VERSION,
    TYPED_MARKET_TICK_SCHEMA_VERSIONS,
    prepare_market_tick_fields,
)


def _tick(schema_version):
    return SimpleNamespace(
        schema_version=schema_version,
        volume_semantics="delta",
        delta_volume=1.0,
        price=10.0,
        bid_price=9.0,
        ask_price=11.0,
        bid_volume=1.0,
        ask_volume=1.0,
        quality_flags=[],
    )


def test_current_and_previous_schema_labels_are_typed() -> None:
    assert TYPED_MARKET_TICK_SCHEMA_VERSIONS == frozenset(
        f"{MARKET_TICK_SCHEMA}.v{version}" for version in range(1, MARKET_TICK_SCHEMA_VERSION + 1)
    )
    for label in sorted(TYPED_MARKET_TICK_SCHEMA_VERSIONS):
        typed, replay, *_ = prepare_market_tick_fields(
            _tick(label), replay_adapter_available=False, legacy_schema="legacy"
        )
        assert typed is True and replay is False


def test_unknown_labels_are_not_typed() -> None:
    for label in ("sdk.market.tick.v99", "sdk.market.tick", "other.v2"):
        typed, *_ = prepare_market_tick_fields(
            _tick(label), replay_adapter_available=False, legacy_schema="legacy"
        )
        assert typed is False


def test_finite_market_number_float_fast_path_matches_the_general_rule() -> None:
    import math

    from bt_api_py.market_tick_contracts import finite_market_number

    def reference(value):
        if value in (None, "") or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(number) or abs(number) >= 1.0e50:
            return None
        return number

    values = [0.0, -0.0, 1.5, -2.25, 1e49, -1e49, 1e50, -1e50, 9.99e49, 1e308,
              math.inf, -math.inf, math.nan, 5e-324, 1, True, None, "", "3.5", "x", 10**400]
    for value in values:
        expected = reference(value)
        actual = finite_market_number(value)
        assert (actual is None) == (expected is None), value
        if expected is not None:
            assert actual == expected and type(actual) is type(expected), value
