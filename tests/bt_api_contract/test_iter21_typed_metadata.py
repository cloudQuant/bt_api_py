"""Iteration 21 typed metadata contracts, without network or credentials."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from queue import Queue
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from bt_api_py import FeeSchedule, Freshness, FundingSnapshot, InstrumentSpec
from bt_api_py.bt_api import BtApi


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    instance = BtApi(debug=False)
    yield instance
    instance.close()


def test_okx_instrument_spec_preserves_contract_units_and_decimal_rounding(api):
    venue = "OKX___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(
        get_exchange_info=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "instType": "SWAP",
                        "ctType": "linear",
                        "ctVal": "0.01",
                        "ctMult": "1",
                        "ctValCcy": "BTC",
                        "settleCcy": "USDT",
                        "tickSz": "0.1",
                        "lotSz": "1",
                        "minSz": "1",
                        "state": "live",
                    }
                ],
            }
        )
    )

    spec = api.get_instrument_spec(venue, "BTC-USDT-SWAP")

    assert isinstance(spec, InstrumentSpec) and spec.available
    assert spec.quantity_unit == "contracts"
    assert spec.base_to_native_quantity(Decimal("0.035")) == Decimal("3.5")
    assert spec.quantize_quantity(Decimal("3.5")) == Decimal("3")
    assert spec.native_to_base_quantity(Decimal("3")) == Decimal("0.03")
    assert spec.quantize_price(Decimal("60000.19"), "buy") == Decimal("60000.1")
    assert spec.min_notional == Decimal("0")
    assert spec.raw["min_notional"] == ""
    assert spec.raw_rule_fingerprint


def test_binance_instrument_spec_enforces_step_and_notional_fail_closed(api):
    venue = "BINANCE___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(
        get_exchange_info=Mock(
            return_value={
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "baseAsset": "BTC",
                        "quoteAsset": "USDT",
                        "contractType": "PERPETUAL",
                        "status": "TRADING",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                            {
                                "filterType": "LOT_SIZE",
                                "stepSize": "0.001",
                                "minQty": "0.001",
                                "maxQty": "10",
                            },
                            {"filterType": "MIN_NOTIONAL", "notional": "100"},
                        ],
                    }
                ]
            }
        )
    )

    spec = api.get_instrument_spec(venue, "BTCUSDT")

    assert spec.quantity_unit == "base"
    assert spec.max_quantity == Decimal("10")
    assert spec.quantize_quantity(Decimal("0.0019")) == Decimal("0.001")
    with pytest.raises(ValueError, match="notional_below_minimum"):
        spec.validate_order_quantity(Decimal("0.001"), Decimal("60000"))
    spec.validate_order_quantity(Decimal("0.002"), Decimal("60000"))
    with pytest.raises(ValueError, match="price_required_for_min_notional"):
        spec.validate_order_quantity(Decimal("0.002"))
    with pytest.raises(ValueError, match="quantity_above_maximum"):
        spec.validate_order_quantity(Decimal("10.001"), Decimal("60000"))


def test_missing_rules_return_explicit_unavailable_spec_whose_math_fails_closed(api):
    venue = "BINANCE___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(
        get_exchange_info=Mock(return_value={"symbols": [{"symbol": "BTCUSDT"}]})
    )

    spec = api.get_instrument_spec(venue, "BTCUSDT")

    assert not spec.available and spec.freshness.stale
    assert spec.unavailable_reason
    with pytest.raises(ValueError, match="instrument_spec_unavailable"):
        spec.quantize_quantity(Decimal("1"))


def test_fee_and_funding_contracts_keep_decimal_and_freshness(api):
    venue = "BINANCE___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(
        get_fee=Mock(
            return_value={
                "symbol": "BTCUSDT",
                "makerCommissionRate": "-0.00001",
                "takerCommissionRate": "0.0004",
            }
        ),
        get_funding_rate=Mock(
            return_value={
                "symbol": "BTCUSDT",
                "lastFundingRate": "0.00012345",
                "nextFundingTime": 1_700_000_000_000,
                "fundingIntervalHours": "8",
                "received_wall_time": 1_699_999_000_000,
            }
        ),
    )

    fees = api.get_fee_schedule(venue, "BTCUSDT", "acct-1")
    funding = api.get_funding_snapshot(venue, "BTCUSDT")

    assert isinstance(fees, FeeSchedule) and fees.available
    assert fees.maker_rate == Decimal("0.00001")
    assert fees.taker_rate == Decimal("0.0004")
    assert isinstance(funding, FundingSnapshot) and funding.available
    assert funding.rate == Decimal("0.00012345")
    assert isinstance(funding.next_funding_time, datetime)
    assert funding.next_funding_time.tzinfo is UTC
    assert funding.next_funding_time > funding.freshness.observed_at
    assert funding.settlement_interval_seconds == 28_800


def test_okx_swap_fee_uses_exact_metadata_group_and_nonnegative_costs(api):
    venue = "OKX___SWAP"
    feed = SimpleNamespace(
        get_exchange_info=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "instType": "SWAP",
                        "instFamily": "BTC-USDT",
                        "groupId": "2",
                        "settleCcy": "USDT",
                    }
                ],
            }
        ),
        get_fee=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "instType": "SWAP",
                        "feeGroup": [
                            {
                                "groupId": "1",
                                "maker": "-0.009",
                                "taker": "-0.01",
                            },
                            {
                                "groupId": "2",
                                "maker": "-0.0002",
                                "taker": "-0.0005",
                            },
                        ],
                    }
                ],
            }
        ),
    )
    api.exchange_feeds[venue] = feed

    fees = api.get_fee_schedule(venue, "BTC-USDT-SWAP", "acct-1")

    assert fees.available
    assert fees.maker_rate == Decimal("0.0002")
    assert fees.taker_rate == Decimal("0.0005")
    assert fees.currency == "USDT"
    assert fees.raw["selected_group_id"] == "2"
    call = feed.get_fee.call_args
    assert call.kwargs["inst_type"] == "SWAP"
    assert call.kwargs["group_id"] == "2"
    assert "inst_id" not in call.kwargs
    assert "inst_family" not in call.kwargs


def test_okx_swap_fee_falls_back_to_exact_inst_family_and_legacy_fields(api):
    venue = "OKX___SWAP"
    feed = SimpleNamespace(
        get_exchange_info=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "instType": "SWAP",
                        "instFamily": "BTC-USDT",
                        "settleCcy": "USDT",
                    }
                ],
            }
        ),
        get_fee=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "instType": "SWAP",
                        "maker": "-0.0009",
                        "taker": "-0.001",
                        "makerU": "-0.0002",
                        "takerU": "-0.0005",
                    }
                ],
            }
        ),
    )
    api.exchange_feeds[venue] = feed

    fees = api.get_fee_schedule(venue, "BTC-USDT-SWAP", "acct-1")

    assert fees.available
    assert fees.maker_rate == Decimal("0.0002")
    assert fees.taker_rate == Decimal("0.0005")
    call = feed.get_fee.call_args
    assert call.kwargs["inst_family"] == "BTC-USDT"
    assert "inst_id" not in call.kwargs
    assert "group_id" not in call.kwargs


def test_okx_spot_fee_uses_metadata_standardized_inst_id(api):
    venue = "OKX___SPOT"
    feed = SimpleNamespace(
        get_exchange_info=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT",
                        "instType": "SPOT",
                        "groupId": "1",
                        "quoteCcy": "USDT",
                    }
                ],
            }
        ),
        get_fee=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "instType": "SPOT",
                        "feeGroup": [
                            {
                                "groupId": "1",
                                "maker": "-0.0008",
                                "taker": "-0.001",
                            }
                        ],
                    }
                ],
            }
        ),
    )
    api.exchange_feeds[venue] = feed

    fees = api.get_fee_schedule(venue, "btc/usdt", "acct-1")

    assert fees.available
    call = feed.get_fee.call_args
    assert call.kwargs["inst_id"] == "BTC-USDT"
    assert "inst_family" not in call.kwargs
    assert "group_id" not in call.kwargs


def test_okx_positive_rebate_normalizes_to_zero_cost_with_audit_field(api):
    venue = "OKX___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(
        get_exchange_info=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "instType": "SWAP",
                        "instFamily": "BTC-USDT",
                        "groupId": "2",
                    }
                ],
            }
        ),
        get_fee=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "instType": "SWAP",
                        "feeGroup": [
                            {
                                "groupId": "2",
                                "maker": "0.0001",
                                "taker": "-0.0005",
                            }
                        ],
                    }
                ],
            }
        ),
    )

    fees = api.get_fee_schedule(venue, "BTC-USDT-SWAP", "acct-1")

    assert fees.available
    assert fees.maker_rate == Decimal("0")
    assert fees.taker_rate == Decimal("0.0005")
    assert fees.raw["maker_rebate_rate"] == Decimal("0.0001")
    assert fees.raw["taker_rebate_rate"] == Decimal("0")


def test_okx_fee_group_without_exact_metadata_id_rejects_ambiguity(api):
    venue = "OKX___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(
        get_exchange_info=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "instType": "SWAP",
                        "instFamily": "BTC-USDT",
                    }
                ],
            }
        ),
        get_fee=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "instType": "SWAP",
                        "feeGroup": [
                            {"groupId": "1", "maker": "-0.0002", "taker": "-0.0005"},
                            {"groupId": "2", "maker": "-0.0003", "taker": "-0.0006"},
                        ],
                    }
                ],
            }
        ),
    )

    fees = api.get_fee_schedule(venue, "BTC-USDT-SWAP", "acct-1")

    assert not fees.available
    assert fees.unavailable_reason == "fee_group_ambiguous"
    assert fees.raw == {}


def test_okx_fee_error_50016_is_redacted_parameter_failure(api):
    venue = "OKX___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(
        get_exchange_info=Mock(
            return_value={
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "instType": "SWAP",
                        "instFamily": "BTC-USDT",
                        "groupId": "2",
                    }
                ],
            }
        ),
        get_fee=Mock(
            return_value={
                "code": "50016",
                "msg": "PRIVATE instId does not match instType",
            }
        ),
    )

    fees = api.get_fee_schedule(venue, "BTC-USDT-SWAP", "acct-1")

    assert not fees.available
    assert fees.unavailable_reason == "fee_parameter_error_50016"
    assert fees.freshness.stale_reason == "fee_parameter_error_50016"
    assert fees.raw == {}
    assert "PRIVATE" not in repr(fees)


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"rate": 0.0001}, "rate must be a Decimal"),
        (
            {"next_funding_time": datetime(2026, 1, 1, 8, 0)},
            "next_funding_time must be timezone-aware",
        ),
        (
            {"next_funding_time": datetime(2026, 1, 1, tzinfo=UTC)},
            "strictly after freshness.observed_at",
        ),
        ({"settlement_interval_seconds": None}, "complete funding schedule"),
        ({"settlement_interval_seconds": 0}, "settlement_interval_seconds must be > 0"),
    ],
)
def test_available_funding_snapshot_rejects_invalid_contract_boundaries(overrides, error):
    values: dict[str, Any] = {
        "exchange_name": "BINANCE___SWAP",
        "symbol": "BTCUSDT",
        "rate": Decimal("0.0001"),
        "next_funding_time": datetime(2026, 1, 1, 8, tzinfo=UTC),
        "settlement_interval_seconds": 28_800,
        "source": "binance_funding_rate",
        "freshness": Freshness(
            source="exchange",
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    }
    values.update(overrides)

    with pytest.raises((TypeError, ValueError), match=error):
        FundingSnapshot(**values)


def test_available_funding_snapshot_canonicalizes_aware_time_to_utc():
    snapshot = FundingSnapshot(
        exchange_name="BINANCE___SWAP",
        symbol="BTCUSDT",
        rate=Decimal("0.0001"),
        next_funding_time=datetime(
            2026,
            1,
            1,
            16,
            tzinfo=timezone(timedelta(hours=8)),
        ),
        settlement_interval_seconds=28_800,
        source="binance_funding_rate",
        freshness=Freshness(
            source="exchange",
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )

    assert snapshot.next_funding_time == datetime(2026, 1, 1, 8, tzinfo=UTC)
    assert snapshot.next_funding_time.tzinfo is UTC


@pytest.mark.parametrize(
    ("venue", "row", "expected_next", "expected_interval"),
    [
        (
            "OKX___SWAP",
            {
                "fundingRate": "0.0001",
                "fundingTime": 1_700_000_000_000,
                "nextFundingTime": 1_700_028_800_000,
                "received_wall_time": 1_699_999_000_000,
            },
            datetime.fromtimestamp(1_700_000_000, UTC),
            28_800,
        ),
        (
            "BINANCE___SWAP",
            {
                "symbol": "BTCUSDT",
                "lastFundingRate": "0.0002",
                "nextFundingTime": 1_700_028_800_000,
                "fundingIntervalHours": "4",
                "received_wall_time": 1_700_000_000_000,
            },
            datetime.fromtimestamp(1_700_028_800, UTC),
            14_400,
        ),
    ],
)
def test_funding_snapshot_accepts_only_evidenced_future_schedules(
    api, venue, row, expected_next, expected_interval
):
    api.exchange_feeds[venue] = SimpleNamespace(get_funding_rate=Mock(return_value=row))

    snapshot = api.get_funding_snapshot(venue, "BTCUSDT")

    assert snapshot.available
    assert isinstance(snapshot.rate, Decimal)
    assert snapshot.next_funding_time == expected_next
    assert snapshot.next_funding_time.tzinfo is UTC
    assert snapshot.settlement_interval_seconds == expected_interval


def test_binance_premium_index_selects_exact_symbol_from_out_of_order_rows(api):
    venue = "BINANCE___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(
        get_funding_rate=Mock(
            return_value=[
                {
                    "symbol": "ETHUSDT",
                    "lastFundingRate": "0.0099",
                    "nextFundingTime": 1_700_057_600_000,
                    "fundingIntervalHours": 8,
                    "received_wall_time": 1_700_000_000_000,
                },
                {
                    "symbol": "BTCUSDT",
                    "lastFundingRate": "0.00012345",
                    "nextFundingTime": 1_700_014_400_000,
                    "fundingIntervalHours": 4,
                    "received_wall_time": 1_700_000_000_000,
                },
            ]
        )
    )

    snapshot = api.get_funding_snapshot(venue, "BTC-USDT")

    assert snapshot.available is True
    assert snapshot.symbol == "BTC-USDT"
    assert snapshot.rate == Decimal("0.00012345")
    assert snapshot.next_funding_time == datetime.fromtimestamp(1_700_014_400, UTC)
    assert snapshot.settlement_interval_seconds == 14_400


@pytest.mark.parametrize(
    "premium",
    [
        [
            {
                "symbol": "ETHUSDT",
                "lastFundingRate": "0.0099",
                "nextFundingTime": 1_700_028_800_000,
                "fundingIntervalHours": 8,
            }
        ],
        [
            {
                "lastFundingRate": "0.0001",
                "nextFundingTime": 1_700_028_800_000,
                "fundingIntervalHours": 8,
            }
        ],
        [
            {
                "symbol": "BTCUSDT",
                "lastFundingRate": "0.0001",
                "nextFundingTime": 1_700_028_800_000,
                "fundingIntervalHours": 8,
            },
            {
                "symbol": "BTC-USDT",
                "lastFundingRate": "0.0002",
                "nextFundingTime": 1_700_028_800_000,
                "fundingIntervalHours": 8,
            },
        ],
    ],
    ids=("wrong-symbol", "missing-symbol", "duplicate-symbol"),
)
def test_binance_premium_index_requires_one_exact_symbol_row(api, premium):
    venue = "BINANCE___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(get_funding_rate=Mock(return_value=premium))

    snapshot = api.get_funding_snapshot(venue, "BTCUSDT")

    assert snapshot.available is False
    assert snapshot.unavailable_reason == "funding_payload_invalid"
    assert snapshot.settlement_interval_seconds is None


def test_binance_funding_snapshot_merges_adjusted_interval_from_funding_info(api):
    venue = "BINANCE___SWAP"
    premium = {
        "symbol": "BTCUSDT",
        "lastFundingRate": "0.00012345",
        "nextFundingTime": 1_700_014_400_000,
        "received_wall_time": 1_700_000_000_000,
    }
    funding_info = [
        {
            "symbol": "ETHUSDT",
            "adjustedFundingRateCap": "0.025",
            "adjustedFundingRateFloor": "-0.025",
            "fundingIntervalHours": 8,
            "disclaimer": False,
        },
        {
            "symbol": "BTCUSDT",
            "adjustedFundingRateCap": "0.025",
            "adjustedFundingRateFloor": "-0.025",
            "fundingIntervalHours": 4,
            "disclaimer": False,
        },
    ]
    history = Mock(side_effect=AssertionError("adjusted interval is authoritative"))
    api.exchange_feeds[venue] = SimpleNamespace(
        get_funding_rate=Mock(return_value=premium),
        get_funding_info=Mock(return_value=funding_info),
        get_history_funding_rate=history,
    )

    snapshot = api.get_funding_snapshot(venue, "BTC-USDT")

    assert snapshot.available is True
    assert snapshot.rate == Decimal("0.00012345")
    assert snapshot.settlement_interval_seconds == 14_400
    assert snapshot.next_funding_time == datetime.fromtimestamp(1_700_014_400, UTC)
    api.exchange_feeds[venue].get_funding_info.assert_called_once_with(extra_data=None)
    history.assert_not_called()


def test_binance_standard_interval_is_derived_from_latest_settlement(api):
    venue = "BINANCE___SWAP"
    premium = {
        "symbol": "BTCUSDT",
        "lastFundingRate": "0.0001",
        "nextFundingTime": 1_700_028_800_000,
        "received_wall_time": 1_700_000_000_000,
    }
    history = Mock(
        return_value=[
            {
                "symbol": "BTCUSDT",
                "fundingRate": "0.00008",
                "fundingTime": 1_699_971_200_000,
                "markPrice": "35000.0",
            },
            {
                "symbol": "BTCUSDT",
                "fundingRate": "0.00009",
                "fundingTime": 1_700_000_000_000,
                "markPrice": "35100.0",
            },
        ]
    )
    api.exchange_feeds[venue] = SimpleNamespace(
        get_funding_rate=Mock(return_value=premium),
        # Binance documents this endpoint as adjusted symbols only, so an
        # empty successful response cannot prove the standard interval.
        get_funding_info=Mock(return_value=[]),
        get_history_funding_rate=history,
    )

    snapshot = api.get_funding_snapshot(venue, "BTCUSDT")

    assert snapshot.available is True
    assert snapshot.settlement_interval_seconds == 28_800
    history.assert_called_once_with("BTCUSDT", count=2, extra_data=None)


def test_binance_never_invents_interval_when_schedule_evidence_is_missing(api):
    venue = "BINANCE___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(
        get_funding_rate=Mock(
            return_value={
                "symbol": "BTCUSDT",
                "lastFundingRate": "0.0001",
                "nextFundingTime": 1_700_028_800_000,
                "received_wall_time": 1_700_000_000_000,
            }
        ),
        get_funding_info=Mock(return_value=[{"symbol": "ETHUSDT", "fundingIntervalHours": 8}]),
        get_history_funding_rate=Mock(return_value=[]),
    )

    snapshot = api.get_funding_snapshot(venue, "BTCUSDT")

    assert snapshot.available is False
    assert snapshot.unavailable_reason == "funding_interval_missing"
    assert snapshot.settlement_interval_seconds is None


def test_binance_invalid_adjusted_interval_fails_closed_without_history_fallback(api):
    venue = "BINANCE___SWAP"
    history = Mock(side_effect=AssertionError("invalid explicit schedule must fail closed"))
    api.exchange_feeds[venue] = SimpleNamespace(
        get_funding_rate=Mock(
            return_value={
                "symbol": "BTCUSDT",
                "lastFundingRate": "0.0001",
                "nextFundingTime": 1_700_028_800_000,
                "received_wall_time": 1_700_000_000_000,
            }
        ),
        get_funding_info=Mock(return_value=[{"symbol": "BTCUSDT", "fundingIntervalHours": 0}]),
        get_history_funding_rate=history,
    )

    snapshot = api.get_funding_snapshot(venue, "BTCUSDT")

    assert snapshot.available is False
    assert snapshot.unavailable_reason == "funding_interval_invalid"
    history.assert_not_called()


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (
            {
                "symbol": "BTCUSDT",
                "lastFundingRate": "0.0001",
                "nextFundingTime": 1_700_028_800_000,
                "received_wall_time": 1_700_000_000_000,
            },
            "funding_interval_missing",
        ),
        (
            {
                "symbol": "BTCUSDT",
                "lastFundingRate": "0.0001",
                "nextFundingTime": 1_700_000_000_000,
                "fundingIntervalHours": "8",
                "received_wall_time": 1_700_000_000_000,
            },
            "funding_schedule_stale",
        ),
        (
            {
                "symbol": "BTCUSDT",
                "lastFundingRate": "0.0001",
                "nextFundingTime": 1_699_999_999_000,
                "fundingIntervalHours": "8",
                "received_wall_time": 1_700_000_000_000,
            },
            "funding_schedule_stale",
        ),
        (
            {
                "symbol": "BTCUSDT",
                "lastFundingRate": "0.0001",
                "nextFundingTime": 1_700_028_800_000,
                "fundingIntervalHours": "0",
                "received_wall_time": 1_700_000_000_000,
            },
            "funding_interval_invalid",
        ),
    ],
)
def test_incomplete_or_expired_funding_normalizes_to_typed_unavailable(api, row, reason):
    venue = "BINANCE___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(get_funding_rate=Mock(return_value=row))

    snapshot = api.get_funding_snapshot(venue, "BTCUSDT")

    assert isinstance(snapshot, FundingSnapshot)
    assert not snapshot.available
    assert snapshot.freshness.stale
    assert snapshot.freshness.stale_reason == reason
    assert snapshot.unavailable_reason == reason


def test_fee_and_funding_read_failures_are_explicit_unavailable(api):
    venue = "OKX___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(
        get_fee=Mock(side_effect=TimeoutError()),
        get_funding_rate=Mock(side_effect=TimeoutError()),
    )

    fees = api.get_fee_schedule(venue, "BTC-USDT-SWAP", "acct-1")
    funding = api.get_funding_snapshot(venue, "BTC-USDT-SWAP")

    assert not fees.available and fees.freshness.stale and fees.unavailable_reason
    assert not funding.available and funding.freshness.stale
    assert funding.unavailable_reason == "funding_transport_failed"
    assert funding.freshness.stale_reason == "funding_transport_failed"
    assert funding.raw == {}


def test_funding_api_error_response_is_a_transport_failure_not_a_changed_schedule(api):
    venue = "OKX___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(
        get_funding_rate=Mock(return_value={"code": "50011", "msg": "fixture upstream throttled"})
    )

    funding = api.get_funding_snapshot(venue, "BTC-USDT-SWAP")

    assert isinstance(funding, FundingSnapshot)
    assert funding.available is False
    assert funding.unavailable_reason == "funding_transport_failed"
    assert funding.freshness.stale_reason == "funding_transport_failed"
    assert funding.raw == {}


def test_malformed_funding_container_fails_closed_as_payload_invalid(api):
    venue = "BINANCE___SWAP"
    api.exchange_feeds[venue] = SimpleNamespace(get_funding_rate=Mock(return_value=object()))

    funding = api.get_funding_snapshot(venue, "BTCUSDT")

    assert isinstance(funding, FundingSnapshot)
    assert funding.available is False
    assert funding.unavailable_reason == "funding_payload_invalid"
    assert funding.freshness.stale_reason == "funding_payload_invalid"
    assert funding.raw == {}


def test_private_reconnect_triggers_idempotent_four_scope_backfill(api, monkeypatch):
    venue = "OKX___SWAP"
    feed = SimpleNamespace(
        get_open_orders=Mock(return_value=[]),
        get_account=Mock(
            return_value=[
                {
                    "currency": "USDT",
                    "available_margin": "100",
                    "value": "100",
                    "can_trade": True,
                }
            ]
        ),
        get_position=Mock(return_value=[]),
        get_deals=Mock(return_value=[]),
        disconnect=Mock(),
    )
    api.exchange_feeds[venue] = feed
    api.data_queues[venue] = Queue()

    class ImmediateThread:
        def __init__(self, *, target, args, kwargs, **_options):
            self.target = target
            self.args = args
            self.kwargs = kwargs

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr("bt_api_py.bt_api.threading.Thread", ImmediateThread)
    reconnect = {
        "stream_role": "account",
        "exchange_name": "OKX",
        "asset_type": "SWAP",
        "connection_generation": 2,
    }

    api.event_bus.emit("ws.connected", reconnect)
    api.event_bus.emit("ws.connected", reconnect)

    assert feed.get_open_orders.call_count == 1
    assert feed.get_account.call_count == 1
    assert feed.get_position.call_count == 1
    assert feed.get_deals.call_count == 1
    assert api.get_event_metrics()["private_reconcile_triggers"] == 1
    required = api.data_queues[venue].get_nowait()
    result = api.data_queues[venue].get_nowait()
    assert required["status"] == "required"
    assert result["status"] == "complete"
    assert set(result["scopes"]) == {"orders", "account", "positions", "trades"}
