"""Bounded deterministic measurements for LOAD01 operation and CPU metrics."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from math import fsum
from typing import Iterable, Sequence


ORDER_OPERATIONS = (
    "orders.submit",
    "orders.query",
    "orders.cancel",
)
OPERATION_OUTCOMES = ("success", "error", "timeout")
_SAMPLE_SEED = "load01-p0-operation-latency-v1"


def nearest_rank(values: Sequence[int | float], quantile: float) -> int | float:
    """Return the nearest-rank quantile (rank=ceil(q*n), one-based)."""

    if not values:
        raise ValueError("nearest-rank quantile requires at least one observation")
    if isinstance(quantile, bool) or not isinstance(quantile, (int, float)):
        raise TypeError("quantile must be a finite number in (0, 1]")
    if not math.isfinite(quantile) or not 0 < quantile <= 1:
        raise ValueError("quantile must be a finite number in (0, 1]")
    normalized: list[int | float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("observations must be finite numbers")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("observations must be finite numbers")
        normalized.append(value)
    rank = math.ceil(float(quantile) * len(normalized))
    return sorted(normalized)[max(0, rank - 1)]


def _deterministic_index(operation: str, observation_number: int) -> int:
    """Choose Algorithm-R's slot deterministically and without modulo bias."""

    upper_bound = observation_number
    range_size = 1 << 64
    accepted_limit = range_size - (range_size % upper_bound)
    retry = 0
    while True:
        payload = (
            f"{_SAMPLE_SEED}\x1f{operation}\x1f{observation_number}\x1f{retry}"
        ).encode("ascii")
        candidate = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
        if candidate < accepted_limit:
            return candidate % upper_bound
        retry += 1


@dataclass(slots=True)
class _OperationCounts:
    observed_count: int = 0
    success_count: int = 0
    error_count: int = 0
    timeout_count: int = 0
    sample_ns: list[int] = field(default_factory=list)


