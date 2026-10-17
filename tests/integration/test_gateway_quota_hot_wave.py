from __future__ import annotations

import json
import math
import multiprocessing
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil
import pytest
from bt_api_base.account_protocol import AccountKey
from bt_api_base.exceptions import RateLimitError
from bt_api_gateway.quota_admission import (
    GatewayQuotaAdmission,
    GatewayQuotaAdmissionError,
    GatewayQuotaDispatchAdmission,
    GatewayQuotaOperation,
    GatewayQuotaProviderSession,
    GatewayQuotaSubject,
)

from tests.integration._dim07_hot_wave_metrics import BoundedQuotaWaveMetrics

_DOMAIN_REF = "synthetic:dim07-hot-wave-v1"
_CAPACITY = 8
_WINDOW_SECONDS = 60.0
_PROCESS_COUNT = 5
_STRATEGIES_PER_PROCESS = 20
_BATCH_SIZE = 2
_BATCH_COUNT = _STRATEGIES_PER_PROCESS // _BATCH_SIZE
_TOTAL_REQUESTS = _PROCESS_COUNT * _STRATEGIES_PER_PROCESS
_REQUEST_DEADLINE_SECONDS = 0.75
_HUNDRED_PROCESS_STRATEGIES = 100
_HUNDRED_PROCESS_REQUESTS_PER_STRATEGY = 2
_HUNDRED_PROCESS_WINDOW_SECONDS = 20.0
_HUNDRED_PROCESS_DEADLINE_SECONDS = 30.0
_HUNDRED_PROCESS_RESULT_COLLECTION_SECONDS = _HUNDRED_PROCESS_DEADLINE_SECONDS + 20.0
# Keep the full LOAD01 resource preflight unchanged: 112 measured child means
# plus the existing 2 GiB reserve, even though this isolated wave uses 100.
_LOAD01_REQUIRED_AVAILABLE_BYTES = 112 * 72 * 1024**2 + 2 * 1024**3
_MINIMUM_FREE_RESERVE_BYTES = 2 * 1024**3
_CHILD_CLEANUP_TIMEOUT_SECONDS = 5.0


def _collect_results_before_deadline(
    results: Any,
    result_count: int,
    timeout_seconds: float,
    *,
    monotonic: Any = time.monotonic,
) -> list[Any]:
    deadline = monotonic() + timeout_seconds
    collected: list[Any] = []
    for _ in range(result_count):
        remaining = deadline - monotonic()
        if remaining <= 0.0:
            raise TimeoutError("child results exceeded the shared collection deadline")
        result = results.get(timeout=remaining)
        if monotonic() >= deadline:
            raise TimeoutError("child results exceeded the shared collection deadline")
        collected.append(result)
    return collected


def _cleanup_owned_children(
    children: list[Any],
    *,
    join_timeout_seconds: float,
    escalation_events: list[str] | None = None,
    unresolved_custody: list[str] | None = None,
) -> tuple[str, ...]:
    failures: list[str] = []
    unresolved = unresolved_custody if unresolved_custody is not None else []
    for child in children:
        pid = child.pid
        if pid is None:
            continue

        try:
            alive = child.is_alive()
        except Exception as exc:
            failures.append(f"pid={pid}:initial_alive_check_{type(exc).__name__}")
            alive = None

        if alive is not False:
            if escalation_events is not None:
                escalation_events.append(f"pid={pid}:terminate_attempted")
            try:
                child.terminate()
            except Exception as exc:
                failures.append(f"pid={pid}:terminate_{type(exc).__name__}")
            try:
                child.join(timeout=join_timeout_seconds)
            except Exception as exc:
                failures.append(f"pid={pid}:terminate_join_{type(exc).__name__}")
            try:
                alive = child.is_alive()
            except Exception as exc:
                failures.append(f"pid={pid}:post_terminate_check_{type(exc).__name__}")
                alive = None

        if alive is True:
            if escalation_events is not None:
                escalation_events.append(f"pid={pid}:kill_attempted")
            try:
                child.kill()
            except Exception as exc:
                failures.append(f"pid={pid}:kill_{type(exc).__name__}")
            try:
                child.join(timeout=join_timeout_seconds)
            except Exception as exc:
                failures.append(f"pid={pid}:kill_join_{type(exc).__name__}")
            try:
                alive = child.is_alive()
            except Exception as exc:
                failures.append(f"pid={pid}:post_kill_check_{type(exc).__name__}")
                alive = None

        if alive is True:
            fact = f"pid={pid}:alive_after_kill_join"
            failures.append(fact)
            unresolved.append(fact)
        elif alive is None:
            fact = f"pid={pid}:cleanup_state_unknown"
            failures.append(fact)
            unresolved.append(fact)
        else:
            try:
                if child.exitcode is None:
                    fact = f"pid={pid}:exitcode_unknown_after_join"
                    failures.append(fact)
                    unresolved.append(fact)
                else:
                    child.close()
            except Exception as exc:
                fact = f"pid={pid}:close_{type(exc).__name__}"
                failures.append(fact)
                unresolved.append(fact)
    return tuple(failures)


