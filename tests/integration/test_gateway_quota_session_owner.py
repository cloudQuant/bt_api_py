"""Real local owner-fence coverage for an explicitly shared quota domain."""

from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from bt_api_base.account_protocol import AccountKey, AccountObservation, AccountResourceKey
from bt_api_gateway.account_session_worker import (
    GatewayAccountSessionWorker,
    GatewayAccountSessionWorkerError,
)
from bt_api_gateway.event_journal import GatewayEventJournal
from bt_api_gateway.quota_admission import (
    GatewayQuotaAdmissionError,
    GatewayQuotaDomainBinding,
    GatewayQuotaDomainConfig,
    GatewayQuotaOperation,
    GatewayQuotaProviderSession,
    GatewayQuotaSubject,
)
from bt_api_gateway.session_guard import LocalAccountSessionGuard

_DOMAIN_REF = "synthetic:shared-api-key-ip-owner-v1"
_COVERAGE = (AccountResourceKey("SWAP", "cross"),)


def _domain_config(database_path: Path, *, capacity: int = 32) -> GatewayQuotaDomainConfig:
    return GatewayQuotaDomainConfig(
        database_path=database_path,
        quota_domain_ref=_DOMAIN_REF,
        capacity_per_window=capacity,
        window_seconds=60.0,
        reserved_capacity=2,
        max_queue=32,
        max_queue_per_subject=8,
        max_backoff_seconds=2.0,
        max_queue_wait_seconds=10.0,
        poll_interval_seconds=0.005,
    )


def _record_provider_call(path: str, account_ref: str, operation: str) -> None:
    with sqlite3.connect(path, timeout=5.0) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS provider_calls ("
            "account_ref TEXT NOT NULL, operation TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO provider_calls(account_ref, operation) VALUES (?, ?)",
            (account_ref, operation),
        )


