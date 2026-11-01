"""Callback-driven scheduling and backoff for managed-order refresh.

This SDK component starts no thread and creates no service or provider.
The caller owns refresh, lifecycle, and any local-object projections.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

__all__ = [
    "ManagedOrderRefreshLoop",
    "ManagedRefreshStats",
    "RefreshLoopError",
]


class RefreshLoopError(ValueError):
    """The refresh loop was configured with an unusable value."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ManagedRefreshStats:
    """Immutable counters for one refresh loop."""

    attempts: int
    applied: int
    failures: int
    last_error_code: Optional[str]
    current_interval_seconds: float
    last_applied_refs: tuple[Any, ...]


class ManagedOrderRefreshLoop:
    """Decide when to invoke the caller-owned refresh callback."""

    def __init__(
        self,
        refresh: Callable[..., Any],
        *,
        partial_refs_from_error: Optional[Callable[[BaseException], tuple[Any, ...]]] = None,
        unavailable_code: str = "REFRESH_CALLBACK_REQUIRED",
        interval_seconds: float = 1.0,
        max_interval_seconds: float = 30.0,
        backoff_factor: float = 2.0,
        limit: int = 32,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(refresh):
            raise RefreshLoopError(unavailable_code)
        if partial_refs_from_error is not None and not callable(partial_refs_from_error):
            raise RefreshLoopError("INVALID_PARTIAL_REFS_CALLBACK")
        if type(limit) is not int or limit <= 0:
            raise RefreshLoopError("INVALID_LIMIT")
        if not callable(monotonic_clock):
            raise RefreshLoopError("INVALID_CLOCK")
        if not _positive(interval_seconds) or not _positive(max_interval_seconds):
            raise RefreshLoopError("INVALID_INTERVAL")
        if float(max_interval_seconds) < float(interval_seconds):
            raise RefreshLoopError("INVALID_INTERVAL")
        if not _finite_number(backoff_factor) or float(backoff_factor) < 1:
            raise RefreshLoopError("INVALID_BACKOFF")

        self._refresh = refresh
        self._partial_refs_from_error = partial_refs_from_error
        self._base_interval = float(interval_seconds)
        self._max_interval = float(max_interval_seconds)
        self._backoff = float(backoff_factor)
        self._limit = limit
        self._clock = monotonic_clock

        self._interval = self._base_interval
        self._next_due = self._clock()
        self._attempts = 0
        self._applied = 0
        self._failures = 0
        self._last_error_code: Optional[str] = None
        self._last_applied_refs: tuple[Any, ...] = ()

    def due(self) -> bool:
        """Return whether a pass is due right now."""

        return self._clock() >= self._next_due

    def next_delay(self) -> float:
        """Return the seconds remaining until the next due pass (never negative)."""

        return max(0.0, self._next_due - self._clock())

    def run_once(self) -> tuple[Any, ...]:
        """Run one pass if it is due; return the applied order refs.

        A due pass always advances the schedule, whether it succeeded or not,
        so a failing service cannot turn into a busy loop. A failed pass returns
        an empty tuple; any refs applied before the failure remain in ``stats``.
        """

        if not self.due():
            return ()
        self._attempts += 1
        try:
            applied = self._refresh(limit=self._limit)
        except Exception as error:  # noqa: BLE001 - a degraded pass must not break the caller
            partial_refs = (
                tuple(self._partial_refs_from_error(error))
                if self._partial_refs_from_error is not None
                else ()
            )
            self._failures += 1
            self._last_error_code = _error_code(error)
            self._applied += len(partial_refs)
            self._last_applied_refs = partial_refs
            self._interval = min(self._max_interval, self._interval * self._backoff)
            self._next_due = self._clock() + self._interval
            return ()
        refs = tuple(applied) if isinstance(applied, (tuple, list)) else ()
        self._last_error_code = None
        self._interval = self._base_interval
        self._next_due = self._clock() + self._interval
        self._applied += len(refs)
        self._last_applied_refs = refs
        return refs

    @property
    def stats(self) -> ManagedRefreshStats:
        return ManagedRefreshStats(
            attempts=self._attempts,
            applied=self._applied,
            failures=self._failures,
            last_error_code=self._last_error_code,
            current_interval_seconds=self._interval,
            last_applied_refs=self._last_applied_refs,
        )


def _positive(value: Any) -> bool:
    return _finite_number(value) and float(value) > 0


def _finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _error_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code and code.isascii() and len(code) <= 64:
        return code
    return type(error).__name__ if type(error).__name__.isascii() else "REFRESH_FAILED"