def _cleanup_process_queues(
    queues: tuple[tuple[str, Any], ...], *, join_feeders: bool
) -> tuple[str, ...]:
    failures: list[str] = []
    may_join_feeders = join_feeders
    for name, queue in queues:
        if not may_join_feeders:
            try:
                queue.cancel_join_thread()
            except Exception as exc:
                failures.append(f"{name}:cancel_join_thread_{type(exc).__name__}")

        try:
            queue.close()
        except Exception as exc:
            failures.append(f"{name}:close_{type(exc).__name__}")
            try:
                queue.cancel_join_thread()
            except Exception as cancel_exc:
                failures.append(
                    f"{name}:cancel_after_close_error_{type(cancel_exc).__name__}"
                )
            may_join_feeders = False
            continue

        if may_join_feeders:
            try:
                queue.join_thread()
            except Exception as exc:
                failures.append(f"{name}:join_thread_{type(exc).__name__}")
                try:
                    queue.cancel_join_thread()
                except Exception as cancel_exc:
                    failures.append(
                        f"{name}:cancel_after_join_error_{type(cancel_exc).__name__}"
                    )
                may_join_feeders = False
    return tuple(failures)


class _FakeDeadlineResults:
    def __init__(self, clock: _FakeMonotonicClock, *, advance: float) -> None:
        self.clock = clock
        self.advance = advance
        self.timeouts: list[float] = []

    def get(self, *, timeout: float) -> object:
        self.timeouts.append(timeout)
        self.clock.now += self.advance
        return object()


class _FakeMonotonicClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_result_collection_uses_one_global_deadline() -> None:
    clock = _FakeMonotonicClock()
    results = _FakeDeadlineResults(clock, advance=0.6)
    with pytest.raises(TimeoutError, match="shared collection deadline"):
        _collect_results_before_deadline(
            results, 3, 1.0, monotonic=clock
        )
    assert len(results.timeouts) == 2
    assert results.timeouts[0] == 1.0
    assert results.timeouts[1] == pytest.approx(0.4)


def test_result_collection_rejects_a_last_item_returned_after_deadline() -> None:
    clock = _FakeMonotonicClock()
    results = _FakeDeadlineResults(clock, advance=1.1)
    with pytest.raises(TimeoutError, match="shared collection deadline"):
        _collect_results_before_deadline(
            results, 1, 1.0, monotonic=clock
        )
    assert len(results.timeouts) == 1
    assert results.timeouts[0] == 1.0


class _FakeCleanupProcess:
    def __init__(self, *, kill_stops: bool) -> None:
        self.pid = 12345
        self.alive = True
        self.kill_stops = kill_stops
        self.exitcode: int | None = None
        self.join_timeouts: list[float] = []
        self.close_called = False

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        if self.kill_stops:
            self.alive = False
            self.exitcode = -9

    def join(self, *, timeout: float) -> None:
        self.join_timeouts.append(timeout)

    def close(self) -> None:
        self.close_called = True


def test_child_cleanup_records_terminate_escalation_and_unresolved_alive_state() -> None:
    already_stopped = _FakeCleanupProcess(kill_stops=False)
    already_stopped.alive = False
    already_stopped.exitcode = 0
    assert _cleanup_owned_children(
        [already_stopped], join_timeout_seconds=0.1
    ) == ()
    assert already_stopped.close_called

    stopped_by_kill = _FakeCleanupProcess(kill_stops=True)
    escalations: list[str] = []
    failures = _cleanup_owned_children(
        [stopped_by_kill],
        join_timeout_seconds=0.1,
        escalation_events=escalations,
    )
    assert failures == ()
    assert escalations == [
        "pid=12345:terminate_attempted",
        "pid=12345:kill_attempted",
    ]
    assert stopped_by_kill.join_timeouts == [0.1, 0.1]
    assert stopped_by_kill.close_called

    still_alive = _FakeCleanupProcess(kill_stops=False)
    escalations = []
    unresolved: list[str] = []
    failures = _cleanup_owned_children(
        [still_alive],
        join_timeout_seconds=0.1,
        escalation_events=escalations,
        unresolved_custody=unresolved,
    )
    assert failures == ("pid=12345:alive_after_kill_join",)
    assert escalations == [
        "pid=12345:terminate_attempted",
        "pid=12345:kill_attempted",
    ]
    assert unresolved == ["pid=12345:alive_after_kill_join"]
    assert not still_alive.close_called


def test_process_queue_cleanup_is_independent_and_skips_failing_path_join() -> None:
    class FakeQueue:
        def __init__(self, *, fail_close: bool = False) -> None:
            self.fail_close = fail_close
            self.calls: list[str] = []

        def cancel_join_thread(self) -> None:
            self.calls.append("cancel_join_thread")

        def close(self) -> None:
            self.calls.append("close")
            if self.fail_close:
                raise OSError("synthetic close failure")

        def join_thread(self) -> None:
            self.calls.append("join_thread")

    failed_ready = FakeQueue(fail_close=True)
    results = FakeQueue()
    failures = _cleanup_process_queues(
        (("ready", failed_ready), ("results", results)), join_feeders=True
    )
    assert failures == ("ready:close_OSError",)
    assert failed_ready.calls == ["close", "cancel_join_thread"]
    assert results.calls == ["cancel_join_thread", "close"]

    failing_path_ready = FakeQueue()
    failing_path_results = FakeQueue()
    failures = _cleanup_process_queues(
        (("ready", failing_path_ready), ("results", failing_path_results)),
        join_feeders=False,
    )
    assert failures == ()
    assert failing_path_ready.calls == ["cancel_join_thread", "close"]
    assert failing_path_results.calls == ["cancel_join_thread", "close"]


