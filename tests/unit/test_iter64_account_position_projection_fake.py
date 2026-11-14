"""Bounded Risk account-position projection; no account or service I/O."""

from dataclasses import replace
from decimal import ROUND_DOWN, ROUND_UP, Decimal, Inexact, localcontext
from types import SimpleNamespace

import pytest
from bt_api_base.account_protocol import (
    AccountKey,
    AccountObservation,
    AccountPosition,
    AccountResourceKey,
)
from bt_api_base.market_protocol import MarketInstrumentKey

from bt_api_py.risk_service_config import RiskInstrumentBinding
from bt_api_py.risk_service_gateway import (
    RiskAccountSnapshotSource,
    RiskFactsCache,
    account_snapshot_from_observation,
)

KEY = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")
ACCOUNT = AccountKey("ctp", "simnow", "simnow", "synthetic")
R1 = AccountResourceKey("future", "pool-a")
R2 = AccountResourceKey("future", "pool-b")
TIME = "2030-01-01T01:00:00Z"


def position(resource=R1, *, instrument=KEY, side="long", quantity="1.23",
             today="1.23", yesterday="0", unit="contract"):
    return AccountPosition(resource, instrument, side, quantity, unit, None, None,
                           TIME, today, yesterday)


def observation(*rows):
    resources = tuple(dict.fromkeys(row.resource for row in rows)) or (R1,)
    return AccountObservation(ACCOUNT, "synthetic-observation", resources, (), tuple(rows),
                              None, TIME, TIME, "multi_query", False, False)


def project(value, keys=None):
    return account_snapshot_from_observation(
        value, instrument_ids={KEY: "rb"} if keys is None else keys,
        dated_venues=frozenset({"SHFE"}),
    )


def source(value):
    binding = RiskInstrumentBinding("rb", KEY, Decimal("10"), Decimal("1"))
    facts = RiskFactsCache({"rb": binding}, now_ns=lambda: 1)
    client = SimpleNamespace(refresh=lambda **_: SimpleNamespace(observation=value))
    result = RiskAccountSnapshotSource(client, facts, instrument_ids={("SHFE", "rb"): "rb"},
                                       timeout_seconds=1)
    return result, facts, client


@pytest.mark.parametrize("rounding", [ROUND_DOWN, ROUND_UP])
def test_same_complete_key_and_unit_sum_exactly_independent_of_context(rounding):
    value = observation(position(R1), position(R2, quantity="4.56", today="4.56"))
    with localcontext() as context:
        context.prec = 2
        context.rounding = rounding
        context.traps[Inexact] = True
        snapshot = project(value)
    assert snapshot.positions[("rb", "LONG")] == (Decimal("5.79"), Decimal(0))
    assert snapshot.complete is False and snapshot.valid_until_ns is None


def test_actual_source_recovers_full_config_key_and_revokes_failed_refresh():
    good = observation(position())
    reader, facts, client = source(good)
    assert reader.refresh() is True
    assert facts.account_snapshot().positions[("rb", "LONG")] == (Decimal("1.23"), Decimal(0))
    other_resource = AccountResourceKey("option", "pool-c")
    other_key = replace(KEY, product_type="option")
    bad = observation(position(), position(other_resource, instrument=other_key))
    client.refresh = lambda **_: SimpleNamespace(observation=bad)
    assert reader.refresh() is False and reader.failures == 1
    assert facts.account_snapshot() is None


@pytest.mark.parametrize("change", [
    {"instrument_id": "another"},
    {"native_symbol": "another"},
    {"venue": "DCE"},
])
def test_unconfigured_complete_key_never_falls_back_to_native_alias(change):
    other = replace(KEY, **change)
    with pytest.raises(ValueError, match="^RISK_ACCOUNT_POSITION_UNAVAILABLE$"):
        project(observation(position(instrument=other)))


@pytest.mark.parametrize("side", ["long", "short"])
def test_same_key_different_observed_units_are_not_merged_even_across_sides(side):
    value = observation(position(R1), position(R2, side=side, unit="lot"))
    with pytest.raises(ValueError, match="^RISK_ACCOUNT_POSITION_UNAVAILABLE$"):
        project(value)


def test_duplicate_projection_id_or_legacy_partial_key_is_unavailable():
    other = replace(KEY, venue="DCE")
    for keys in ({KEY: "rb", other: "rb"}, {("SHFE", "rb"): "rb"}):
        with pytest.raises(ValueError, match="^RISK_ACCOUNT_POSITION_UNAVAILABLE$"):
            project(observation(position()), keys)


def test_source_rejects_mismatched_host_alias_map():
    binding = RiskInstrumentBinding("rb", KEY, None, None)
    facts = RiskFactsCache({"rb": binding}, now_ns=lambda: 1)
    with pytest.raises(ValueError, match="^RISK_ACCOUNT_POSITION_CONFIG_INVALID$"):
        RiskAccountSnapshotSource(object(), facts, instrument_ids={("SHFE", "rb"): "other"},
                                  timeout_seconds=1)


@pytest.mark.parametrize("field,bad", [
    ("quantity", "1" * 129),
    ("quantity", "NaN"),
    ("quantity", "1e99999"),
    ("today_quantity", "2"),
    ("yesterday_quantity", None),
    ("qty_unit", ""),
])
def test_invalid_or_unbounded_mutated_rows_fail_whole_projection(field, bad):
    row = position()
    value = observation(row)
    object.__setattr__(row, field, bad)
    with pytest.raises(ValueError, match="^RISK_ACCOUNT_POSITION_UNAVAILABLE$"):
        project(value)


def test_source_failure_does_not_keep_earlier_cache_entry():
    reader, facts, client = source(observation(position()))
    assert reader.refresh()
    def fail(**_):
        raise RuntimeError("synthetic failure")
    client.refresh = fail
    assert reader.refresh() is False and facts.account_snapshot() is None


def test_source_clock_failure_during_install_clears_earlier_snapshot():
    reader, facts, client = source(observation(position()))
    assert reader.refresh()
    client.refresh = lambda **_: SimpleNamespace(
        observation=observation(position(quantity="2", today="2"))
    )

    def fail_clock():
        raise RuntimeError("synthetic clock failure")

    facts._now_ns = fail_clock
    assert reader.refresh() is False
    assert reader.failures == 1 and facts.account_snapshot() is None
    assert facts._account_received_ns is None


def test_direct_cache_install_clock_failure_preserves_previous_entry_atomically():
    reader, facts, _ = source(observation(position()))
    assert reader.refresh()
    previous = facts._account
    received = facts._account_received_ns

    def fail_clock():
        raise RuntimeError("synthetic clock failure")

    facts._now_ns = fail_clock
    with pytest.raises(RuntimeError, match="synthetic clock failure"):
        facts.set_account(object())
    assert facts._account is previous and facts._account_received_ns == received


def test_non_dated_venue_without_split_remains_incomplete():
    key = replace(KEY, venue="DCE")
    row = position(instrument=key, quantity="2", today=None, yesterday=None)
    result = account_snapshot_from_observation(observation(row), instrument_ids={key: "rb"},
                                               dated_venues=frozenset({"SHFE"}))
    assert result.positions[("rb", "LONG")] == (Decimal(2), Decimal(0))
    assert result.complete is False
