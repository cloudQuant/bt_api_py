"""Small LOAD01 adapter for the accepted DIM07 CTP-query quota wave.

The parent owns domain initialization and the 100-grant seed. Each strategy
constructs its own admission handle before entering its market loop, then starts
exactly two admission threads when its scheduled wave begins. The threads only
call GatewayQuotaAdmission; they do not dispatch a provider request. SQLite
connections are owned by each admission call and are closed before that thread
records its outcome. Callers must poll during the market loop and join only
after the wave has completed or during post-loop collection.
"""

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


QUOTA_DOMAIN_REF = "synthetic:dim07-hot-wave-v1"
STRATEGY_COUNT = 100
REQUESTS_PER_STRATEGY = 2
CAPACITY_PER_WINDOW = 100
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
_ACCOUNT_REF = "account:shared-hot"
_INSTRUMENT_REF = "instrument:hot"
_SEED_STRATEGY_REF = "strategy:window-seed"


@dataclass(frozen=True, slots=True)
class QuotaWaveOutcome:
    """One completed admission attempt with only bounded, non-secret facts."""

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
) -> GatewayQuotaAdmission:
    """Initialize the exact shared policy used by the accepted DIM07 wave."""

    return GatewayQuotaAdmission(
        database_path,
        QUOTA_DOMAIN_REF,
        capacity_per_window=CAPACITY_PER_WINDOW,
        window_seconds=WINDOW_SECONDS,
        reserved_capacity=RESERVED_CAPACITY,
        max_queue=MAX_QUEUE,
        max_queue_per_subject=MAX_QUEUE_PER_SUBJECT,
        max_backoff_seconds=MAX_BACKOFF_SECONDS,
        max_queue_wait_seconds=MAX_QUEUE_WAIT_SECONDS,
        poll_interval_seconds=POLL_INTERVAL_SECONDS,
    )


def seed_quota_window(
    admission: GatewayQuotaAdmission,
) -> tuple[GatewayQuotaGrant, ...]:
    """Consume the 100-slot window without rewriting grant timestamps."""

    grants: list[GatewayQuotaGrant] = []
    for index in range(CAPACITY_PER_WINDOW):
        subject = GatewayQuotaSubject(
            account_ref=_ACCOUNT_REF,
            strategy_ref=_SEED_STRATEGY_REF,
            instrument_ref=_INSTRUMENT_REF,
            run_ref=f"run:window-seed:{index:03d}",
        )
        grant = admission.admit(
            subject,
            GatewayQuotaOperation.CTP_QUERY,
            time.monotonic_ns() + int(SEED_DEADLINE_SECONDS * _NANOSECONDS),
        )
        if type(grant) is not GatewayQuotaGrant:
            raise AssertionError("DIM07 seed admission did not return a grant")
        grants.append(grant)
    if len({grant.run_digest for grant in grants}) != CAPACITY_PER_WINDOW:
        raise AssertionError("DIM07 seed grants did not retain unique run identities")
    return tuple(grants)


def _strategy_ref(strategy_index: int) -> str:
    if type(strategy_index) is not int or not 0 <= strategy_index < STRATEGY_COUNT:
        raise ValueError("strategy index must be in the fixed 0..99 wave")
    return f"strategy:hundred:{strategy_index:03d}"