class _HotWaveMetadataProvider:
    def __init__(
        self,
        metrics: BoundedQuotaWaveMetrics,
        *,
        strategy_slot: int,
        deadline_ns: int,
        rate_limit_first: bool = False,
    ) -> None:
        self.metrics = metrics
        self.strategy_slot = strategy_slot
        self.deadline_ns = deadline_ns
        self.rate_limit_first = rate_limit_first
        self.metadata_calls = 0

    def connect(self, **_kwargs: Any) -> None:
        return None

    def read_account(self, **_kwargs: Any) -> None:
        return None

    def close(self) -> None:
        return None

    def read_instrument_metadata(self, **_kwargs: Any) -> object:
        self.metadata_calls += 1
        dispatch_ns = time.monotonic_ns()
        self.metrics.record_dispatch(
            GatewayQuotaOperation.CTP_QUERY,
            strategy_slot=self.strategy_slot,
            dispatch_ns=dispatch_ns,
            deadline_ns=self.deadline_ns,
        )
        if self.rate_limit_first and self.metadata_calls == 1:
            raise RateLimitError("synthetic", retry_after=0.12)
        return {"metadata": "synthetic"}


def _metadata_read(
    session: GatewayQuotaProviderSession,
    *,
    deadline_seconds: float,
) -> tuple[str, int]:
    started_ns = time.monotonic_ns()
    try:
        session.read_instrument_metadata(
            instrument_id="SYNTH",
            exchange_id="synthetic",
            generation="hot-wave-generation",
            timeout=deadline_seconds,
        )
    except RateLimitError as exc:
        return f"RATE_LIMIT:{exc.retry_after}", time.monotonic_ns() - started_ns
    except GatewayQuotaAdmissionError as exc:
        return exc.code, time.monotonic_ns() - started_ns
    return "granted", time.monotonic_ns() - started_ns


def test_bounded_metrics_cover_shared_ctp_query_backoff_and_reserved_cancel(
    tmp_path: Path,
) -> None:
    """Separate account refs share one synthetic key/IP-domain quota owner."""

    domain_ref = "synthetic:dim07-shared-key-ip-domain"
    database_path = tmp_path / "dim07-shared-key-ip.sqlite3"
    owner_a = GatewayQuotaAdmission(
        database_path,
        domain_ref,
        capacity_per_window=3,
        window_seconds=10.0,
        reserved_capacity=1,
        max_queue=16,
        max_queue_per_subject=4,
        max_backoff_seconds=0.5,
        max_queue_wait_seconds=1.0,
        poll_interval_seconds=0.005,
    )
    # A distinct process-local client with the same trusted reference attaches
    # to the same SQLite domain, matching the Gateway owner contract.
    owner_b = GatewayQuotaAdmission(
        database_path,
        domain_ref,
        capacity_per_window=3,
        window_seconds=10.0,
        reserved_capacity=1,
        max_queue=16,
        max_queue_per_subject=4,
        max_backoff_seconds=0.5,
        max_queue_wait_seconds=1.0,
        poll_interval_seconds=0.005,
    )
    metrics = BoundedQuotaWaveMetrics(strategy_count=2)
    provider_a = _HotWaveMetadataProvider(
        metrics,
        strategy_slot=0,
        deadline_ns=time.monotonic_ns() + 500_000_000,
        rate_limit_first=True,
    )
    session_a = GatewayQuotaProviderSession(
        provider_a,
        owner_a,
        GatewayQuotaSubject(
            "account:synthetic-a",
            strategy_ref="strategy:hot-a",
            instrument_ref="SYNTH",
        ),
    )

    provider_a.deadline_ns = time.monotonic_ns() + 500_000_000
    outcome, elapsed_ns = _metadata_read(session_a, deadline_seconds=0.5)
    assert outcome == "RATE_LIMIT:0.12"
    assert owner_b.status()["backoff_remaining_seconds"] > 0.0
    metrics.record_admission(
        GatewayQuotaOperation.CTP_QUERY, 0, "granted", elapsed_ns
    )
    metrics.record_provider_429(0.12)

    provider_b = _HotWaveMetadataProvider(
        metrics,
        strategy_slot=1,
        deadline_ns=time.monotonic_ns() + 50_000_000,
    )
    session_b = GatewayQuotaProviderSession(
        provider_b,
        owner_b,
        GatewayQuotaSubject(
            "account:synthetic-b",
            strategy_ref="strategy:hot-b",
            instrument_ref="SYNTH",
        ),
    )
    outcome, elapsed_ns = _metadata_read(session_b, deadline_seconds=0.05)
    assert outcome == "DEADLINE_EXCEEDED"
    assert provider_b.metadata_calls == 0
    metrics.record_admission(
        GatewayQuotaOperation.CTP_QUERY,
        1,
        "DEADLINE_EXCEEDED",
        elapsed_ns,
    )

    time.sleep(0.14)
    provider_a.deadline_ns = time.monotonic_ns() + 500_000_000
    outcome, elapsed_ns = _metadata_read(session_a, deadline_seconds=0.5)
    assert outcome == "granted"
    metrics.record_admission(
        GatewayQuotaOperation.CTP_QUERY, 0, "granted", elapsed_ns
    )

    provider_b.deadline_ns = time.monotonic_ns() + 40_000_000
    outcome, elapsed_ns = _metadata_read(session_b, deadline_seconds=0.04)
    assert outcome == "DEADLINE_EXCEEDED"
    assert provider_b.metadata_calls == 0
    metrics.record_admission(
        GatewayQuotaOperation.CTP_QUERY,
        1,
        "DEADLINE_EXCEEDED",
        elapsed_ns,
    )

    cancel_started_ns = time.monotonic_ns()
    cancel_deadline_ns = cancel_started_ns + 500_000_000
    GatewayQuotaDispatchAdmission(owner_b)(
        "account:synthetic-b",
        "strategy:hot-b",
        "run:hot-b:cancel",
        "SYNTH",
        "CANCEL",
        cancel_deadline_ns,
    )
    cancel_elapsed_ns = time.monotonic_ns() - cancel_started_ns
    metrics.record_admission(
        GatewayQuotaOperation.CANCEL,
        1,
        "granted",
        cancel_elapsed_ns,
    )
    metrics.record_reserved_cancel_grant()

    snapshot = metrics.snapshot()
    assert snapshot["provider_429_count"] == 1
    assert snapshot["reserved_cancel_grants"] == 1
    assert snapshot["dispatch_after_deadline"] == 0
    assert snapshot["provider_dispatches_by_operation"]["ctp_query"] == 2
    assert snapshot["granted_by_operation"]["ctp_query"] == 2
    assert snapshot["deadline_exceeded_by_operation"]["ctp_query"] == 2
    assert snapshot["granted_by_operation"]["cancel"] == 1
    assert snapshot["strategy_grants"] == (2, 1)
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT operation, COUNT(*) FROM grants GROUP BY operation ORDER BY operation"
        ).fetchall()
    assert rows == [("cancel", 1), ("ctp_query", 2)]


