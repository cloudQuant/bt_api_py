import json
from collections import Counter
from math import fsum

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


def test_n3_multi_account_bindings_keep_u0_market_and_per_account_order_targets() -> None:
    profile = load01_profile("N3")
    keys = market_keys_for_profile("N3")
    account_counts = tuple(map(len, profile.strategy_indices_by_account))
    waves = profile.startup_waves(slot_limit=10)

    assert profile.accounts_by_strategy[:10] == ((0, 1),) * 10
    assert profile.accounts_by_strategy[10:19] == ((0,),) * 9
    assert profile.accounts_by_strategy[19:28] == ((1,),) * 9
    assert account_counts == (19, 19) + (9,) * 8
    assert profile.strategy_account_binding_count == 110
    assert profile.topic_indices_by_strategy == (tuple(range(20)),) * 100
    assert profile.topic_messages_per_second == (10,) * 20
    assert profile.total_delivery_target_per_second == 20_000
    assert len(keys) == 20
    assert all(key.instrument.venue == "loopback" for key in keys)
    assert profile.order_targets_per_account_per_second == (2.0,) * 10
    assert sum(profile.order_targets_per_account_per_second) == 20.0
    assert profile.expected_bindings_per_strategy(0) == 9
    assert profile.expected_bindings_per_strategy(10) == 5
    assert profile.expected_registry_sessions["market"] == 100
    assert profile.expected_registry_sessions["monitor-durable"] == 110
    assert profile.expected_registry_sessions["monitor-telemetry"] == 110
    assert tuple(
        profile.expected_registry_sessions[f"account-{index:02d}"]
        for index in range(10)
    ) == account_counts
    assert tuple(
        profile.expected_registry_sessions[f"execution-{index:02d}"]
        for index in range(10)
    ) == tuple(2 * count for count in account_counts)
    assert sum(map(len, waves)) == 100
    assert len({index for wave in waves for index in wave}) == 100
    assert all(1 <= len(wave) <= 10 for wave in waves)
    assert profile.registration_status == "CANDIDATE_NOT_P0_REGISTERED"
    assert profile.formal_acceptance_eligible("full") is False


def test_n4_same_account_market_rotation_and_cycle_quotas_are_explicit() -> None:
    profile = load01_profile("N4")
    keys = market_keys_for_profile("N4")
    expected_intervals = (2.5, 5.0, 5.0, 5.0, 5.0, 10.0, 5.0, 5.0, 5.0, 10.0)
    expected_phases = (0.0, 0.0, 0.5, 1.0, 1.5, 2.5, 3.0, 3.5, 4.0, 7.5)

    assert profile.accounts_by_strategy == tuple(
        (strategy_index // 10,) for strategy_index in range(100)
    )
    for strategy_index in range(100):
        account_index, local_index = divmod(strategy_index, 10)
        account_keys = tuple((2 * account_index + offset) % 20 for offset in range(4))
        expected_topics = account_keys if local_index == 0 else (account_keys[local_index % 4],)

        assert profile.subscribed_topic_indices(strategy_index) == expected_topics
        assert profile.order_interval_seconds_by_strategy[strategy_index] == expected_intervals[
            local_index
        ]
        assert profile.order_phase_seconds_by_strategy[strategy_index] == expected_phases[
            local_index
        ]
        assert profile.order_topic_index(strategy_index, 1) == expected_topics[0]
        assert profile.order_topic_index(strategy_index, 4) == expected_topics[(3 % len(expected_topics))]

    assert tuple(key.instrument.instrument_id for key in keys[:5]) == (
        "sim-sim-a-btc-usdt",
        "sim-sim-b-btc-usdt",
        "SYNTH-X",
        "SYNTH-X",
        "SYNTH-X",
    )
    assert tuple(key.timeframe for key in keys[2:5]) == ("1m", "5m", "15m")
    account_one_topics = profile.subscribed_topic_indices(10)
    account_one_instruments = tuple(
        keys[topic_index].instrument.instrument_id for topic_index in account_one_topics
    )
    assert account_one_topics == (2, 3, 4, 5)
    assert account_one_instruments == (
        "SYNTH-X",
        "SYNTH-X",
        "SYNTH-X",
        "load01-instrument-05",
    )
    assert len(set(account_one_instruments)) == 2
    assert sum(map(len, profile.topic_indices_by_strategy)) == 130
    assert profile.topic_subscriber_counts == (6, 7) * 10
    assert profile.topic_messages_per_second == (10,) * 20
    assert profile.total_input_target_per_second == 200
    assert profile.total_delivery_target_per_second == 1_300
    assert profile.order_targets_per_account_per_second == (2.0,) * 10
    for account_index, topic_rates in enumerate(
        profile.order_targets_by_account_topic_per_second
    ):
        expected = [0.0] * 20
        for topic_index in range(4):
            expected[(2 * account_index + topic_index) % 20] = 0.5
        assert topic_rates == tuple(expected)
    assert profile.order_topic_index(0, 1) == 0
    assert tuple(profile.order_topic_index(0, cycle) for cycle in range(1, 5)) == (0, 1, 2, 3)
    assert profile.registration_status == "CANDIDATE_NOT_P0_REGISTERED"
    assert profile.formal_acceptance_eligible("full") is False


def test_n5_group_mix_preserves_u0_market_and_separates_logical_and_leg_rates() -> None:
    profile = load01_profile("N5")
    keys = market_keys_for_profile("N5")

    assert profile.accounts_by_strategy == tuple((index // 10,) for index in range(100))
    assert profile.topic_indices_by_strategy == (tuple(range(20)),) * 100
    assert keys == market_keys_for_profile("U0")
    assert profile.order_interval_seconds_by_strategy == (5.0,) * 100
    assert profile.order_phase_seconds_by_strategy == tuple(
        (index % 10) * 0.5 for index in range(100)
    )
    assert profile.expected_group_capable_strategies == tuple(range(0, 100, 10))
    assert profile.expected_bindings_per_strategy(0) == 6
    assert profile.expected_bindings_per_strategy(1) == 5
    assert profile.expected_registry_sessions["group-coordinator"] == 10

    account_mix = []
    for account_index in range(10):
        strategy_indices = profile.strategy_indices_by_account[account_index]
        cycles = [
            profile.order_kind_for_cycle(strategy_index, cycle)
            for strategy_index in strategy_indices
            for cycle in (1, 2)
        ]
        account_mix.append((cycles.count("single"), cycles.count("group")))
    assert account_mix == [(19, 1)] * 10
    assert profile.order_targets_per_account_per_second == (2.0,) * 10
    assert profile.single_targets_per_account_per_second == (1.9,) * 10
    assert profile.group_targets_per_account_per_second == (0.1,) * 10
    assert profile.child_dispatch_targets_per_account_per_second == (2.2,) * 10
    assert fsum(profile.order_targets_per_account_per_second) == 20.0
    assert fsum(profile.group_targets_per_account_per_second) == 1.0
    assert fsum(profile.child_dispatch_targets_per_account_per_second) == 22.0
    assert profile.order_targets_by_account_topic_per_second == ((0.095,) * 20,) * 10
    assert all(
        fsum(topic_rates) == 1.9
        for topic_rates in profile.order_targets_by_account_topic_per_second
    )
    assert profile.registration_status == "CANDIDATE_NOT_P0_REGISTERED"
    assert profile.formal_acceptance_eligible("full") is False


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
    with pytest.raises(ValueError, match="U0, N1, N2, N3, N4, or N5"):
        load01_profile("N6")