class OperationLatencyCollector:
    """Collect order-call latency with a deterministic bounded reservoir.

    Use one collector per strategy/profile instance so operation coverage cannot
    be satisfied by another strategy's calls. Timeouts are a subset of errors.
    Percentiles include every outcome in the retained deterministic Algorithm-R
    sample; counts always cover every recorded call. A summary reports status,
    never PASS. The caller classifies each outcome and supplies it to record();
    this collector does not infer timeout or error categories from exceptions.
    """

    def __init__(
        self,
        sample_capacity: int = 2048,
        *,
        required_operations: Sequence[str] = ORDER_OPERATIONS,
    ) -> None:
        if type(sample_capacity) is not int or sample_capacity <= 0:
            raise ValueError("sample_capacity must be a positive integer")
        selected_operations = tuple(required_operations)
        if (
            not selected_operations
            or any(
                type(operation) is not str or operation not in ORDER_OPERATIONS
                for operation in selected_operations
            )
            or len(set(selected_operations)) != len(selected_operations)
        ):
            raise ValueError("required_operations must be a non-empty unique subset")
        self.sample_capacity = sample_capacity
        self.required_operations = selected_operations
        self._operations = {name: _OperationCounts() for name in ORDER_OPERATIONS}

    def record(self, operation: str, elapsed_ns: int, outcome: str) -> None:
        if type(operation) is not str or operation not in self._operations:
            raise ValueError("operation must be one of the registered order calls")
        if type(elapsed_ns) is not int or elapsed_ns < 0:
            raise ValueError("elapsed_ns must be a non-negative integer")
        if type(outcome) is not str or outcome not in OPERATION_OUTCOMES:
            raise ValueError("outcome must be success, error, or timeout")

        row = self._operations[operation]
        row.observed_count += 1
        if outcome == "success":
            row.success_count += 1
        else:
            row.error_count += 1
            if outcome == "timeout":
                row.timeout_count += 1

        if len(row.sample_ns) < self.sample_capacity:
            row.sample_ns.append(elapsed_ns)
            return
        # Algorithm R, using an operation/call-index-derived SHA-256 value so
        # repeated runs over the same ordered observations produce the same
        # bounded sample without retaining every call.
        slot = _deterministic_index(operation, row.observed_count)
        if slot < self.sample_capacity:
            row.sample_ns[slot] = elapsed_ns

    def sampled_values_ns(self, operation: str) -> tuple[int, ...]:
        if type(operation) is not str or operation not in self._operations:
            raise ValueError("operation must be one of the registered order calls")
        return tuple(self._operations[operation].sample_ns)

    def summary(self) -> dict[str, object]:
        missing_required = [
            operation
            for operation in self.required_operations
            if self._operations[operation].observed_count == 0
        ]
        unobserved = [
            operation
            for operation, row in self._operations.items()
            if row.observed_count == 0
        ]
        operation_summary: dict[str, object] = {}
        for operation, row in self._operations.items():
            values = tuple(row.sample_ns)
            sampled_count = len(values)
            operation_summary[operation] = {
                "observed_count": row.observed_count,
                "success_count": row.success_count,
                "error_count": row.error_count,
                "timeout_count": row.timeout_count,
                "sampled_count": sampled_count,
                "sample_capacity": self.sample_capacity,
                "sample_coverage_fraction": (
                    sampled_count / row.observed_count if row.observed_count else None
                ),
                "full_population_retained": (
                    sampled_count == row.observed_count if row.observed_count else None
                ),
                "sample_method": "deterministic_sha256_algorithm_r",
                "percentiles_ns": {
                    "p50": nearest_rank(values, 0.50) if values else None,
                    "p95": nearest_rank(values, 0.95) if values else None,
                    "p99": nearest_rank(values, 0.99) if values else None,
                },
            }
        return {
            "schema_version": 1,
            "measurement_scope": "single_strategy_profile_instance",
            "status": (
                "INCOMPLETE_MISSING_REQUIRED_OPERATION"
                if missing_required
                else "OBSERVED_NOT_EVALUATED"
            ),
            "acceptance_status": "INCOMPLETE" if missing_required else "NOT_EVALUATED",
            "required_operations": list(self.required_operations),
            "missing_required_operations": missing_required,
            "unobserved_operations": unobserved,
            "required_operation_presence_complete": not missing_required,
            "operation_coverage_definition": (
                "each required operation was observed at least once in this "
                "single strategy/profile instance; workload-volume completeness "
                "must be checked against registered profile targets"
            ),
            "latency_sample_population": "all_observed_calls_including_errors_and_timeouts",
            "operations": operation_summary,
        }


def summarize_cpu_samples(
    samples_percent: Iterable[int | float], *, expected_samples: int | None
) -> dict[str, int | float | str | bool | None]:
    """Summarize actual system-CPU samples; absent expected coverage is not PASS."""

    if expected_samples is not None and (
        type(expected_samples) is not int or expected_samples <= 0
    ):
        raise ValueError("expected_samples must be a positive integer or None")
    samples: list[float] = []
    for value in samples_percent:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("CPU samples must be finite numbers from 0 through 100")
        try:
            normalized = float(value)
        except OverflowError as exc:
            raise ValueError("CPU samples must be finite numbers from 0 through 100") from exc
        if not math.isfinite(normalized) or not 0.0 <= normalized <= 100.0:
            raise ValueError("CPU samples must be finite numbers from 0 through 100")
        samples.append(normalized)

    count = len(samples)
    if not count:
        status = "INCOMPLETE_NO_SAMPLES"
    elif expected_samples is None:
        status = "OBSERVED_ONLY"
    elif count < expected_samples:
        status = "INCOMPLETE_EXPECTED_SAMPLES"
    else:
        status = "COMPLETE"
    coverage = (
        min(1.0, count / expected_samples) if expected_samples is not None else None
    )
    return {
        "status": status,
        "acceptance_status": "NOT_EVALUATED" if status == "COMPLETE" else "INCOMPLETE",
        "sample_count": count,
        "expected_sample_count": expected_samples,
        "coverage_fraction": coverage,
        "mean_percent": fsum(samples) / count if count else None,
        "p95_percent": nearest_rank(samples, 0.95) if count else None,
        "max_percent": max(samples) if count else None,
        "min_percent": min(samples) if count else None,
    }