def test_bounded_metrics_count_one_login_then_one_reconnect(tmp_path: Path) -> None:
    class SessionProvider:
        def __init__(self, bounded_metrics: BoundedQuotaWaveMetrics) -> None:
            self.connect_calls = 0
            self.metrics = bounded_metrics
            self.deadline_ns = 0

        def connect(self, **_kwargs: Any) -> None:
            operation = (
                GatewayQuotaOperation.LOGIN
                if self.connect_calls == 0
                else GatewayQuotaOperation.RECONNECT
            )
            self.metrics.record_dispatch(
                operation,
                strategy_slot=0,
                dispatch_ns=time.monotonic_ns(),
                deadline_ns=self.deadline_ns,
            )
            self.metrics.record_session_operation(operation)
            self.connect_calls += 1

        def read_account(self, **_kwargs: Any) -> None:
            return None

        def close(self) -> None:
            return None

    database_path = tmp_path / "dim07-login-reconnect.sqlite3"
    admission = GatewayQuotaAdmission(
        database_path,
        "synthetic:dim07-login-domain",
        capacity_per_window=3,
        window_seconds=10.0,
        reserved_capacity=0,
        max_queue=8,
        max_queue_per_subject=4,
        max_backoff_seconds=0.5,
        max_queue_wait_seconds=1.0,
        poll_interval_seconds=0.005,
    )
    metrics = BoundedQuotaWaveMetrics(strategy_count=1)
    provider = SessionProvider(metrics)
    session = GatewayQuotaProviderSession(
        provider,
        admission,
        GatewayQuotaSubject("account:synthetic-login", strategy_ref="strategy:login"),
    )
    for expected_operation in (
        GatewayQuotaOperation.LOGIN,
        GatewayQuotaOperation.RECONNECT,
    ):
        started_ns = time.monotonic_ns()
        provider.deadline_ns = started_ns + 500_000_000
        session.connect(
            account_key=AccountKey("synthetic", "loopback", "paper", "dim07-login"),
            generation="synthetic-owner-generation",
            timeout=0.5,
        )
        elapsed_ns = time.monotonic_ns() - started_ns
        metrics.record_admission(expected_operation, 0, "granted", elapsed_ns)
    assert provider.connect_calls == 2
    snapshot = metrics.snapshot()
    assert snapshot["login_attempts"] == 1
    assert snapshot["reconnect_attempts"] == 1
    assert snapshot["granted_by_operation"]["login"] == 1
    with sqlite3.connect(database_path) as connection:
        grant_rows = connection.execute(
            "SELECT operation, COUNT(*) FROM grants GROUP BY operation ORDER BY operation"
        ).fetchall()
    assert grant_rows == [("login", 1), ("reconnect", 1)]
    assert snapshot["granted_by_operation"]["reconnect"] == 1
    assert snapshot["provider_dispatches_by_operation"]["login"] == 1
    assert snapshot["provider_dispatches_by_operation"]["reconnect"] == 1
    assert snapshot["dispatch_after_deadline"] == 0


