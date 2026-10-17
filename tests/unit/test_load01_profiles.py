import json
from collections import Counter

import pytest

from tests.unit._load01_profiles import (
    load01_profile,
    market_keys_for_profile,
    market_payload_data,
)


def test_u0_remains_the_default_uniform_profile() -> None:
    profile = load01_profile()

    assert profile.profile_id == "U0"
    assert profile.strategy_indices_by_account == tuple(
        tuple(range(account * 10, (account + 1) * 10)) for account in range(10)
    )
    assert all(topics == tuple(range(20)) for topics in profile.topic_indices_by_strategy)
    assert profile.topic_messages_per_second == (10,) * 20
    assert profile.total_input_target_per_second == 200
    assert profile.total_delivery_target_per_second == 20_000
    assert profile.order_targets_per_account_per_second == (2.0,) * 10
    assert profile.topic_publish_schedule() == tuple(range(20)) * 10
    assert profile.registration_status == "P0_INITIAL_LOAD_BASELINE"
    assert profile.formal_acceptance_eligible("full") is True
    assert profile.formal_acceptance_eligible("pilot") is False

    payload, payload_bytes = market_payload_data(
        market_keys_for_profile("U0")[0], 1, 123
    )
    assert payload["padding"] == "x" * 160
    assert payload_bytes == len(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def test_n1_registers_partial_overlap_cross_venue_and_explicit_bar_keys() -> None:
    profile = load01_profile("N1")
    keys = market_keys_for_profile("N1")

    assert len(keys) == 20
    assert keys[0].instrument.native_symbol == keys[1].instrument.native_symbol == "BTC-USDT"
    assert keys[0].instrument.venue != keys[1].instrument.venue
    assert keys[0].instrument.instrument_id != keys[1].instrument.instrument_id
    assert tuple(key.timeframe for key in keys[2:5]) == ("1m", "5m", "15m")
    assert all(key.channel == "bars" for key in keys[2:5])
    assert all(key.instrument == keys[2].instrument for key in keys[2:5])
    assert len({key.stream_id for key in keys}) == 20
    assert all(len(topics) == 10 for topics in profile.topic_indices_by_strategy)
    assert profile.topic_subscriber_counts == (100,) * 5 + (34,) * 5 + (33,) * 10
    assert sum(map(len, profile.topic_indices_by_strategy)) == 1_000
    assert profile.total_input_target_per_second == 200
    assert profile.total_delivery_target_per_second == 10_000
    assert profile.registration_status == "CANDIDATE_NOT_P0_REGISTERED"
    assert profile.formal_acceptance_eligible("full") is False


def test_n2_hotspot_topic_and_account_quotas_are_explicit_and_exact() -> None:
    profile = load01_profile("N2")
    topic_schedule = Counter(profile.topic_publish_schedule())

    assert tuple(map(len, profile.strategy_indices_by_account)) == (
        40, 40, 3, 3, 3, 3, 2, 2, 2, 2
    )
    assert profile.topic_messages_per_second == (70, 70, 4, 4, 4, 4, 4, 4) + (3,) * 12
    assert tuple(topic_schedule[index] for index in range(20)) == profile.topic_messages_per_second
    assert profile.topic_subscriber_counts == (100,) * 5 + (34,) * 5 + (33,) * 10
    assert profile.order_targets_per_account_per_second == (8.0, 8.0) + (0.5,) * 8
    assert sum(profile.order_targets_per_account_per_second) == pytest.approx(20.0)
    assert profile.total_input_target_per_second == 200
    assert profile.total_delivery_target_per_second == 16_802
    assert profile.registration_status == "CANDIDATE_NOT_P0_REGISTERED"
    assert profile.formal_acceptance_eligible("full") is False
    assert profile.expected_registry_sessions["market"] == 100
    registry_sessions = profile.expected_registry_sessions
    account_strategy_counts = tuple(map(len, profile.strategy_indices_by_account))
    assert registry_sessions["market"] == 100
    assert registry_sessions["monitor-durable"] == 100
    assert registry_sessions["monitor-telemetry"] == 100
    assert tuple(
        registry_sessions[f"account-{index:02d}"] for index in range(10)
    ) == account_strategy_counts
    assert tuple(
        registry_sessions[f"execution-{index:02d}"] for index in range(10)
    ) == tuple(2 * count for count in account_strategy_counts)
    assert sum(map(len, profile.topic_indices_by_strategy)) == 1_000


def test_bars_are_direct_facts_with_per_message_json_byte_measurement() -> None:
    keys = market_keys_for_profile("N1")[2:5]

    assert tuple(key.timeframe for key in keys) == ("1m", "5m", "15m")
    for key in keys:
        first, first_bytes = market_payload_data(key, 1, 1_000_000_000)
        second, second_bytes = market_payload_data(key, 2, 1_100_000_000)
        first_bar = first["bar"]
        second_bar = second["bar"]

        assert "padding" not in first
        assert first_bar["timeframe"] == key.timeframe
        assert first_bar["open_time_ns"] == second_bar["open_time_ns"]
        assert first_bar["update_time_ns"] < second_bar["update_time_ns"]
        assert first_bar["is_closed"] is False
        assert second_bar["is_closed"] is False
        assert first_bar["fact_source"] == "synthetic-provider-direct"
        assert {"open", "high", "low", "close", "volume"} <= first_bar.keys()
        assert int(second_bar["volume"]) > int(first_bar["volume"])
        assert first_bytes == len(
            json.dumps(first, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        assert second_bytes == len(
            json.dumps(second, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )


def test_n1_and_n2_startup_waves_keep_the_ten_strategy_slot_guard() -> None:
    for profile_id in ("N1", "N2"):
        profile = load01_profile(profile_id)
        waves = profile.startup_waves(slot_limit=10)

        assert sum(map(len, waves)) == 100
        assert all(1 <= len(wave) <= 10 for wave in waves)
        assert len({index for wave in waves for index in wave}) == 100


def test_unknown_profile_fails_closed() -> None:
    with pytest.raises(ValueError, match="U0, N1, or N2"):
        load01_profile("N3")
