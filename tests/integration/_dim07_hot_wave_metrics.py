"""Bounded, identifier-free metrics for synthetic Gateway quota waves."""

from __future__ import annotations

import math
import threading

from bt_api_gateway.quota_admission import GatewayQuotaOperation

_MAX_STRATEGIES = 100
_LATENCY_BUCKETS_NS = (1_000_000, 10_000_000, 100_000_000, 1_000_000_000)
_OUTCOMES = frozenset({"granted", "DEADLINE_EXCEEDED", "QUEUE_FULL", "OWNER_UNAVAILABLE"})
_OPERATIONS = frozenset(operation.value for operation in GatewayQuotaOperation)


class BoundedQuotaWaveMetrics:
    """Keep only fixed counters and histograms; never retain account/key/IP IDs."""

    def __init__(self, strategy_count: int = _MAX_STRATEGIES) -> None:
        if type(strategy_count) is not int or not 1 <= strategy_count <= _MAX_STRATEGIES:
            raise ValueError("strategy_count must be between 1 and 100")
        self._strategy_grants = [0] * strategy_count
        self._offered = dict.fromkeys(sorted(_OPERATIONS), 0)
        self._granted = dict.fromkeys(sorted(_OPERATIONS), 0)
        self._deadline_exceeded = dict.fromkeys(sorted(_OPERATIONS), 0)
        self._outcomes = dict.fromkeys(sorted(_OUTCOMES), 0)
        self._latency_buckets = {
            operation: [0] * (len(_LATENCY_BUCKETS_NS) + 1)
            for operation in sorted(_OPERATIONS)
        }
        self._dispatches = dict.fromkeys(sorted(_OPERATIONS), 0)
        self._dispatch_after_deadline = 0
        self._provider_429 = 0
        self._reserved_cancel_grants = 0
        self._login_attempts = 0
        self._reconnect_attempts = 0
        self._lock = threading.Lock()

    def record_admission(
        self,
        operation: GatewayQuotaOperation,
        strategy_slot: int,
        outcome: str,
        latency_ns: int,
    ) -> None:
        if (
            type(operation) is not GatewayQuotaOperation
            or operation.value not in _OPERATIONS
            or type(strategy_slot) is not int
            or not 0 <= strategy_slot < len(self._strategy_grants)
            or type(outcome) is not str
            or outcome not in _OUTCOMES
            or type(latency_ns) is not int
            or latency_ns < 0
        ):
            raise ValueError("invalid bounded quota metric")
        bucket = next(
            (index for index, edge in enumerate(_LATENCY_BUCKETS_NS) if latency_ns <= edge),
            len(_LATENCY_BUCKETS_NS),
        )
        with self._lock:
            self._offered[operation.value] += 1
            self._latency_buckets[operation.value][bucket] += 1
            self._outcomes[outcome] += 1
            if outcome == "granted":
                self._granted[operation.value] += 1
                self._strategy_grants[strategy_slot] += 1
            elif outcome == "DEADLINE_EXCEEDED":
                self._deadline_exceeded[operation.value] += 1

    def record_dispatch(
        self,
        operation: GatewayQuotaOperation,
        *,
        strategy_slot: int,
        dispatch_ns: int,
        deadline_ns: int,
    ) -> None:
        if (
            type(operation) is not GatewayQuotaOperation
            or operation.value not in _OPERATIONS
            or type(strategy_slot) is not int
            or not 0 <= strategy_slot < len(self._strategy_grants)
            or type(dispatch_ns) is not int
            or type(deadline_ns) is not int
            or min(dispatch_ns, deadline_ns) < 0
        ):
            raise ValueError("invalid bounded dispatch metric")
        with self._lock:
            self._dispatches[operation.value] += 1
            if dispatch_ns >= deadline_ns:
                self._dispatch_after_deadline += 1

    def record_provider_429(self, retry_after_seconds: int | float) -> None:
        if (
            isinstance(retry_after_seconds, bool)
            or not isinstance(retry_after_seconds, (int, float))
            or not math.isfinite(float(retry_after_seconds))
            or retry_after_seconds < 0
        ):
            raise ValueError("invalid bounded 429 metric")
        with self._lock:
            self._provider_429 += 1

    def record_reserved_cancel_grant(self) -> None:
        with self._lock:
            self._reserved_cancel_grants += 1

    def record_session_operation(self, operation: GatewayQuotaOperation) -> None:
        if type(operation) is not GatewayQuotaOperation or operation not in {
            GatewayQuotaOperation.LOGIN,
            GatewayQuotaOperation.RECONNECT,
        }:
            raise ValueError("session metric accepts LOGIN or RECONNECT only")
        with self._lock:
            if operation is GatewayQuotaOperation.LOGIN:
                self._login_attempts += 1
            else:
                self._reconnect_attempts += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "latency_bucket_upper_bounds_ms": tuple(
                    edge / 1_000_000 for edge in _LATENCY_BUCKETS_NS
                ),
                "offered_by_operation": dict(self._offered),
                "granted_by_operation": dict(self._granted),
                "deadline_exceeded_by_operation": dict(self._deadline_exceeded),
                "outcomes": dict(self._outcomes),
                "admission_latency_bucket_counts": {
                    operation: tuple(counts)
                    for operation, counts in self._latency_buckets.items()
                },
                "provider_dispatches_by_operation": dict(self._dispatches),
                "dispatch_after_deadline": self._dispatch_after_deadline,
                "provider_429_count": self._provider_429,
                "reserved_cancel_grants": self._reserved_cancel_grants,
                "login_attempts": self._login_attempts,
                "reconnect_attempts": self._reconnect_attempts,
                "strategy_grants": tuple(self._strategy_grants),
            }
