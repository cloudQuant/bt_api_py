from __future__ import annotations

import pytest

from tests.unit._load01_metrics import (
    ORDER_OPERATIONS,
    OperationLatencyCollector,
    nearest_rank,
    summarize_cpu_samples,
)


def test_nearest_rank_uses_one_based_ceiling_rank() -> None:
    values = tuple(range(1, 101))

    assert nearest_rank(values, 0.50) == 50
    assert nearest_rank(values, 0.95) == 95
    assert nearest_rank(values, 0.99) == 99
    assert nearest_rank(values, 1.0) == 100


def test_operation_collector_has_deterministic_bounded_reservoir_and_counts() -> None:
    collectors = [OperationLatencyCollector(sample_capacity=7) for _ in range(2)]
    outcomes = ("success", "success", "error", "timeout")
    for collector in collectors:
        for index in range(100):
            collector.record(
                "orders.submit",
                index * 1_000,
                outcomes[index % len(outcomes)],
            )

    first = collectors[0]
    second = collectors[1]
    sample = first.sampled_values_ns("orders.submit")
    assert sample == second.sampled_values_ns("orders.submit")
    assert len(sample) == 7
    assert all(value in range(0, 100_000, 1_000) for value in sample)

    summary = first.summary()
    assert summary["measurement_scope"] == "single_strategy_profile_instance"
    assert summary["latency_sample_population"] == (
        "all_observed_calls_including_errors_and_timeouts"
    )
    submit = summary["operations"]["orders.submit"]
    assert submit["observed_count"] == 100
    assert submit["success_count"] == 50
    assert submit["error_count"] == 50
    assert submit["timeout_count"] == 25
    assert submit["sampled_count"] == 7
    assert submit["sample_capacity"] == 7
    assert submit["sample_coverage_fraction"] == pytest.approx(0.07)
    assert submit["full_population_retained"] is False
    assert submit["percentiles_ns"]["p95"] == nearest_rank(sample, 0.95)
    assert summary["acceptance_status"] == "INCOMPLETE"
    assert summary["missing_required_operations"] == ["orders.query", "orders.cancel"]
    assert summary["unobserved_operations"] == ["orders.query", "orders.cancel"]
    query = summary["operations"]["orders.query"]
    assert query["observed_count"] == 0
    assert query["sampled_count"] == 0
    assert query["sample_coverage_fraction"] is None
    assert query["full_population_retained"] is None


def test_complete_operation_observation_is_never_implicitly_a_pass() -> None:
    collector = OperationLatencyCollector(sample_capacity=16)
    for operation in ORDER_OPERATIONS:
        collector.record(operation, 123_000, "success")
        collector.record(operation, 456_000, "timeout")

    summary = collector.summary()
    assert summary["required_operation_presence_complete"] is True
    assert "workload-volume completeness" in summary["operation_coverage_definition"]
    assert summary["missing_required_operations"] == []
    assert summary["status"] == "OBSERVED_NOT_EVALUATED"
    assert summary["acceptance_status"] == "NOT_EVALUATED"
    for operation in ORDER_OPERATIONS:
        row = summary["operations"][operation]
        assert row["observed_count"] == 2
        assert row["success_count"] == 1
        assert row["error_count"] == 1
        assert row["timeout_count"] == 1
        assert row["full_population_retained"] is True


def test_profile_can_mark_only_exercised_operations_required() -> None:
    collector = OperationLatencyCollector(
        required_operations=("orders.submit", "orders.cancel")
    )
    collector.record("orders.submit", 10, "success")
    collector.record("orders.cancel", 20, "success")

    summary = collector.summary()
    assert summary["acceptance_status"] == "NOT_EVALUATED"
    assert summary["required_operations"] == ["orders.submit", "orders.cancel"]
    assert summary["missing_required_operations"] == []
    assert summary["unobserved_operations"] == ["orders.query"]
    assert summary["operations"]["orders.query"]["observed_count"] == 0


@pytest.mark.parametrize(
    ("operation", "elapsed_ns", "outcome", "error"),
    [
        ("orders.unknown", 1, "success", ValueError),
        ("orders.submit", -1, "success", ValueError),
        ("orders.submit", True, "success", ValueError),
        ("orders.submit", 1, "other", ValueError),
    ],
)
def test_operation_collector_rejects_unregistered_or_invalid_observations(
    operation: str,
    elapsed_ns: int,
    outcome: str,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        OperationLatencyCollector().record(operation, elapsed_ns, outcome)


def test_cpu_summary_uses_actual_values_and_never_passes_missing_coverage() -> None:
    complete = summarize_cpu_samples([0, 20, 40, 80, 100], expected_samples=5)
    assert complete["status"] == "COMPLETE"
    assert complete["acceptance_status"] == "NOT_EVALUATED"
    assert complete["sample_count"] == 5
    assert complete["coverage_fraction"] == 1.0
    assert complete["mean_percent"] == pytest.approx(48.0)
    assert complete["p95_percent"] == 100.0
    assert complete["max_percent"] == 100.0

    incomplete = summarize_cpu_samples([20.0, 40.0], expected_samples=5)
    assert incomplete["status"] == "INCOMPLETE_EXPECTED_SAMPLES"
    assert incomplete["acceptance_status"] == "INCOMPLETE"
    assert incomplete["coverage_fraction"] == pytest.approx(0.4)

    missing = summarize_cpu_samples([], expected_samples=5)
    assert missing["status"] == "INCOMPLETE_NO_SAMPLES"
    assert missing["acceptance_status"] == "INCOMPLETE"
    assert missing["p95_percent"] is None


def test_cpu_samples_without_an_expected_count_are_observed_only() -> None:
    summary = summarize_cpu_samples([10.0], expected_samples=None)

    assert summary["status"] == "OBSERVED_ONLY"
    assert summary["acceptance_status"] == "INCOMPLETE"
    assert summary["coverage_fraction"] is None


@pytest.mark.parametrize("samples", [[-0.1], [100.1], [float("nan")], [True]])
def test_cpu_summary_rejects_invalid_samples(samples: list[object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        summarize_cpu_samples(samples, expected_samples=1)  # type: ignore[arg-type]