def _hot_wave_admission(database_path: str) -> GatewayQuotaAdmission:
    return GatewayQuotaAdmission(
        database_path,
        _DOMAIN_REF,
        capacity_per_window=_CAPACITY,
        window_seconds=_WINDOW_SECONDS,
        reserved_capacity=0,
        max_queue=32,
        max_queue_per_subject=1,
        max_backoff_seconds=1.0,
        max_queue_wait_seconds=2.0,
        poll_interval_seconds=0.1,
    )


def _hot_wave_process(
    database_path: str,
    process_index: int,
    release: Any,
    ready: Any,
    results: Any,
) -> None:
    admission = _hot_wave_admission(database_path)
    ready.put((process_index, "ready"))
    if not release.wait(timeout=15.0):
        results.put((process_index, "release_timeout", []))
        return

    outcomes: list[tuple[str, str, str, str | None, str | None, int]] = []
    outcomes_lock = threading.Lock()

    def admit_one(strategy_index: int) -> None:
        strategy_ref = f"strategy:hot:{strategy_index:03d}"
        run_ref = f"run:hot:{strategy_index:03d}:single"
        started_ns = time.monotonic_ns()
        deadline_ns = started_ns + int(_REQUEST_DEADLINE_SECONDS * 1_000_000_000)
        try:
            grant = admission.admit(
                GatewayQuotaSubject(
                    "account:shared-hot",
                    strategy_ref=strategy_ref,
                    instrument_ref="instrument:hot",
                    run_ref=run_ref,
                ),
                GatewayQuotaOperation.QUERY,
                deadline_ns,
            )
            outcome = (
                "granted",
                strategy_ref,
                run_ref,
                grant.subject_digest,
                grant.run_digest,
                time.monotonic_ns() - started_ns,
            )
        except GatewayQuotaAdmissionError as exc:
            outcome = (
                exc.code,
                strategy_ref,
                run_ref,
                None,
                None,
                time.monotonic_ns() - started_ns,
            )
        except BaseException as exc:
            outcome = (
                f"unexpected:{type(exc).__name__}",
                strategy_ref,
                run_ref,
                None,
                None,
                time.monotonic_ns() - started_ns,
            )
        with outcomes_lock:
            outcomes.append(outcome)

    first_strategy = process_index * _STRATEGIES_PER_PROCESS
    for batch_index in range(_BATCH_COUNT):
        first_in_batch = first_strategy + batch_index * _BATCH_SIZE
        threads = [
            threading.Thread(
                target=admit_one,
                args=(first_in_batch + offset,),
                daemon=True,
            )
            for offset in range(_BATCH_SIZE)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=_REQUEST_DEADLINE_SECONDS + 3.0)
        if any(thread.is_alive() for thread in threads):
            results.put((process_index, "thread_timeout", outcomes))
            return
    results.put((process_index, "complete", outcomes))


def _hundred_strategy_process(
    strategy_index: int,
    database_path: str,
    release: Any,
    ready: Any,
    results: Any,
) -> None:
    admission = GatewayQuotaAdmission(
        database_path,
        _DOMAIN_REF,
        capacity_per_window=_HUNDRED_PROCESS_STRATEGIES,
        window_seconds=_HUNDRED_PROCESS_WINDOW_SECONDS,
        reserved_capacity=0,
        max_queue=256,
        max_queue_per_subject=_HUNDRED_PROCESS_REQUESTS_PER_STRATEGY,
        max_backoff_seconds=1.0,
        max_queue_wait_seconds=40.0,
        poll_interval_seconds=0.025,
    )
    strategy_ref = f"strategy:hundred:{strategy_index:03d}"
    ready.put(strategy_ref)
    if not release.wait(timeout=60.0):
        results.put((strategy_index, "release_timeout", []))
        return

    outcomes: list[tuple[str, str, str, str | None, str | None, int]] = []
    outcomes_lock = threading.Lock()

    def admit_one(request_index: int) -> None:
        run_ref = f"run:hundred:{strategy_index:03d}:{request_index}"
        started_ns = time.monotonic_ns()
        deadline_ns = started_ns + int(_HUNDRED_PROCESS_DEADLINE_SECONDS * 1e9)
        try:
            grant = admission.admit(
                GatewayQuotaSubject(
                    "account:shared-hot",
                    strategy_ref=strategy_ref,
                    instrument_ref="instrument:hot",
                    run_ref=run_ref,
                ),
                GatewayQuotaOperation.CTP_QUERY,
                deadline_ns,
            )
            outcome = (
                "granted",
                strategy_ref,
                run_ref,
                grant.subject_digest,
                grant.run_digest,
                time.monotonic_ns() - started_ns,
            )
        except GatewayQuotaAdmissionError as exc:
            outcome = (
                exc.code,
                strategy_ref,
                run_ref,
                None,
                None,
                time.monotonic_ns() - started_ns,
            )
        except BaseException as exc:
            outcome = (
                f"unexpected:{type(exc).__name__}",
                strategy_ref,
                run_ref,
                None,
                None,
                time.monotonic_ns() - started_ns,
            )
        with outcomes_lock:
            outcomes.append(outcome)

    requests = [
        threading.Thread(target=admit_one, args=(index,), daemon=True)
        for index in range(_HUNDRED_PROCESS_REQUESTS_PER_STRATEGY)
    ]
    for request in requests:
        request.start()
    for request in requests:
        request.join(timeout=_HUNDRED_PROCESS_DEADLINE_SECONDS + 5.0)
    if any(request.is_alive() for request in requests):
        results.put((strategy_index, "thread_timeout", outcomes))
        return
    results.put((strategy_index, "complete", outcomes))