class _CountedSyntheticProvider:
    def __init__(self, calls_path: str, account_ref: str, account_key: AccountKey) -> None:
        self._calls_path = calls_path
        self._account_ref = account_ref
        self._account_key = account_key
        self._generation: str | None = None

    def connect(self, *, account_key: AccountKey, generation: str, timeout: float) -> None:
        if account_key != self._account_key or not generation or timeout <= 0:
            raise AssertionError("synthetic provider received invalid connect facts")
        self._generation = generation
        _record_provider_call(self._calls_path, self._account_ref, "connect")
        return None

    def read_account(self, *, generation: str, timeout: float) -> AccountObservation:
        if generation != self._generation or timeout <= 0:
            raise AssertionError("synthetic provider received a stale read generation")
        _record_provider_call(self._calls_path, self._account_ref, "read_account")
        timestamp = datetime.now(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
        return AccountObservation(
            account_key=self._account_key,
            observation_id=f"owner-observation-{self._account_ref}",
            coverage=_COVERAGE,
            balances=(),
            positions=(),
            valuation=None,
            started_at_utc=timestamp,
            completed_at_utc=timestamp,
            consistency="provider_atomic",
            balances_complete=True,
            positions_complete=True,
        )

    def close(self) -> bool:
        self._generation = None
        return True


class _ReadCounterProvider:
    def __init__(self) -> None:
        self.read_calls = 0

    def connect(self, **_kwargs: Any) -> None:
        return None

    def read_account(self, **_kwargs: Any) -> object:
        self.read_calls += 1
        return object()

    def close(self) -> bool:
        return True


def _make_worker(
    *,
    label: str,
    account_ref: str,
    quota_path: str,
    calls_path: str,
    guard_directory: str,
    work_directory: str,
) -> GatewayAccountSessionWorker:
    account_key = AccountKey("synthetic", "loopback", "paper", account_ref)
    domain = GatewayQuotaDomainBinding(_domain_config(Path(quota_path)))
    guard = LocalAccountSessionGuard(
        Path(guard_directory), account_key.account_key_id, f"synthetic-owner:{label}"
    )
    raw_provider = _CountedSyntheticProvider(calls_path, account_ref, account_key)
    provider = GatewayQuotaProviderSession(
        raw_provider,
        domain.admission,
        GatewayQuotaSubject(account_ref=f"account:{account_ref}"),
        assert_session_owned=guard.assert_owned,
        assert_domain_current=domain.assert_current,
    )
    journal = GatewayEventJournal(Path(work_directory) / f"{label}-journal.sqlite3")
    return GatewayAccountSessionWorker(
        provider,
        journal,
        account_key=account_key,
        journal_account_scope=f"journal:{account_ref}",
        state_scope=f"state:{account_ref}",
        guard=guard,
        required_coverage=_COVERAGE,
        refresh_interval_seconds=5.0,
        max_staleness_seconds=10.0,
        request_timeout_seconds=2.0,
    )


def _owner_storm_process(
    label: str,
    account_ref: str,
    quota_path: str,
    calls_path: str,
    guard_directory: str,
    work_directory: str,
    prepared: Any,
    start: Any,
    release_owner: Any,
    results: Any,
) -> None:
    worker = _make_worker(
        label=label,
        account_ref=account_ref,
        quota_path=quota_path,
        calls_path=calls_path,
        guard_directory=guard_directory,
        work_directory=work_directory,
    )
    prepared.put(label)
    if not start.wait(10.0):
        results.put((label, "denied", "START_SIGNAL_TIMEOUT"))
        return
    try:
        worker.start()
    except GatewayAccountSessionWorkerError as exc:
        results.put((label, "denied", exc.code))
        worker.close()
        return
    results.put((label, "started", worker.session_generation))
    if not release_owner.wait(15.0):
        worker.close()
        results.put((label, "closed", "OWNER_RELEASE_TIMEOUT"))
        return
    worker.close()
    results.put((label, "closed", "normal"))


def _crash_after_start_process(
    quota_path: str,
    calls_path: str,
    guard_directory: str,
    work_directory: str,
    result_pipe: Any,
    crash: Any,
) -> None:
    worker = _make_worker(
        label="crash-owner",
        account_ref="crash-account",
        quota_path=quota_path,
        calls_path=calls_path,
        guard_directory=guard_directory,
        work_directory=work_directory,
    )
    try:
        worker.start()
    except GatewayAccountSessionWorkerError as exc:
        result_pipe.send(("denied", exc.code))
        worker.close()
        return
    result_pipe.send(("started", worker.session_generation))
    if not crash.wait(10.0):
        os._exit(91)
    # This process is owned by the test. Abrupt exit deliberately models a
    # crash: it does not call provider.close or clear the active guard record.
    os._exit(0)


def _restart_attempt_process(
    quota_path: str,
    calls_path: str,
    guard_directory: str,
    work_directory: str,
    result_pipe: Any,
) -> None:
    worker = _make_worker(
        label="crash-restart-attempt",
        account_ref="crash-account",
        quota_path=quota_path,
        calls_path=calls_path,
        guard_directory=guard_directory,
        work_directory=work_directory,
    )
    try:
        worker.start()
    except GatewayAccountSessionWorkerError as exc:
        result_pipe.send(("denied", exc.code))
        worker.close()
        return
    worker.close()
    result_pipe.send(("started", None))


@pytest.mark.integration
def test_shared_domain_owner_storm_and_crash_restart_fail_closed(tmp_path: Path) -> None:
    """Competing local owners share one budget and cannot replay a crashed LOGIN."""

    context = multiprocessing.get_context("spawn")
    quota_path = tmp_path / "shared-provider-domain.sqlite3"
    calls_path = tmp_path / "synthetic-provider-calls.sqlite3"
    guard_directory = tmp_path / "account-guards"
    work_directory = tmp_path / "worker-state"
    guard_directory.mkdir()
    work_directory.mkdir()
    shared_domain = GatewayQuotaDomainBinding(_domain_config(quota_path))

    prepared = context.Queue()
    results = context.Queue()
    start = context.Event()
    release_owner = context.Event()
    children = [
        context.Process(
            target=_owner_storm_process,
            args=(
                label,
                account_ref,
                str(quota_path),
                str(calls_path),
                str(guard_directory),
                str(work_directory),
                prepared,
                start,
                release_owner,
                results,
            ),
        )
        for label, account_ref in (
            ("account-a-owner-1", "account-a"),
            ("account-a-owner-2", "account-a"),
            ("account-b-owner", "account-b"),
        )
    ]
    additional_processes: list[multiprocessing.Process] = []
    try:
        for child in children:
            child.start()
        assert {prepared.get(timeout=15.0) for _ in children} == {
            "account-a-owner-1",
            "account-a-owner-2",
            "account-b-owner",
        }
        start.set()
        startup = [results.get(timeout=15.0) for _ in children]
        started = [item for item in startup if item[1] == "started"]
        denied = [item for item in startup if item[1] == "denied"]
        assert len(started) == 2, startup
        assert {item[0] for item in started} >= {"account-b-owner"}, startup
        assert len(denied) == 1, startup
        assert denied[0][2] == "SESSION_ALREADY_OWNED", startup
        release_owner.set()
        closed = [results.get(timeout=10.0) for _ in started]
        assert sorted(closed) == sorted((item[0], "closed", "normal") for item in started)
        for child in children:
            child.join(10.0)
            assert child.exitcode == 0
        assert shared_domain.admission.status()["grants_in_window"] == 4
        with sqlite3.connect(calls_path) as connection:
            calls = connection.execute(
                "SELECT account_ref, operation, COUNT(*) "
                "FROM provider_calls GROUP BY account_ref, operation ORDER BY account_ref, operation"
            ).fetchall()
        assert calls == [
            ("account-a", "connect", 1),
            ("account-a", "read_account", 1),
            ("account-b", "connect", 1),
            ("account-b", "read_account", 1),
        ]

        # A second explicit binding to the same local domain cannot create a
        # different effective budget by changing its persisted settings.
        with pytest.raises(GatewayQuotaAdmissionError) as mismatch:
            GatewayQuotaDomainBinding(_domain_config(quota_path, capacity=31))
        assert mismatch.value.code == "DOMAIN_CONFIGURATION_MISMATCH"
        assert shared_domain.admission.assert_configuration() == shared_domain.configuration_version
        assert shared_domain.admission.status()["grants_in_window"] == 4

        parent_pipe, child_pipe = context.Pipe(duplex=False)
        crash = context.Event()
        crashed_owner = context.Process(
            target=_crash_after_start_process,
            args=(
                str(quota_path),
                str(calls_path),
                str(guard_directory),
                str(work_directory),
                child_pipe,
                crash,
            ),
        )
        additional_processes.append(crashed_owner)
        crashed_owner.start()
        child_pipe.close()
        assert parent_pipe.poll(15.0)
        assert parent_pipe.recv()[0] == "started"
        crash.set()
        crashed_owner.join(10.0)
        assert crashed_owner.exitcode == 0
        parent_pipe.close()

        grants_before_restart = shared_domain.admission.status()["grants_in_window"]
        restart_parent, restart_child = context.Pipe(duplex=False)
        restart = context.Process(
            target=_restart_attempt_process,
            args=(
                str(quota_path),
                str(calls_path),
                str(guard_directory),
                str(work_directory),
                restart_child,
            ),
        )
        additional_processes.append(restart)
        restart.start()
        restart_child.close()
        assert restart_parent.poll(15.0)
        assert restart_parent.recv() == ("denied", "SESSION_RECONCILIATION_REQUIRED")
        restart.join(10.0)
        assert restart.exitcode == 0
        restart_parent.close()

        assert shared_domain.admission.status()["grants_in_window"] == grants_before_restart
        with sqlite3.connect(calls_path) as connection:
            crash_calls = connection.execute(
                "SELECT operation, COUNT(*) FROM provider_calls "
                "WHERE account_ref = 'crash-account' GROUP BY operation ORDER BY operation"
            ).fetchall()
        assert crash_calls == [("connect", 1), ("read_account", 1)]
        persisted_guard = LocalAccountSessionGuard(
            guard_directory,
            AccountKey("synthetic", "loopback", "paper", "crash-account").account_key_id,
            "read-only-test-observer",
        )
        record = json.loads(persisted_guard.lock_path.read_text(encoding="ascii"))
        assert record["status"] == "active"
    finally:
        start.set()
        release_owner.set()
        for child in children:
            if child.pid is not None:
                if child.is_alive():
                    child.terminate()
                child.join(5.0)
            if child.pid is not None and child.exitcode is not None:
                child.close()
        for child in additional_processes:
            if child.pid is not None:
                if child.is_alive():
                    child.terminate()
                child.join(5.0)
            if child.pid is not None and child.exitcode is not None:
                child.close()
        prepared.close()
        results.close()
        prepared.join_thread()
        results.join_thread()


@pytest.mark.integration
@pytest.mark.parametrize("changed_owner_state", ("account_guard", "quota_domain"))
def test_provider_boundary_rechecks_owner_and_domain_after_admission_wait(
    tmp_path: Path, changed_owner_state: str
) -> None:
    database_path = tmp_path / f"boundary-{changed_owner_state}.sqlite3"
    domain = GatewayQuotaDomainBinding(
        GatewayQuotaDomainConfig(
            database_path=database_path,
            quota_domain_ref=f"synthetic:boundary-{changed_owner_state}",
            capacity_per_window=1,
            window_seconds=1.0,
            reserved_capacity=0,
            max_queue=8,
            max_queue_per_subject=4,
            max_backoff_seconds=1.0,
            max_queue_wait_seconds=3.0,
            poll_interval_seconds=0.005,
        )
    )
    subject = GatewayQuotaSubject("account:boundary")
    domain.admission.admit(
        subject,
        GatewayQuotaOperation.QUERY,
        time.monotonic_ns() + 1_000_000_000,
    )
    account_key = AccountKey("synthetic", "loopback", "paper", "boundary-account")
    guard_directory = tmp_path / "boundary-guard"
    guard_directory.mkdir()
    guard = LocalAccountSessionGuard(guard_directory, account_key.account_key_id, "boundary-owner")
    guard.acquire()
    original_guard_record = guard._read_record()
    assert original_guard_record is not None
    provider = _ReadCounterProvider()
    quota_provider = GatewayQuotaProviderSession(
        provider,
        domain.admission,
        subject,
        assert_session_owned=guard.assert_owned,
        assert_domain_current=domain.assert_current,
    )

    thread_errors: list[str] = []
    mutation_done = threading.Event()

    def mutate_while_queued() -> None:
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if domain.admission.pending_waiter_count == 1:
                    if changed_owner_state == "account_guard":
                        record = dict(original_guard_record)
                        record["status"] = "closed"
                        # Mutate through the exact descriptor held by this test's
                        # guard. Windows denies reopening a byte-range-locked file;
                        # provider dispatch is still blocked in its quota wait.
                        guard._write_record(record)
                    else:
                        with sqlite3.connect(database_path, timeout=1.0) as connection:
                            connection.execute(
                                "UPDATE quota_domains SET capacity = capacity + 1"
                            )
                    mutation_done.set()
                    return
                time.sleep(0.005)
            thread_errors.append("quota provider did not register its queued waiter")
        except Exception as exc:
            thread_errors.append(type(exc).__name__)

    # A local thread can change the synthetic durable owner fact while the
    # bound owner thread waits; the guard API itself remains owner-thread-only.
    thread = threading.Thread(target=mutate_while_queued, daemon=True)
    thread.start()
    try:
        with pytest.raises(GatewayQuotaAdmissionError) as denied:
            quota_provider.read_account(generation="boundary-generation", timeout=2.0)
        assert denied.value.code == (
            "SESSION_OWNERSHIP_UNAVAILABLE"
            if changed_owner_state == "account_guard"
            else "DOMAIN_CONFIGURATION_MISMATCH"
        )
        thread.join(5.0)
        assert not thread.is_alive()
        assert mutation_done.is_set()
        assert thread_errors == []
        assert provider.read_calls == 0
        assert domain.admission.pending_waiter_count == 0
    finally:
        thread.join(5.0)
        if mutation_done.is_set() and changed_owner_state == "account_guard":
            guard._write_record(original_guard_record)
        elif mutation_done.is_set():
            with sqlite3.connect(database_path, timeout=1.0) as connection:
                connection.execute("UPDATE quota_domains SET capacity = capacity - 1")
        guard.release()


class _SharedDomainMetadataProvider:
    def __init__(self) -> None:
        self.metadata_reads = 0

    def connect(self, **_kwargs: Any) -> None:
        return None

    def read_account(self, **_kwargs: Any) -> object:
        return object()

    def read_instrument_metadata(
        self,
        *,
        instrument_id: str,
        exchange_id: str,
        generation: str,
        timeout: float,
    ) -> dict[str, str]:
        if not instrument_id or not exchange_id or not generation or timeout <= 0.0:
            raise AssertionError("provider metadata query facts are incomplete")
        self.metadata_reads += 1
        return {"instrument_id": instrument_id, "exchange_id": exchange_id}

    def close(self) -> bool:
        return True


def _shared_domain_ctp_query_process(
    account_ref: str,
    database_path: str,
    ready: Any,
    release: Any,
    results: Any,
) -> None:
    domain = GatewayQuotaDomainBinding(
        GatewayQuotaDomainConfig(
            database_path=Path(database_path),
            quota_domain_ref="synthetic:shared-api-key-ip-ctp-v1",
            capacity_per_window=1,
            window_seconds=60.0,
            reserved_capacity=0,
            max_queue=4,
            max_queue_per_subject=2,
            max_backoff_seconds=1.0,
            max_queue_wait_seconds=1.0,
            poll_interval_seconds=0.005,
        )
    )
    provider = _SharedDomainMetadataProvider()
    session = GatewayQuotaProviderSession(
        provider,
        domain.admission,
        GatewayQuotaSubject(account_ref=f"account:{account_ref}"),
        assert_domain_current=domain.assert_current,
    )
    ready.put(account_ref)
    if not release.wait(timeout=10.0):
        results.put((account_ref, "RELEASE_SIGNAL_TIMEOUT", provider.metadata_reads))
        return
    try:
        metadata = session.read_instrument_metadata(
            instrument_id="cu2611",
            exchange_id="SHFE",
            generation=f"generation:{account_ref}",
            timeout=0.5,
        )
        results.put((account_ref, "GRANTED", provider.metadata_reads, metadata))
    except GatewayQuotaAdmissionError as exc:
        results.put((account_ref, exc.code, provider.metadata_reads))


@pytest.mark.integration
def test_two_account_scopes_share_one_api_key_ip_ctp_quota(tmp_path: Path) -> None:
    """Distinct account subjects consume one explicitly shared provider domain."""

    database_path = tmp_path / "shared-api-key-ip-ctp-quota.sqlite3"
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    results = context.Queue()
    release = context.Event()
    account_refs = ("account-alpha", "account-beta")
    children = [
        context.Process(
            target=_shared_domain_ctp_query_process,
            args=(account_ref, str(database_path), ready, release, results),
        )
        for account_ref in account_refs
    ]
    try:
        for child in children:
            child.start()
        assert {ready.get(timeout=15.0) for _ in children} == set(account_refs)
        release.set()
        outcomes = [results.get(timeout=5.0) for _ in children]
        assert {item[0] for item in outcomes} == set(account_refs), outcomes
        granted = [item for item in outcomes if item[1] == "GRANTED"]
        denied = [item for item in outcomes if item[1] == "DEADLINE_EXCEEDED"]
        assert len(granted) == 1, outcomes
        assert len(denied) == 1, outcomes
        assert granted[0][3] == {"instrument_id": "cu2611", "exchange_id": "SHFE"}
        assert granted[0][2] == 1
        assert denied[0][2] == 0
        for child in children:
            child.join(5.0)
            assert child.exitcode == 0

        with sqlite3.connect(database_path) as connection:
            grants = connection.execute(
                "SELECT operation, COUNT(*), COUNT(DISTINCT subject_digest) "
                "FROM grants GROUP BY operation"
            ).fetchall()
            pending_waiters = connection.execute(
                "SELECT COUNT(*) FROM waiters WHERE deadline_ns > ?",
                (time.monotonic_ns(),),
            ).fetchone()[0]
        assert grants == [("ctp_query", 1, 1)]
        assert pending_waiters == 0
    finally:
        release.set()
        for child in children:
            if child.pid is not None:
                if child.is_alive():
                    child.terminate()
                child.join(5.0)
                if child.exitcode is not None:
                    child.close()
        ready.close()
        results.close()
        ready.join_thread()
        results.join_thread()
