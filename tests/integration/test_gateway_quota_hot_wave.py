from __future__ import annotations

import multiprocessing
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from bt_api_gateway.quota_admission import (
    GatewayQuotaAdmission,
    GatewayQuotaAdmissionError,
    GatewayQuotaOperation,
    GatewayQuotaSubject,
)


_DOMAIN_REF = "synthetic:dim07-hot-wave-v1"
_CAPACITY = 8
_WINDOW_SECONDS = 60.0
_PROCESS_COUNT = 5
_STRATEGIES_PER_PROCESS = 20
_BATCH_SIZE = 2
_BATCH_COUNT = _STRATEGIES_PER_PROCESS // _BATCH_SIZE
_TOTAL_REQUESTS = _PROCESS_COUNT * _STRATEGIES_PER_PROCESS
_REQUEST_DEADLINE_SECONDS = 0.75


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
