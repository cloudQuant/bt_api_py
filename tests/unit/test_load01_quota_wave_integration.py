from __future__ import annotations

import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from tests.unit import test_load01_hundred_strategy_30min as load_runner
from tests.unit._load01_profiles import load01_profile, market_keys_for_profile

_N6_MODULES_ABSENT_AFTER_RUNNER_IMPORT = (
    "tests.unit._load01_quota_wave" not in sys.modules
    and "bt_api_gateway.quota_admission" not in sys.modules
)


def test_n6_loader_rejects_nonfixed_search_path_before_import(tmp_path: Path) -> None:
    unit_package = sys.modules["tests.unit"]
    original_path = unit_package.__path__
    unit_package.__path__ = [str(tmp_path)]
    try:
        with pytest.raises(AssertionError, match="package search path is not fixed"):
            load_runner._load_n6_quota_wave_module()
    finally:
        unit_package.__path__ = original_path
    assert "tests.unit._load01_quota_wave" not in sys.modules
    assert "bt_api_gateway.quota_admission" not in sys.modules


class _WaveEvent:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.is_set_value = False

    def is_set(self) -> bool:
        return self.is_set_value

    def set(self) -> None:
        self.calls.append("event.set")
        self.is_set_value = True


def _strategy_result_rows() -> dict[int, dict[str, Any]]:
    results: dict[int, dict[str, Any]] = {}
    for strategy_index in range(100):
        strategy_ref = f"strategy:{strategy_index:03d}"
        grant = {
            "strategy_index": strategy_index,
            "request_index": 0,
            "outcome": "granted",
            "subject_digest": f"subject-digest-{strategy_index:03d}",
            "run_digest": f"run-digest-{strategy_index:03d}",
            "elapsed_ns": 500_000_000,
        }
        denial = {
            "strategy_index": strategy_index,
            "request_index": 1,
            "outcome": "DEADLINE_EXCEEDED",
            "subject_digest": None,
            "run_digest": None,
            "elapsed_ns": 30_250_000_000,
        }
        results[strategy_index] = {
            "quota_admission_wave": {
                "started": True,
                "complete": True,
                "outcomes": [grant, denial],
            }
        }
    return results


def test_n6_preserves_u0_load_shape_and_remains_candidate_only() -> None:
    baseline = load01_profile("U0")
    candidate = load01_profile("N6")

    assert candidate.profile_id == "N6"
    assert candidate.accounts_by_strategy == baseline.accounts_by_strategy
    assert candidate.topic_indices_by_strategy == baseline.topic_indices_by_strategy
    assert candidate.topic_messages_per_second == baseline.topic_messages_per_second
    assert candidate.order_interval_seconds_by_strategy == baseline.order_interval_seconds_by_strategy
    assert candidate.order_phase_seconds_by_strategy == baseline.order_phase_seconds_by_strategy
    assert market_keys_for_profile("N6") == market_keys_for_profile("U0")
    assert candidate.registration_status == "CANDIDATE_NOT_P0_REGISTERED"
    assert candidate.formal_acceptance_eligible("full") is False
    assert candidate.formal_acceptance_eligible("pilot") is False
    assert len(fields(type(candidate))) == len(fields(type(baseline)))