@pytest.mark.integration
def test_hundred_hot_strategy_requests_share_domain_budget_with_bounded_latency(
    tmp_path: Path,
    record_property: Any,
) -> None:
    """One local budget caps 100 requests from five spawned strategy workers."""

    database_path = tmp_path / "dim07-hot-wave.sqlite3"
    admission = _hot_wave_admission(str(database_path))
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    results = context.Queue()
    release = context.Event()
    children = [
        context.Process(
            target=_hot_wave_process,
            args=(str(database_path), index, release, ready, results),
        )
        for index in range(_PROCESS_COUNT)
    ]
    try:
        for index, child in enumerate(children):
            child.start()
            assert ready.get(timeout=20.0) == (index, "ready")
        release.set()

        per_process = [results.get(timeout=12.0) for _ in children]
        assert all(status == "complete" for _, status, _ in per_process), per_process
        for child in children:
            child.join(timeout=5.0)
            assert child.exitcode == 0

        outcomes = [outcome for _, _, batch in per_process for outcome in batch]
        assert len(outcomes) == _TOTAL_REQUESTS
        grants = [outcome for outcome in outcomes if outcome[0] == "granted"]
        denied = [outcome for outcome in outcomes if outcome[0] != "granted"]
        assert len(grants) == _CAPACITY
        assert len(denied) == _TOTAL_REQUESTS - _CAPACITY
        assert {outcome[0] for outcome in denied} == {"DEADLINE_EXCEEDED"}

        # The accepted grants all belong to distinct strategy/instrument subjects.
        assert len({outcome[3] for outcome in grants}) == _CAPACITY
        assert len({outcome[4] for outcome in grants}) == _CAPACITY
        latencies = sorted(outcome[5] for outcome in outcomes)
        all_outcome_p95_index = (95 * len(latencies) + 99) // 100 - 1
        all_outcome_p95_latency_ns = latencies[all_outcome_p95_index]
        denial_latencies = sorted(outcome[5] for outcome in denied)
        denial_p95_index = (95 * len(denial_latencies) + 99) // 100 - 1
        denial_p95_latency_ns = denial_latencies[denial_p95_index]
        assert all(outcome[5] < int(_REQUEST_DEADLINE_SECONDS * 1e9) for outcome in grants)
        # The existing busy-SQLite case caps total elapsed time at 750 ms for a
        # 50 ms deadline. This wave separately allows 750 ms of cleanup slack
        # after its 750 ms request deadline; grants must still be on time.
        denial_cleanup_bound_ns = int((_REQUEST_DEADLINE_SECONDS + 0.75) * 1e9)
        assert 0 <= denial_p95_latency_ns <= denial_cleanup_bound_ns
        record_property("strategy_request_count", len(outcomes))
        record_property("grant_count", len(grants))
        record_property("denial_p95_latency_ms", denial_p95_latency_ns / 1_000_000)
        record_property(
            "all_outcome_p95_latency_ms",
            all_outcome_p95_latency_ns / 1_000_000,
        )

        with sqlite3.connect(database_path) as connection:
            rows = connection.execute(
                "SELECT COUNT(*), COUNT(DISTINCT subject_digest), COUNT(DISTINCT run_digest) "
                "FROM grants WHERE operation = ?",
                (GatewayQuotaOperation.QUERY.value,),
            ).fetchone()
            remaining_active_waiters = connection.execute(
                "SELECT COUNT(*) FROM waiters WHERE deadline_ns > ?",
                (time.monotonic_ns(),),
            ).fetchone()[0]
        assert rows == (_CAPACITY, _CAPACITY, _CAPACITY)
        assert remaining_active_waiters == 0
        # Expired waiter rows are intentionally left for a later bounded prune.
        assert admission.pending_waiter_count == 0
    finally:
        release.set()
        for child in children:
            if child.pid is not None:
                if child.is_alive():
                    child.terminate()
                child.join(timeout=5.0)
                if child.exitcode is not None:
                    child.close()
        ready.close()
        results.close()
        ready.join_thread()
        results.join_thread()


