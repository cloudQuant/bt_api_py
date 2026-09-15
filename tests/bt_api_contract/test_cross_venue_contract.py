"""Public cross-venue planning contracts consume typed SDK metadata only."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from bt_api_py import (
    CrossVenueLeg,
    CrossVenueValueError,
    FeeSchedule,
    Freshness,
    FundingSnapshot,
    InstrumentSpec,
    coerce_funding_snapshot,
    normalize_orderbook_evidence,
)


def _freshness() -> Freshness:
    return Freshness(source="metadata", observed_at=datetime(2026, 9, 8, tzinfo=UTC))


def _instrument() -> InstrumentSpec:
    return InstrumentSpec(
        exchange_name="OKX___SWAP",
        symbol="BTC-USDT-SWAP",
        asset_type="swap",
        base_currency="BTC",
        quote_currency="USDT",
        contract_type="linear",
        linear=True,
        contract_value=Decimal("0.01"),
        contract_multiplier=Decimal("1"),
        price_tick=Decimal("0.1"),
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
        max_quantity=None,
        min_notional=Decimal("0"),
        quantity_unit="contracts",
        status="live",
        freshness=_freshness(),
        raw_rule_fingerprint="a" * 64,
    )


def _fees(**overrides: Any) -> FeeSchedule:
    values: dict[str, Any] = {
        "exchange_name": "OKX___SWAP",
        "symbol": "BTC-USDT-SWAP",
        "account_id": "demo-account",
        "maker_rate": Decimal("0.0002"),
        "taker_rate": Decimal("0.0005"),
        "currency": "USDT",
        "source": "account-fees",
        "freshness": _freshness(),
    }
    values.update(overrides)
    return FeeSchedule(**values)


def _funding(**overrides: Any) -> FundingSnapshot:
    freshness = _freshness()
    values: dict[str, Any] = {
        "exchange_name": "OKX___SWAP",
        "symbol": "BTC-USDT-SWAP",
        "rate": Decimal("0.0001"),
        "next_funding_time": freshness.observed_at + timedelta(hours=8),
        "settlement_interval_seconds": 28_800,
        "source": "public-funding",
        "freshness": freshness,
    }
    values.update(overrides)
    return FundingSnapshot(**values)


def test_cross_venue_leg_derives_native_contract_rules_from_public_metadata():
    leg = CrossVenueLeg.from_sdk_contracts(_instrument(), _fees(), _funding())

    assert leg.multiplier == Decimal("0.01")
    assert leg.quantity_step == Decimal("1")
    assert leg.base_step == Decimal("0.01")
    assert leg.taker_fee == Decimal("0.0005")
    assert leg.funding_interval_seconds == Decimal("28800")


@pytest.mark.parametrize(
    ("fees", "funding", "reason"),
    (
        (_fees(symbol="BTCUSDT"), _funding(), "fee_symbol_mismatch"),
        (_fees(exchange_name="BINANCE___SWAP"), _funding(), "fee_exchange_name_mismatch"),
        (_fees(), _funding(symbol="BTCUSDT"), "funding_symbol_mismatch"),
        (_fees(), _funding(exchange_name="BINANCE___SWAP"), "funding_exchange_name_mismatch"),
    ),
)
def test_cross_venue_leg_rejects_mixed_venue_contracts(fees, funding, reason):
    with pytest.raises(CrossVenueValueError, match=reason):
        CrossVenueLeg.from_sdk_contracts(_instrument(), fees, funding)


def test_funding_coercion_validates_identity_and_expiry_without_venue_schema():
    snapshot = _funding()

    assert (
        coerce_funding_snapshot(
            snapshot,
            now_epoch=Decimal("1788825600"),
            expected_exchange_name="OKX___SWAP",
            expected_symbol="BTC-USDT-SWAP",
        )
        is snapshot
    )
    with pytest.raises(CrossVenueValueError, match="funding_symbol_mismatch"):
        coerce_funding_snapshot(
            snapshot,
            now_epoch=Decimal("1788825600"),
            expected_symbol="BTCUSDT",
        )
    with pytest.raises(CrossVenueValueError, match="funding_schedule_expired"):
        coerce_funding_snapshot(snapshot, now_epoch=snapshot.next_funding_epoch)


def test_orderbook_evidence_requires_a_complete_delta_chain():
    assert normalize_orderbook_evidence(7, 6, "delta", "continuous") == (
        7,
        6,
        "delta",
        "continuous",
    )
    with pytest.raises(CrossVenueValueError, match="previous_sequence"):
        normalize_orderbook_evidence(7, None, "delta", "continuous")
