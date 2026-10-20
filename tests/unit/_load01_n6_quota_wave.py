"""LOAD01-sized N6 admission wave, separate from DIM07's 100-strategy helper."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from os import PathLike
from typing import Iterable

from bt_api_gateway.quota_admission import (
    GatewayQuotaAdmission,
    GatewayQuotaAdmissionError,
    GatewayQuotaGrant,
    GatewayQuotaOperation,
    GatewayQuotaSubject,
)


QUOTA_DOMAIN_REF = "synthetic:load01-n6-hot-wave-v1"
STRATEGY_COUNT = 10
REQUESTS_PER_STRATEGY = 2
CAPACITY_PER_WINDOW = 10
WINDOW_SECONDS = 20.0
RESERVED_CAPACITY = 0
MAX_QUEUE = 256
MAX_QUEUE_PER_SUBJECT = 2
MAX_BACKOFF_SECONDS = 1.0
MAX_QUEUE_WAIT_SECONDS = 40.0
POLL_INTERVAL_SECONDS = 0.025
REQUEST_DEADLINE_SECONDS = 30.0
SEED_DEADLINE_SECONDS = 5.0
RESULT_COLLECTION_SECONDS = REQUEST_DEADLINE_SECONDS + 20.0
DENIAL_P95_CLEANUP_SECONDS = 0.75

_NANOSECONDS = 1_000_000_000
_ACCOUNT_REF = "account:load01-n6-shared"
_INSTRUMENT_REF = "instrument:load01-n6-hot"
_SEED_STRATEGY_REF = "strategy:load01-n6-window-seed"


@dataclass(frozen=True, slots=True)
class QuotaWaveOutcome:
    """One bounded admission receipt without provider request data."""

    strategy_index: int
    request_index: int
    outcome: str
    subject_digest: str | None
    run_digest: str | None
    elapsed_ns: int


@dataclass(frozen=True, slots=True)
class QuotaWaveSummary:
    offered: int
    granted: int
    deadline_exceeded: int
    grant_p95_ns: int
    denial_p95_ns: int
    grants_by_strategy: tuple[int, ...]
    denials_by_strategy: tuple[int, ...]


def create_quota_wave_admission(
    database_path: str | PathLike[str],
    *,
    window_seconds: float = WINDOW_SECONDS,
) -> GatewayQuotaAdmission:
    """Create the shared 10-slot LOAD01 window with its registered limits."""

    return GatewayQuotaAdmission(
        database_path,
        QUOTA_DOMAIN_REF,
        capacity_per_window=CAPACITY_PER_WINDOW,
        window_seconds=window_seconds,
        reserved_capacity=RESERVED_CAPACITY,
        max_queue=MAX_QUEUE,
        max_queue_per_subject=MAX_QUEUE_PER_SUBJECT,
        max_backoff_seconds=MAX_BACKOFF_SECONDS,
        max_queue_wait_seconds=MAX_QUEUE_WAIT_SECONDS,
        poll_interval_seconds=POLL_INTERVAL_SECONDS,
    )


def seed_quota_window(
    admission: GatewayQuotaAdmission,
    *,
    seed_deadline_seconds: float = SEED_DEADLINE_SECONDS,
) -> tuple[GatewayQuotaGrant, ...]:
    """Consume exactly ten slots before the LOAD01 strategy requests begin."""

    grants: list[GatewayQuotaGrant] = []
    for index in range(CAPACITY_PER_WINDOW):
        subject = GatewayQuotaSubject(
            account_ref=_ACCOUNT_REF,
            strategy_ref=_SEED_STRATEGY_REF,
            instrument_ref=_INSTRUMENT_REF,
            run_ref=f"run:load01-n6-window-seed:{index:02d}",
        )
        grant = admission.admit(
            subject,
            GatewayQuotaOperation.CTP_QUERY,
            time.monotonic_ns() + int(seed_deadline_seconds * _NANOSECONDS),
        )
        if type(grant) is not GatewayQuotaGrant:
            raise AssertionError("LOAD01 N6 seed did not return a grant")
        grants.append(grant)
    if len({grant.run_digest for grant in grants}) != CAPACITY_PER_WINDOW:
        raise AssertionError("LOAD01 N6 seed grants did not retain unique run identities")
    return tuple(grants)


def _strategy_ref(strategy_index: int) -> str:
    if type(strategy_index) is not int or not 0 <= strategy_index < STRATEGY_COUNT:
        raise ValueError("LOAD01 N6 strategy index must be in the fixed 0..9 wave")
    return f"strategy:load01-n6:{strategy_index:02d}"


class StrategyQuotaWave:
    """Two non-blocking admission requests for one strategy identity."""

    def __init__(
        self,
        database_path: str | PathLike[str],
        strategy_index: int,
        *,
        window_seconds: float = WINDOW_SECONDS,
        request_deadline_seconds: float = REQUEST_DEADLINE_SECONDS,
    ) -> None:
        self.strategy_index = strategy_index
        self.strategy_ref = _strategy_ref(strategy_index)
        self._database_path = database_path
        self._window_seconds = window_seconds
        self._request_deadline_seconds = request_deadline_seconds
        self._admission = create_quota_wave_admission(
            database_path, window_seconds=window_seconds
        )
        self._lock = threading.Lock()
        self._outcomes: list[QuotaWaveOutcome] = []
        self._threads: tuple[threading.Thread, ...] = ()
        self._started = False
        self._launching = False
        self._reaped = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("LOAD01 N6 strategy quota wave already started")
            self._started = True
            self._launching = True
            threads = tuple(
                threading.Thread(
                    target=self._admit_one,
                    args=(request_index,),
                    name=f"load01-n6-query-{self.strategy_index:02d}-{request_index}",
                    daemon=False,
                )
                for request_index in range(REQUESTS_PER_STRATEGY)
            )
        try:
            started_threads: list[threading.Thread] = []
            for thread in threads:
                thread.start()
                started_threads.append(thread)
                with self._lock:
                    self._threads = tuple(started_threads)
        except BaseException:
            started_threads = [thread for thread in threads if thread.ident is not None]
            with self._lock:
                self._threads = tuple(started_threads)
                self._launching = False
                self._reaped = not started_threads
            raise
        with self._lock:
            self._threads = tuple(started_threads)
            self._launching = False

    def _admit_one(self, request_index: int) -> None:
        started_ns = time.monotonic_ns()
        deadline_ns = started_ns + int(self._request_deadline_seconds * _NANOSECONDS)
        subject = GatewayQuotaSubject(
            account_ref=_ACCOUNT_REF,
            strategy_ref=self.strategy_ref,
            instrument_ref=_INSTRUMENT_REF,
            run_ref=f"run:load01-n6:{self.strategy_index:02d}:{request_index}",
        )
        subject_digest: str | None = None
        run_digest: str | None = None
        try:
            grant = self._admission.admit(
                subject, GatewayQuotaOperation.CTP_QUERY, deadline_ns
            )
            if type(grant) is not GatewayQuotaGrant:
                outcome = "UNEXPECTED:INVALID_GRANT"
            else:
                outcome = "granted"
                subject_digest = grant.subject_digest
                run_digest = grant.run_digest
        except GatewayQuotaAdmissionError as exc:
            outcome = exc.code
        except Exception as exc:
            outcome = f"UNEXPECTED:{type(exc).__name__}"
        row = QuotaWaveOutcome(
            strategy_index=self.strategy_index,
            request_index=request_index,
            outcome=outcome,
            subject_digest=subject_digest,
            run_digest=run_digest,
            elapsed_ns=max(0, time.monotonic_ns() - started_ns),
        )
        with self._lock:
            self._outcomes.append(row)

    @property
    def complete(self) -> bool:
        with self._lock:
            return self._reaped and len(self._outcomes) == REQUESTS_PER_STRATEGY

    def poll(self) -> tuple[QuotaWaveOutcome, ...]:
        with self._lock:
            if self._launching:
                return tuple(sorted(self._outcomes, key=lambda row: row.request_index))
            threads = self._threads
        if threads and all(not thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(timeout=0)
            with self._lock:
                self._threads = ()
                self._reaped = True
        with self._lock:
            return tuple(sorted(self._outcomes, key=lambda row: row.request_index))

    def join(
        self,
        timeout_seconds: float = RESULT_COLLECTION_SECONDS,
    ) -> tuple[QuotaWaveOutcome, ...]:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds < 0
        ):
            raise ValueError("LOAD01 N6 join timeout must be finite and non-negative")
        with self._lock:
            if not self._started:
                raise RuntimeError("LOAD01 N6 strategy quota wave has not started")
            if self._launching:
                raise RuntimeError("cannot join while LOAD01 N6 quota threads are launching")
            threads = self._threads
        deadline = time.monotonic() + float(timeout_seconds)
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            raise TimeoutError("LOAD01 N6 quota admission threads remain active")
        return self.poll()


def summarize_quota_wave(
    outcomes: Iterable[QuotaWaveOutcome],
) -> QuotaWaveSummary:
    rows = tuple(outcomes)
    expected_offered = STRATEGY_COUNT * REQUESTS_PER_STRATEGY
    if len(rows) != expected_offered or any(type(row) is not QuotaWaveOutcome for row in rows):
        raise AssertionError("LOAD01 N6 wave must contain exactly 20 typed outcomes")
    by_strategy: dict[int, list[QuotaWaveOutcome]] = {
        index: [] for index in range(STRATEGY_COUNT)
    }
    for row in rows:
        if (
            type(row.strategy_index) is not int
            or row.strategy_index not in by_strategy
            or type(row.request_index) is not int
            or row.request_index not in {0, 1}
            or type(row.elapsed_ns) is not int
            or row.elapsed_ns < 0
        ):
            raise AssertionError("LOAD01 N6 outcome has an invalid identity or duration")
        by_strategy[row.strategy_index].append(row)
    if any(
        len(rows_for_strategy) != REQUESTS_PER_STRATEGY
        or {row.request_index for row in rows_for_strategy} != {0, 1}
        for rows_for_strategy in by_strategy.values()
    ):
        raise AssertionError("each LOAD01 N6 strategy must return both outcomes")

    granted = [row for row in rows if row.outcome == "granted"]
    denied = [row for row in rows if row.outcome == "DEADLINE_EXCEEDED"]
    if len(granted) != CAPACITY_PER_WINDOW or len(denied) != CAPACITY_PER_WINDOW:
        raise AssertionError("LOAD01 N6 wave must produce 10 grants and 10 deadline denials")
    if any(row.outcome not in {"granted", "DEADLINE_EXCEEDED"} for row in rows):
        raise AssertionError("LOAD01 N6 wave contains an unexpected outcome")
    grants_by_strategy = tuple(
        sum(row.strategy_index == index for row in granted)
        for index in range(STRATEGY_COUNT)
    )
    denials_by_strategy = tuple(
        sum(row.strategy_index == index for row in denied)
        for index in range(STRATEGY_COUNT)
    )
    if grants_by_strategy != (1,) * STRATEGY_COUNT or denials_by_strategy != (1,) * STRATEGY_COUNT:
        raise AssertionError("each LOAD01 N6 strategy must receive one grant and one denial")
    if any(
        type(row.subject_digest) is not str or type(row.run_digest) is not str
        for row in granted
    ):
        raise AssertionError("LOAD01 N6 grants must report subject and run digests")
    if len({row.subject_digest for row in granted}) != STRATEGY_COUNT:
        raise AssertionError("LOAD01 N6 grant subjects are not unique")
    if len({row.run_digest for row in granted}) != STRATEGY_COUNT:
        raise AssertionError("LOAD01 N6 grant runs are not unique")
    if any(row.elapsed_ns >= int(REQUEST_DEADLINE_SECONDS * _NANOSECONDS) for row in granted):
        raise AssertionError("LOAD01 N6 grant returned at or after its deadline")

    grant_latencies = sorted(row.elapsed_ns for row in granted)
    denial_latencies = sorted(row.elapsed_ns for row in denied)
    grant_p95_ns = grant_latencies[math.ceil(0.95 * len(grant_latencies)) - 1]
    denial_p95_ns = denial_latencies[math.ceil(0.95 * len(denial_latencies)) - 1]
    denial_limit_ns = int(
        (REQUEST_DEADLINE_SECONDS + DENIAL_P95_CLEANUP_SECONDS) * _NANOSECONDS
    )
    if denial_p95_ns > denial_limit_ns:
        raise AssertionError("LOAD01 N6 denial p95 exceeded its registered cleanup bound")
    return QuotaWaveSummary(
        offered=expected_offered,
        granted=len(granted),
        deadline_exceeded=len(denied),
        grant_p95_ns=grant_p95_ns,
        denial_p95_ns=denial_p95_ns,
        grants_by_strategy=grants_by_strategy,
        denials_by_strategy=denials_by_strategy,
    )


def finish_quota_wave(
    admission: GatewayQuotaAdmission,
    outcomes: Iterable[QuotaWaveOutcome],
) -> QuotaWaveSummary:
    summary = summarize_quota_wave(outcomes)
    if admission.pending_waiter_count != 0:
        raise AssertionError("LOAD01 N6 quota domain retained pending waiters")
    return summary