@pytest.mark.integration
def test_hundred_strategy_processes_share_fair_quota_with_bounded_deadlines(
    tmp_path: Path,
    record_property: Any,
) -> None:
    """One request process per strategy receives a fair turn after a hot window."""

    available_bytes = psutil.virtual_memory().available
    if available_bytes < _LOAD01_REQUIRED_AVAILABLE_BYTES:
        pytest.skip(
            "100-process DIM07 wave requires the unchanged LOAD01 resource reserve: "
            f"available={available_bytes}, required={_LOAD01_REQUIRED_AVAILABLE_BYTES}"
        )
    record_property("available_memory_bytes_before_start", available_bytes)
    record_property("required_memory_bytes_before_start", _LOAD01_REQUIRED_AVAILABLE_BYTES)

    database_path = tmp_path / "dim07-hundred-process.sqlite3"
    admission = GatewayQuotaAdmission(
        database_path,
        _DOMAIN_REF,
        capacity_per_window=_HUNDRED_PROCESS_STRATEGIES,
        window_seconds=_HUNDRED_PROCESS_WINDOW_SECONDS,
        reserved_capacity=0,
        max_queue=256,
        max_queue_per_subject=_HUNDRED_PROCESS_REQUESTS_PER_STRATEGY,
        max_backoff_seconds=1.0,
        max_queue_wait_seconds=40.0,
        poll_interval_seconds=0.025,
    )
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    results = context.Queue()
    release = context.Event()
    children = [
        context.Process(
            target=_hundred_strategy_process,
            args=(index, str(database_path), release, ready, results),
            daemon=True,
        )
        for index in range(_HUNDRED_PROCESS_STRATEGIES)
    ]
    try:
        for child in children:
            child.start()
        ready_deadline = time.monotonic() + 90.0
        ready_strategies: set[str] = set()
        while len(ready_strategies) < _HUNDRED_PROCESS_STRATEGIES:
            remaining = ready_deadline - time.monotonic()
            assert remaining > 0.0, len(ready_strategies)
            ready_strategies.add(ready.get(timeout=remaining))
        assert ready_strategies == {
            f"strategy:hundred:{index:03d}"
            for index in range(_HUNDRED_PROCESS_STRATEGIES)
        }
        assert psutil.virtual_memory().available >= _MINIMUM_FREE_RESERVE_BYTES

        # Fill the shared window only after every test-owned process is ready.
        # The strategy wave then waits for its natural expiry; no grant rows or
        # timestamps are rewritten while workers are polling SQLite.
        seed_subject = GatewayQuotaSubject(
            "account:shared-hot",
            strategy_ref="strategy:window-seed",
            instrument_ref="instrument:hot",
        )
        for index in range(_HUNDRED_PROCESS_STRATEGIES):
            admission.admit(
                GatewayQuotaSubject(
                    "account:shared-hot",
                    strategy_ref=seed_subject.strategy_ref,
                    instrument_ref=seed_subject.instrument_ref,
                    run_ref=f"run:window-seed:{index:03d}",
                ),
                GatewayQuotaOperation.CTP_QUERY,
                time.monotonic_ns() + 5_000_000_000,
            )
        release.set()

        queue_deadline = time.monotonic() + 12.0
        waiter_count = 0
        while time.monotonic() < queue_deadline:
            try:
                with sqlite3.connect(database_path, timeout=0.25) as connection:
                    waiter_count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM waiters WHERE deadline_ns > ?",
                            (time.monotonic_ns(),),
                        ).fetchone()[0]
                    )
            except sqlite3.OperationalError:
                assert (
                    psutil.virtual_memory().available
                    >= _MINIMUM_FREE_RESERVE_BYTES
                )
                time.sleep(0.01)
                continue
            if waiter_count == (
                _HUNDRED_PROCESS_STRATEGIES
                * _HUNDRED_PROCESS_REQUESTS_PER_STRATEGY
            ):
                assert (
                    psutil.virtual_memory().available
                    >= _MINIMUM_FREE_RESERVE_BYTES
                )
                break
            assert psutil.virtual_memory().available >= _MINIMUM_FREE_RESERVE_BYTES
            # Do not let the full seed window expire before the whole queue is
            # visible; that would make the intended fairness wave ambiguous.
            assert time.monotonic() < queue_deadline, waiter_count
            time.sleep(0.01)
        assert waiter_count == (
            _HUNDRED_PROCESS_STRATEGIES
            * _HUNDRED_PROCESS_REQUESTS_PER_STRATEGY
        )

        process_results = _collect_results_before_deadline(
            results,
            len(children),
            _HUNDRED_PROCESS_RESULT_COLLECTION_SECONDS,
        )
        assert all(status == "complete" for _, status, _ in process_results), (
            process_results
        )
        for child in children:
            child.join(timeout=5.0)
            assert child.exitcode == 0

        outcomes = [
            outcome
            for _, _, process_outcomes in process_results
            for outcome in process_outcomes
        ]
        assert len(outcomes) == (
            _HUNDRED_PROCESS_STRATEGIES
            * _HUNDRED_PROCESS_REQUESTS_PER_STRATEGY
        )
        granted = [outcome for outcome in outcomes if outcome[0] == "granted"]
        denied = [outcome for outcome in outcomes if outcome[0] != "granted"]
        assert len(granted) == _HUNDRED_PROCESS_STRATEGIES
        assert len(denied) == _HUNDRED_PROCESS_STRATEGIES
        assert {outcome[0] for outcome in denied} == {"DEADLINE_EXCEEDED"}
        assert len({outcome[3] for outcome in granted}) == _HUNDRED_PROCESS_STRATEGIES
        assert len({outcome[4] for outcome in granted}) == _HUNDRED_PROCESS_STRATEGIES
        grant_counts: dict[str, int] = {}
        denial_counts: dict[str, int] = {}
        for outcome in granted:
            grant_counts[outcome[1]] = grant_counts.get(outcome[1], 0) + 1
        for outcome in denied:
            denial_counts[outcome[1]] = denial_counts.get(outcome[1], 0) + 1
        expected_strategies = {
            f"strategy:hundred:{index:03d}"
            for index in range(_HUNDRED_PROCESS_STRATEGIES)
        }
        assert grant_counts == dict.fromkeys(expected_strategies, 1)
        assert denial_counts == dict.fromkeys(expected_strategies, 1)

        grant_latencies = sorted(outcome[5] for outcome in granted)
        denial_latencies = sorted(outcome[5] for outcome in denied)
        grant_p95_ns = grant_latencies[math.ceil(0.95 * len(grant_latencies)) - 1]
        denial_p95_ns = denial_latencies[math.ceil(0.95 * len(denial_latencies)) - 1]
        assert all(
            outcome[5] < int(_HUNDRED_PROCESS_DEADLINE_SECONDS * 1e9)
            for outcome in granted
        )
        assert denial_p95_ns <= int(
            (_HUNDRED_PROCESS_DEADLINE_SECONDS + 0.75) * 1e9
        )
        bounded_metrics = BoundedQuotaWaveMetrics(
            strategy_count=_HUNDRED_PROCESS_STRATEGIES
        )
        strategy_slots = {
            f"strategy:hundred:{index:03d}": index
            for index in range(_HUNDRED_PROCESS_STRATEGIES)
        }
        for outcome in outcomes:
            bounded_metrics.record_admission(
                GatewayQuotaOperation.CTP_QUERY,
                strategy_slots[outcome[1]],
                outcome[0],
                outcome[5],
            )
        bounded_metrics_snapshot = bounded_metrics.snapshot()
        assert bounded_metrics_snapshot["strategy_grants"] == (1,) * 100
        assert bounded_metrics_snapshot["offered_by_operation"]["ctp_query"] == 200
        assert bounded_metrics_snapshot["deadline_exceeded_by_operation"]["ctp_query"] == 100
        record_property("strategy_process_count", len(children))
        record_property("strategy_request_count", len(outcomes))
        record_property("grant_count", len(granted))
        record_property("grant_p95_latency_ms", grant_p95_ns / 1_000_000)
        record_property("denial_p95_latency_ms", denial_p95_ns / 1_000_000)
        record_property(
            "bounded_admission_metrics",
            json.dumps(
                {
                    "operation": "ctp_query",
                    "offered": bounded_metrics_snapshot["offered_by_operation"]["ctp_query"],
                    "granted": bounded_metrics_snapshot["granted_by_operation"]["ctp_query"],
                    "deadline_exceeded": bounded_metrics_snapshot[
                        "deadline_exceeded_by_operation"
                    ]["ctp_query"],
                    "latency_bucket_upper_bounds_ms": bounded_metrics_snapshot[
                        "latency_bucket_upper_bounds_ms"
                    ],
                    "latency_bucket_counts": bounded_metrics_snapshot[
                        "admission_latency_bucket_counts"
                    ]["ctp_query"],
                    "grants_by_strategy_slot": bounded_metrics_snapshot[
                        "strategy_grants"
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

        with sqlite3.connect(database_path) as connection:
            grant_rows = connection.execute(
                "SELECT COUNT(*), COUNT(DISTINCT subject_digest), "
                "COUNT(DISTINCT run_digest) FROM grants "
                "WHERE operation = ?",
                (GatewayQuotaOperation.CTP_QUERY.value,),
            ).fetchone()
            remaining_waiters = connection.execute(
                "SELECT COUNT(*) FROM waiters WHERE deadline_ns > ?",
                (time.monotonic_ns(),),
            ).fetchone()[0]
        assert grant_rows == (
            _HUNDRED_PROCESS_STRATEGIES,
            _HUNDRED_PROCESS_STRATEGIES,
            _HUNDRED_PROCESS_STRATEGIES,
        )
        assert remaining_waiters == 0
        assert admission.pending_waiter_count == 0
    finally:
        active_exception = sys.exception()
        cleanup_failures: list[str] = []
        escalation_events: list[str] = []
        unresolved_custody: list[str] = []
        try:
            release.set()
        except Exception as exc:
            cleanup_failures.append(f"release_event_set_{type(exc).__name__}")
        try:
            cleanup_failures.extend(
                _cleanup_owned_children(
                    children,
                    join_timeout_seconds=_CHILD_CLEANUP_TIMEOUT_SECONDS,
                    escalation_events=escalation_events,
                    unresolved_custody=unresolved_custody,
                )
            )
        except Exception as exc:
            cleanup_failures.append(f"child_cleanup_{type(exc).__name__}")

        queue_cleanup_failures = _cleanup_process_queues(
            (("ready", ready), ("results", results)),
            join_feeders=active_exception is None and not cleanup_failures,
        )
        record_property("owned_child_cleanup_escalations", tuple(escalation_events))
        record_property("owned_child_unresolved_custody", tuple(unresolved_custody))
        record_property("owned_child_cleanup_failures", tuple(cleanup_failures))
        record_property("owned_queue_cleanup_failures", queue_cleanup_failures)

        all_cleanup_failures = (*cleanup_failures, *queue_cleanup_failures)
        if all_cleanup_failures:
            message = "owned resource cleanup failed: " + ", ".join(
                all_cleanup_failures
            )
            if active_exception is not None:
                active_exception.add_note(message)
            else:
                raise AssertionError(message)