def test_n6_seeds_only_at_t60_after_all_strategies_are_ready() -> None:
    calls: list[str] = []
    admission = object()
    event = _WaveEvent(calls)

    def seed(fake_admission: object) -> tuple[int, ...]:
        assert fake_admission is admission
        calls.append("seed")
        return tuple(range(100))

    assert load_runner._n6_quota_wave_seed_if_due(
        profile_id="U0",
        now=160.0,
        load_start=100.0,
        all_strategies_ready=True,
        admission=None,
        wave_event=None,
        seed_fn=seed,
    ) is None
    assert load_runner._n6_quota_wave_seed_if_due(
        profile_id="N6",
        now=159.99,
        load_start=100.0,
        all_strategies_ready=True,
        admission=admission,
        wave_event=event,
        seed_fn=seed,
    ) is None
    assert calls == []
    assert not event.is_set()

    with pytest.raises(RuntimeError, match="not allowed before all strategies are ready"):
        load_runner._n6_quota_wave_seed_if_due(
            profile_id="N6",
            now=160.0,
            load_start=100.0,
            all_strategies_ready=False,
            admission=admission,
            wave_event=event,
            seed_fn=seed,
        )
    assert calls == []
    assert not event.is_set()

    record = load_runner._n6_quota_wave_seed_if_due(
        profile_id="N6",
        now=160.0,
        load_start=100.0,
        all_strategies_ready=True,
        admission=admission,
        wave_event=event,
        seed_fn=seed,
    )
    assert record is not None
    assert record["scheduled_after_load_start_seconds"] == 60.0
    assert record["seed_grant_count"] == 100
    assert calls == ["seed", "event.set"]
    assert event.is_set()

    with pytest.raises(AssertionError, match="event was set before parent seeding"):
        load_runner._n6_quota_wave_seed_if_due(
            profile_id="N6",
            now=160.0,
            load_start=100.0,
            all_strategies_ready=True,
            admission=admission,
            wave_event=event,
            seed_fn=seed,
        )
    assert calls == ["seed", "event.set"]


def test_n6_seed_failure_keeps_strategy_workers_untriggered() -> None:
    calls: list[str] = []
    event = _WaveEvent(calls)

    def incomplete_seed(_admission: object) -> tuple[int, ...]:
        calls.append("seed")
        return tuple(range(99))

    with pytest.raises(AssertionError, match="exactly 100 grants"):
        load_runner._n6_quota_wave_seed_if_due(
            profile_id="N6",
            now=60.0,
            load_start=0.0,
            all_strategies_ready=True,
            admission=object(),
            wave_event=event,
            seed_fn=incomplete_seed,
        )
    assert calls == ["seed"]
    assert not event.is_set()


def test_n6_worker_results_restore_typed_fairness_summary() -> None:
    results = _strategy_result_rows()
    outcomes = load_runner._n6_quota_wave_outcomes_from_strategy_results(results)
    from tests.unit import _load01_quota_wave as quota_wave

    summary = quota_wave.summarize_quota_wave(outcomes)

    assert len(outcomes) == 200
    assert all(type(row) is quota_wave.QuotaWaveOutcome for row in outcomes)
    assert summary.offered == 200
    assert summary.granted == summary.deadline_exceeded == 100
    assert summary.grants_by_strategy == (1,) * 100
    assert summary.denials_by_strategy == (1,) * 100
    assert summary.grant_p95_ns == 500_000_000
    assert summary.denial_p95_ns == 30_250_000_000


def test_n6_result_adapter_rejects_partial_or_cross_strategy_results() -> None:
    missing_strategy = _strategy_result_rows()
    del missing_strategy[99]
    with pytest.raises(AssertionError, match="all 100 strategies"):
        load_runner._n6_quota_wave_outcomes_from_strategy_results(missing_strategy)

    crossed = _strategy_result_rows()
    crossed[1]["quota_admission_wave"]["outcomes"][0]["strategy_index"] = 0
    with pytest.raises(AssertionError, match="crossed strategy ownership"):
        load_runner._n6_quota_wave_outcomes_from_strategy_results(crossed)

    incomplete = _strategy_result_rows()
    incomplete[5]["quota_admission_wave"]["complete"] = False
    with pytest.raises(AssertionError, match="did not return both quota outcomes"):
        load_runner._n6_quota_wave_outcomes_from_strategy_results(incomplete)


def test_nonquota_runner_import_does_not_load_n6_quota_modules() -> None:
    assert _N6_MODULES_ABSENT_AFTER_RUNNER_IMPORT is True
