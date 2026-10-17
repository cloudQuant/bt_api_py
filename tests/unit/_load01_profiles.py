"""Declarative workload profiles for the LOAD01 synthetic acceptance runner."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from math import fsum

from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey


_STRATEGY_COUNT = 100
_ACCOUNT_COUNT = 10
_TOPIC_COUNT = 20
_STRATEGIES_PER_ACCOUNT = 10
_SOURCE_REF = "load01-synthetic-source"
_ENTITLEMENT_REF = "load01-public-entitlement"


@dataclass(frozen=True)
class Load01Profile:
    profile_id: str
    accounts_by_strategy: tuple[tuple[int, ...], ...]
    topic_indices_by_strategy: tuple[tuple[int, ...], ...]
    topic_messages_per_second: tuple[int, ...]
    order_interval_seconds_by_strategy: tuple[float, ...]
    order_phase_seconds_by_strategy: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.profile_id not in {"U0", "N1", "N2", "N3", "N4", "N5", "N6"}:
            raise ValueError("LOAD01 profile id is not registered")
        if not (
            len(self.accounts_by_strategy)
            == len(self.topic_indices_by_strategy)
            == len(self.order_interval_seconds_by_strategy)
            == len(self.order_phase_seconds_by_strategy)
            == _STRATEGY_COUNT
        ):
            raise ValueError("LOAD01 profile must define all 100 strategies")
        if len(self.topic_messages_per_second) != _TOPIC_COUNT:
            raise ValueError("LOAD01 profile must define all 20 topic rates")
        if any(
            not accounts or any(not 0 <= item < _ACCOUNT_COUNT for item in accounts)
            for accounts in self.accounts_by_strategy
        ):
            raise ValueError("LOAD01 profile contains an invalid account assignment")
        if any(
            not topics or any(not 0 <= item < _TOPIC_COUNT for item in topics)
            for topics in self.topic_indices_by_strategy
        ):
            raise ValueError("LOAD01 profile contains an invalid topic subscription")
        if any(rate <= 0 for rate in self.topic_messages_per_second):
            raise ValueError("LOAD01 profile topic rates must be positive")
        if any(interval <= 0 for interval in self.order_interval_seconds_by_strategy):
            raise ValueError("LOAD01 profile order intervals must be positive")

    def accounts_for_strategy(self, strategy_index: int) -> tuple[int, ...]:
        return self.accounts_by_strategy[strategy_index]

    def account_for_strategy(self, strategy_index: int) -> int:
        accounts = self.accounts_for_strategy(strategy_index)
        if len(accounts) != 1:
            raise ValueError("this LOAD01 runner path requires one account per strategy")
        return accounts[0]

    @property
    def strategy_indices_by_account(self) -> tuple[tuple[int, ...], ...]:
        assigned: list[list[int]] = [[] for _ in range(_ACCOUNT_COUNT)]
        for strategy_index, accounts in enumerate(self.accounts_by_strategy):
            for account_index in accounts:
                assigned[account_index].append(strategy_index)
        return tuple(tuple(indices) for indices in assigned)

    @property
    def strategy_account_binding_count(self) -> int:
        return sum(map(len, self.accounts_by_strategy))

    def expected_bindings_per_strategy(self, strategy_index: int) -> int:
        # One shared market binding plus account, order, query and telemetry
        # bindings for each authorized account route.
        group_binding = int(
            self.profile_id == "N5"
            and strategy_index in self.expected_group_capable_strategies
        )
        return 1 + 4 * len(self.accounts_for_strategy(strategy_index)) + group_binding

    def subscribed_topic_indices(self, strategy_index: int) -> tuple[int, ...]:
        return self.topic_indices_by_strategy[strategy_index]

    def order_topic_index(self, strategy_index: int, cycle: int) -> int | None:
        """Return the N4 instrument key for a one-based strategy order cycle."""
        if self.profile_id != "N4":
            return None
        if cycle < 1:
            raise ValueError("LOAD01 order cycles are one-based")
        topics = self.subscribed_topic_indices(strategy_index)
        return topics[(cycle - 1) % len(topics)]

    def order_kind_for_cycle(self, strategy_index: int, cycle: int) -> str:
        """Return the registered logical request kind for a one-based cycle."""
        if cycle < 1:
            raise ValueError("LOAD01 order cycles are one-based")
        if (
            self.profile_id == "N5"
            and strategy_index % _STRATEGIES_PER_ACCOUNT == 0
            and cycle % 2 == 1
        ):
            return "group"
        return "single"

    @property
    def group_targets_per_account_per_second(self) -> tuple[float, ...]:
        if self.profile_id != "N5":
            return (0.0,) * _ACCOUNT_COUNT
        return (0.1,) * _ACCOUNT_COUNT

    @property
    def single_targets_per_account_per_second(self) -> tuple[float, ...]:
        return tuple(
            logical - group
            for logical, group in zip(
                self.order_targets_per_account_per_second,
                self.group_targets_per_account_per_second,
                strict=True,
            )
        )

    @property
    def child_dispatch_targets_per_account_per_second(self) -> tuple[float, ...]:
        return tuple(
            single + 3 * group
            for single, group in zip(
                self.single_targets_per_account_per_second,
                self.group_targets_per_account_per_second,
                strict=True,
            )
        )

    @property
    def expected_group_capable_strategies(self) -> tuple[int, ...]:
        if self.profile_id != "N5":
            return ()
        return tuple(indices[0] for indices in self.strategy_indices_by_account)

    @property
    def topic_subscriber_counts(self) -> tuple[int, ...]:
        counts = [0] * _TOPIC_COUNT
        for topics in self.topic_indices_by_strategy:
            for topic_index in topics:
                counts[topic_index] += 1
        return tuple(counts)

    @property
    def topic_delivery_targets_per_second(self) -> tuple[int, ...]:
        return tuple(
            rate * subscribers
            for rate, subscribers in zip(
                self.topic_messages_per_second, self.topic_subscriber_counts, strict=True
            )
        )

    @property
    def total_input_target_per_second(self) -> int:
        return sum(self.topic_messages_per_second)

    @property
    def total_delivery_target_per_second(self) -> int:
        return sum(self.topic_delivery_targets_per_second)

    @property
    def registration_status(self) -> str:
        if self.profile_id == "U0":
            return "P0_INITIAL_LOAD_BASELINE"
        return "CANDIDATE_NOT_P0_REGISTERED"

    def formal_acceptance_eligible(self, mode: str) -> bool:
        return self.profile_id == "U0" and mode == "full"

    @property
    def expected_registry_sessions(self) -> dict[str, int]:
        account_strategy_counts = tuple(
            len(indices) for indices in self.strategy_indices_by_account
        )
        sessions = {
            "market": _STRATEGY_COUNT,
            "monitor-durable": self.strategy_account_binding_count,
            "monitor-telemetry": self.strategy_account_binding_count,
            **{
                f"account-{index:02d}": account_strategy_counts[index]
                for index in range(_ACCOUNT_COUNT)
            },
            **{
                f"execution-{index:02d}": 2 * account_strategy_counts[index]
                for index in range(_ACCOUNT_COUNT)
            },
        }
        if self.profile_id == "N5":
            # Ten client registration sessions share one portfolio
            # coordinator instance; this count is not an instance/host count.
            sessions["group-coordinator"] = len(self.expected_group_capable_strategies)
        return sessions

    @property
    def order_targets_per_account_per_second(self) -> tuple[float, ...]:
        rates_by_account = [
            [
                1.0 / interval
                for strategy_index, interval in enumerate(self.order_interval_seconds_by_strategy)
                if account_index in self.accounts_for_strategy(strategy_index)
            ]
            for account_index in range(_ACCOUNT_COUNT)
        ]
        return tuple(fsum(rates) for rates in rates_by_account)

    @property
    def order_targets_by_account_topic_per_second(self) -> tuple[tuple[float, ...], ...]:
        contributions: list[list[list[float]]] = [
            [[] for _ in range(_TOPIC_COUNT)] for _ in range(_ACCOUNT_COUNT)
        ]
        for strategy_index, interval in enumerate(self.order_interval_seconds_by_strategy):
            topics = self.subscribed_topic_indices(strategy_index)
            single_rate = 1.0 / interval
            if (
                self.profile_id == "N5"
                and strategy_index in self.expected_group_capable_strategies
            ):
                # The designated strategy alternates one group and one
                # ordinary order cycle. Only its ordinary half is attributed
                # to market topics; the three group legs use the separate
                # DIM09 instrument contract.
                single_rate *= 0.5
            per_topic_rate = single_rate / len(topics)
            for account_index in self.accounts_for_strategy(strategy_index):
                for topic_index in topics:
                    contributions[account_index][topic_index].append(per_topic_rate)
        return tuple(
            tuple(fsum(topic_rates) for topic_rates in account_rates)
            for account_rates in contributions
        )

    def topic_publish_schedule(self) -> tuple[int, ...]:
        """Return a smooth weighted round-robin period with exact topic quotas."""
        weights = self.topic_messages_per_second
        total = sum(weights)
        current = [0] * len(weights)
        schedule = []
        for _ in range(total):
            for index, weight in enumerate(weights):
                current[index] += weight
            selected = max(range(len(weights)), key=current.__getitem__)
            current[selected] -= total
            schedule.append(selected)
        if tuple(Counter(schedule)[i] for i in range(_TOPIC_COUNT)) != weights:
            raise AssertionError("LOAD01 topic schedule lost a registered quota")
        return tuple(schedule)

    def startup_waves(self, slot_limit: int = 10) -> tuple[tuple[int, ...], ...]:
        if slot_limit < 1:
            raise ValueError("LOAD01 startup slot limit must be positive")
        remaining = [list(indices) for indices in self.strategy_indices_by_account]
        waves = []
        seen: set[int] = set()
        while any(remaining):
            wave = []
            for account_indices in remaining:
                while account_indices and len(wave) < slot_limit:
                    strategy_index = account_indices.pop(0)
                    if strategy_index not in seen:
                        seen.add(strategy_index)
                        wave.append(strategy_index)
                        break
            if not wave:
                raise AssertionError("LOAD01 startup wave generation made no progress")
            waves.append(tuple(wave))
        return tuple(waves)


def _profile(profile_id: str) -> Load01Profile:
    if profile_id not in {"U0", "N1", "N2", "N3", "N4", "N5", "N6"}:
        raise ValueError("BT_API_LOAD01_PROFILE must be U0, N1, N2, N3, N4, N5, or N6")

    if profile_id == "N3":
        accounts_by_strategy = tuple(
            (0, 1) if strategy_index < 10 else ((strategy_index - 10) // 9,)
            for strategy_index in range(_STRATEGY_COUNT)
        )
    elif profile_id == "N2":
        account_counts = (40, 40, 3, 3, 3, 3, 2, 2, 2, 2)
        accounts_by_strategy = tuple(
            (account_index,)
            for account_index, count in enumerate(account_counts)
            for _ in range(count)
        )
    else:
        accounts_by_strategy = tuple(
            (strategy_index // _STRATEGIES_PER_ACCOUNT,)
            for strategy_index in range(_STRATEGY_COUNT)
        )

    if profile_id in {"U0", "N3", "N5", "N6"}:
        topic_indices = tuple(tuple(range(_TOPIC_COUNT)) for _ in range(_STRATEGY_COUNT))
        topic_rates = (10,) * _TOPIC_COUNT
        if profile_id in {"U0", "N5", "N6"}:
            intervals = (5.0,) * _STRATEGY_COUNT
            phases = tuple(
                (index % _STRATEGIES_PER_ACCOUNT) * 0.5
                for index in range(_STRATEGY_COUNT)
            )
        else:
            interval_values = []
            phase_values = []
            for strategy_index in range(_STRATEGY_COUNT):
                if strategy_index < 10:
                    interval_values.append(10.0)
                    phase_values.append(strategy_index * 0.1)
                    continue
                account_index = (strategy_index - 10) // 9
                local_index = (strategy_index - 10) % 9
                if account_index < 2:
                    interval_values.append(5.0 if local_index == 0 else 10.0)
                    phase_values.append(float(local_index))
                else:
                    interval_values.append(2.5 if local_index == 0 else 5.0)
                    phase_values.append(local_index * 0.5)
            intervals = tuple(interval_values)
            phases = tuple(phase_values)
    elif profile_id == "N4":
        market_keys_by_account = tuple(
            tuple((2 * account_index + offset) % _TOPIC_COUNT for offset in range(4))
            for account_index in range(_ACCOUNT_COUNT)
        )
        topic_indices = tuple(
            market_keys_by_account[strategy_index // _STRATEGIES_PER_ACCOUNT]
            if strategy_index % _STRATEGIES_PER_ACCOUNT == 0
            else (
                market_keys_by_account[strategy_index // _STRATEGIES_PER_ACCOUNT][
                    strategy_index % _STRATEGIES_PER_ACCOUNT % 4
                ],
            )
            for strategy_index in range(_STRATEGY_COUNT)
        )
        topic_rates = (10,) * _TOPIC_COUNT
        interval_values = []
        phase_values = []
        phase_by_local = {
            0: 0.0,
            1: 0.0,
            2: 0.5,
            3: 1.0,
            4: 1.5,
            5: 2.5,
            6: 3.0,
            7: 3.5,
            8: 4.0,
            9: 7.5,
        }
        for strategy_index in range(_STRATEGY_COUNT):
            local_index = strategy_index % _STRATEGIES_PER_ACCOUNT
            interval_values.append(
                2.5 if local_index == 0 else (10.0 if local_index in {5, 9} else 5.0)
            )
            phase_values.append(phase_by_local[local_index])
        intervals = tuple(interval_values)
        phases = tuple(phase_values)
    else:
        groups = (
            (0, 34, tuple(range(5, 10))),
            (34, 67, tuple(range(10, 15))),
            (67, 100, tuple(range(15, 20))),
        )
        topic_indices = tuple(
            tuple(range(5))
            + next(extra for start, end, extra in groups if start <= index < end)
            for index in range(_STRATEGY_COUNT)
        )
        topic_rates = (
            (70, 70, 4, 4, 4, 4, 4, 4, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3)
            if profile_id == "N2"
            else (10,) * _TOPIC_COUNT
        )
        if profile_id == "N1":
            intervals = (5.0,) * _STRATEGY_COUNT
            phases = tuple(
                (index % _STRATEGIES_PER_ACCOUNT) * 0.5
                for index in range(_STRATEGY_COUNT)
            )
        else:
            interval_values = []
            phase_values = []
            local_counts = tuple(len(indices) for indices in _account_groups(accounts_by_strategy))
            local_slots = [0] * _ACCOUNT_COUNT
            for account_index, count in enumerate(local_counts):
                for _ in range(count):
                    local_index = local_slots[account_index]
                    local_slots[account_index] += 1
                    if account_index in (0, 1):
                        interval_values.append(5.0)
                        phase_values.append(local_index / 8.0)
                    elif account_index <= 5:
                        interval_values.append(6.0)
                        phase_values.append(local_index * 2.0)
                    else:
                        interval_values.append(4.0)
                        phase_values.append(local_index * 2.0)
            intervals = tuple(interval_values)
            phases = tuple(phase_values)

    return Load01Profile(
        profile_id,
        accounts_by_strategy,
        topic_indices,
        tuple(topic_rates),
        tuple(intervals),
        tuple(phases),
    )


def _account_groups(
    accounts_by_strategy: tuple[tuple[int, ...], ...]
) -> tuple[tuple[int, ...], ...]:
    assigned: list[list[int]] = [[] for _ in range(_ACCOUNT_COUNT)]
    for index, accounts in enumerate(accounts_by_strategy):
        for account_index in accounts:
            assigned[account_index].append(index)
    return tuple(tuple(indices) for indices in assigned)


def load01_profile(profile_id: str = "U0") -> Load01Profile:
    return _profile(profile_id)


def market_keys_for_profile(profile_id: str = "U0") -> tuple[MarketSubscriptionKey, ...]:
    profile = load01_profile(profile_id)
    keys = []
    for index in range(_TOPIC_COUNT):
        if profile.profile_id in {"U0", "N3", "N5", "N6"}:
            venue = "loopback"
            instrument_id = f"load01-instrument-{index:02d}"
            native_symbol = f"LOAD{index:02d}-USD"
            channel = "trades"
            timeframe = None
        elif index < 2:
            venue = f"SIM-{chr(ord('A') + index)}"
            instrument_id = f"sim-{venue.lower()}-btc-usdt"
            native_symbol = "BTC-USDT"
            channel = "trades"
            timeframe = None
        elif index < 5:
            venue = "SIM-A"
            instrument_id = "SYNTH-X"
            native_symbol = "SYNTH-X"
            channel = "bars"
            timeframe = ("1m", "5m", "15m")[index - 2]
        else:
            venue = "SIM-A"
            instrument_id = f"load01-instrument-{index:02d}"
            native_symbol = f"LOAD{index:02d}-USD"
            channel = "trades"
            timeframe = None
        keys.append(
            MarketSubscriptionKey(
                source_ref=_SOURCE_REF,
                entitlement_ref=_ENTITLEMENT_REF,
                instrument=MarketInstrumentKey(
                    provider="synthetic",
                    venue=venue,
                    environment="paper",
                    product_type="spot",
                    instrument_id=instrument_id,
                    native_symbol=native_symbol,
                ),
                channel=channel,
                timeframe=timeframe,
            )
        )
    return tuple(keys)


def market_payload_data(key, ordinal: int, publish_ns: int) -> tuple[dict, int]:
    """Build one direct synthetic provider fact and measure its serialized JSON bytes."""
    data = {
        "source": "load01-synthetic-provider",
        "topic": key.stream_id,
        "ordinal": ordinal,
        "publish_ns": publish_ns,
    }
    if key.channel == "bars":
        timeframe_ns = {"1m": 60, "5m": 300, "15m": 900}[key.timeframe] * 1_000_000_000
        open_time_ns = publish_ns // timeframe_ns * timeframe_ns
        open_price = 1_000 + (open_time_ns // timeframe_ns) % 100
        update_index = max(0, (publish_ns - open_time_ns) // 100_000_000)
        data["bar"] = {
            "timeframe": key.timeframe,
            "open_time_ns": open_time_ns,
            "update_time_ns": publish_ns,
            "open": str(open_price),
            "high": str(open_price + 2),
            "low": str(open_price - 1),
            "close": str(open_price + update_index % 3),
            "volume": str(update_index + 1),
            "is_closed": False,
            "fact_source": "synthetic-provider-direct",
        }
    else:
        data["padding"] = "x" * 160
    payload_bytes = len(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return data, payload_bytes
