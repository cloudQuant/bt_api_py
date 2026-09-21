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


def _funding_mapping(**overrides: Any) -> dict[str, Any]:
    observed_at = datetime(2026, 9, 8, tzinfo=UTC)
    values: dict[str, Any] = {
        "exchange_name": "OKX___SWAP",
        "symbol": "BTC-USDT-SWAP",
        "rate": Decimal("0.0001"),
        "next_funding_time": observed_at + timedelta(hours=8),
        "settlement_interval_seconds": 28_800,
        "source": "public-funding",
        "freshness": {
            "source": "metadata",
            "observed_at": observed_at,
            "stale": False,
        },
        "available": True,
        "raw": {"fixture": "cross-venue-contract"},
    }
    values.update(overrides)
    return values


class _SourceTrackingMapping(dict[str, Any]):
    def __init__(self, values: dict[str, Any], *, fail_on_source: bool = False) -> None:
        super().__init__(values)
        self.source_reads = 0
        self.fail_on_source = fail_on_source

    def get(self, key: str, default: Any = None) -> Any:
        if key == "source":
            self.source_reads += 1
            if self.fail_on_source:
                raise ValueError("outer source getter failed")
        return super().get(key, default)


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


def test_funding_coercion_rebuilds_a_complete_mapping():
    expected = _funding(raw={"fixture": "cross-venue-contract"})

    result = coerce_funding_snapshot(
        _funding_mapping(),
        now_epoch=Decimal("1788825600"),
        expected_exchange_name="OKX___SWAP",
        expected_symbol="BTC-USDT-SWAP",
    )

    assert result == expected
    assert result is not expected


@pytest.mark.parametrize(
    "freshness",
    (
        _freshness(),
        {
            "source": "metadata",
            "observed_at": datetime(2026, 9, 8, tzinfo=UTC),
            "stale": False,
        },
    ),
)
def test_funding_coercion_reads_outer_source_once_when_freshness_has_its_source(freshness):
    values = _SourceTrackingMapping(_funding_mapping(freshness=freshness))

    coerce_funding_snapshot(values, now_epoch=Decimal("1788825600"))

    assert values.source_reads == 1


def test_funding_coercion_normalizes_value_error_from_constructor_source_read():
    values = _SourceTrackingMapping(
        _funding_mapping(freshness=_freshness()),
        fail_on_source=True,
    )

    with pytest.raises(CrossVenueValueError, match="outer source getter failed"):
        coerce_funding_snapshot(values, now_epoch=Decimal("1788825600"))

    assert values.source_reads == 1


@pytest.mark.parametrize("interval", (True, 0))
def test_funding_coercion_rejects_boolean_or_zero_mapping_interval(interval):
    with pytest.raises(CrossVenueValueError, match="funding_interval_invalid"):
        coerce_funding_snapshot(
            _funding_mapping(settlement_interval_seconds=interval),
            now_epoch=Decimal("1788825600"),
        )


def test_funding_coercion_requires_mapping_freshness():
    values = _funding_mapping()
    values.pop("freshness")

    with pytest.raises(CrossVenueValueError, match="funding_freshness_missing"):
        coerce_funding_snapshot(values, now_epoch=Decimal("1788825600"))


def test_funding_coercion_rejects_naive_mapping_observed_at():
    values = _funding_mapping()
    values["freshness"]["observed_at"] = datetime(2026, 9, 8)

    with pytest.raises(CrossVenueValueError, match="funding_observed_at must be timezone-aware"):
        coerce_funding_snapshot(values, now_epoch=Decimal("1788825600"))


def test_funding_coercion_normalizes_mapping_constructor_type_errors():
    with pytest.raises(CrossVenueValueError, match="rate must be a Decimal or None"):
        coerce_funding_snapshot(
            _funding_mapping(rate="0.0001"),
            now_epoch=Decimal("1788825600"),
        )


def test_funding_coercion_preserves_stale_and_identity_rejection_reasons():
    stale_snapshot = _funding()
    # Deliberately corrupt the frozen object to exercise a state its constructor rejects.
    object.__setattr__(
        stale_snapshot,
        "freshness",
        Freshness(
            source="metadata",
            observed_at=datetime(2026, 9, 8, tzinfo=UTC),
            stale=True,
            stale_reason="fixture_cache_marked_stale",
        ),
    )
    with pytest.raises(CrossVenueValueError, match="fixture_cache_marked_stale"):
        coerce_funding_snapshot(stale_snapshot, now_epoch=Decimal("1788825600"))

    snapshot = _funding()
    with pytest.raises(CrossVenueValueError, match="funding_exchange_name_mismatch"):
        coerce_funding_snapshot(
            snapshot,
            now_epoch=Decimal("1788825600"),
            expected_exchange_name="BINANCE___SWAP",
        )
    with pytest.raises(CrossVenueValueError, match="funding_symbol_mismatch"):
        coerce_funding_snapshot(
            snapshot,
            now_epoch=Decimal("1788825600"),
            expected_symbol="BTCUSDT",
        )


def test_orderbook_evidence_requires_a_complete_delta_chain():
    assert normalize_orderbook_evidence(7, 6, "delta", "continuous") == (
        7,
        6,
        "delta",
        "continuous",
    )
    with pytest.raises(CrossVenueValueError, match="previous_sequence"):
        normalize_orderbook_evidence(7, None, "delta", "continuous")