class StrategyQuotaWave:
    """Non-blocking, two-thread CTP_QUERY admission wave for one strategy."""

    def __init__(
        self,
        database_path: str | PathLike[str],
        strategy_index: int,
    ) -> None:
        self.strategy_index = strategy_index
        self.strategy_ref = _strategy_ref(strategy_index)
        # Construct this before the strategy's market loop. Starting the wave
        # later only creates two threads and does no SQLite work on that loop.
        self._admission = create_quota_wave_admission(database_path)
        self._lock = threading.Lock()
        self._outcomes: list[QuotaWaveOutcome] = []
        self._threads: tuple[threading.Thread, ...] = ()
        self._started = False
        self._launching = False
        self._reaped = False

    def start(self) -> None:
        """Start both requests and return without waiting for admission."""

        with self._lock:
            if self._started:
                raise RuntimeError("strategy quota wave already started")
            self._started = True
            self._launching = True
            threads = tuple(
                threading.Thread(
                    target=self._admit_one,
                    args=(request_index,),
                    name=(
                        f"load01-dim07-ctp-query-"
                        f"{self.strategy_index:03d}-{request_index}"
                    ),
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
            # Keep any thread that actually started reachable so the caller
            # can still poll/join it. A partially started wave remains
            # incomplete at the outcome-summary boundary.
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
        run_ref = f"run:hundred:{self.strategy_index:03d}:{request_index}"
        subject = GatewayQuotaSubject(
            account_ref=_ACCOUNT_REF,
            strategy_ref=self.strategy_ref,
            instrument_ref=_INSTRUMENT_REF,
            run_ref=run_ref,
        )
        started_ns = time.monotonic_ns()
        deadline_ns = started_ns + int(REQUEST_DEADLINE_SECONDS * _NANOSECONDS)
        subject_digest: str | None = None
        run_digest: str | None = None
        try:
            grant = self._admission.admit(
                subject,
                GatewayQuotaOperation.CTP_QUERY,
                deadline_ns,
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
        result = QuotaWaveOutcome(
            strategy_index=self.strategy_index,
            request_index=request_index,
            outcome=outcome,
            subject_digest=subject_digest,
            run_digest=run_digest,
            elapsed_ns=max(0, time.monotonic_ns() - started_ns),
        )
        with self._lock:
            self._outcomes.append(result)

    @property
    def complete(self) -> bool:
        """Whether both attempts returned outcomes and their threads were reaped."""

        with self._lock:
            return self._reaped and len(self._outcomes) == REQUESTS_PER_STRATEGY

    def poll(self) -> tuple[QuotaWaveOutcome, ...]:
        """Return observed outcomes and reap finished threads without waiting."""

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
        """Wait only at a caller-chosen collection boundary, then reap threads.

        Each admission call closes its own SQLite connections before recording
        an outcome. A timeout leaves live threads owned by this object; this
        method does not cancel them, refund grants, or discard their custody.
        """

        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds < 0
        ):
            raise ValueError("join timeout must be a finite non-negative duration")
        with self._lock:
            if not self._started:
                raise RuntimeError("strategy quota wave has not started")
            if self._launching:
                raise RuntimeError("cannot join while strategy quota threads are launching")
            threads = self._threads
        deadline = time.monotonic() + float(timeout_seconds)
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        alive = tuple(thread.name for thread in threads if thread.is_alive())
        if alive:
            raise TimeoutError("DIM07 quota admission threads remain active")
        return self.poll()


def summarize_quota_wave(
    outcomes: Iterable[QuotaWaveOutcome],
) -> QuotaWaveSummary:
    """Validate the fixed 200-request fairness/deadline result envelope."""

    rows = tuple(outcomes)
    expected_offered = STRATEGY_COUNT * REQUESTS_PER_STRATEGY
    if len(rows) != expected_offered or any(type(row) is not QuotaWaveOutcome for row in rows):
        raise AssertionError("DIM07 wave must contain exactly 200 typed outcomes")
    by_slot: dict[int, list[QuotaWaveOutcome]] = {
        index: [] for index in range(STRATEGY_COUNT)
    }
    for row in rows:
        if (
            type(row.strategy_index) is not int
            or row.strategy_index not in by_slot
            or type(row.request_index) is not int
            or not 0 <= row.request_index < REQUESTS_PER_STRATEGY
            or type(row.elapsed_ns) is not int
            or row.elapsed_ns < 0
        ):
            raise AssertionError("DIM07 wave outcome has an invalid slot or duration")
        by_slot[row.strategy_index].append(row)
    if any(
        len(slot_rows) != REQUESTS_PER_STRATEGY
        or {row.request_index for row in slot_rows} != {0, 1}
        for slot_rows in by_slot.values()
    ):
        raise AssertionError("each DIM07 strategy must report both request outcomes")

    granted = [row for row in rows if row.outcome == "granted"]
    denied = [row for row in rows if row.outcome == "DEADLINE_EXCEEDED"]
    if len(granted) != STRATEGY_COUNT or len(denied) != STRATEGY_COUNT:
        raise AssertionError("DIM07 wave must produce 100 grants and 100 deadline denials")
    if any(
        row.outcome not in {"granted", "DEADLINE_EXCEEDED"} for row in rows
    ):
        raise AssertionError("DIM07 wave contains an unexpected admission outcome")
    grant_counts = tuple(
        sum(row.strategy_index == index for row in granted)
        for index in range(STRATEGY_COUNT)
    )
    denial_counts = tuple(
        sum(row.strategy_index == index for row in denied)
        for index in range(STRATEGY_COUNT)
    )
    if grant_counts != (1,) * STRATEGY_COUNT or denial_counts != (1,) * STRATEGY_COUNT:
        raise AssertionError("each DIM07 strategy must receive one grant and one denial")
    if any(
        row.elapsed_ns >= int(REQUEST_DEADLINE_SECONDS * _NANOSECONDS)
        for row in granted
    ):
        raise AssertionError("DIM07 grant returned at or after its request deadline")
    if any(
        type(row.subject_digest) is not str or type(row.run_digest) is not str
        for row in granted
    ):
        raise AssertionError("DIM07 grants must report subject and run digests")
    if len({row.subject_digest for row in granted}) != STRATEGY_COUNT:
        raise AssertionError("DIM07 granted strategy subjects are not unique")
    if len({row.run_digest for row in granted}) != STRATEGY_COUNT:
        raise AssertionError("DIM07 granted request runs are not unique")

    grant_latencies = sorted(row.elapsed_ns for row in granted)
    denial_latencies = sorted(row.elapsed_ns for row in denied)
    grant_p95_ns = grant_latencies[math.ceil(0.95 * len(grant_latencies)) - 1]
    denial_p95_ns = denial_latencies[math.ceil(0.95 * len(denial_latencies)) - 1]
    denial_limit_ns = int(
        (REQUEST_DEADLINE_SECONDS + DENIAL_P95_CLEANUP_SECONDS) * _NANOSECONDS
    )
    if denial_p95_ns > denial_limit_ns:
        raise AssertionError("DIM07 deadline-denial p95 exceeded its accepted cleanup bound")
    return QuotaWaveSummary(
        offered=expected_offered,
        granted=len(granted),
        deadline_exceeded=len(denied),
        grant_p95_ns=grant_p95_ns,
        denial_p95_ns=denial_p95_ns,
        grants_by_strategy=grant_counts,
        denials_by_strategy=denial_counts,
    )


def finish_quota_wave(
    admission: GatewayQuotaAdmission,
    outcomes: Iterable[QuotaWaveOutcome],
) -> QuotaWaveSummary:
    """Check global persistent waiter cleanup after every worker was joined."""

    summary = summarize_quota_wave(outcomes)
    if admission.pending_waiter_count != 0:
        raise AssertionError("DIM07 quota domain retained pending waiters after collection")
    return summary
