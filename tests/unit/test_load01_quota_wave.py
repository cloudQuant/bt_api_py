from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from bt_api_gateway.quota_admission import (
    GatewayQuotaAdmission,
    GatewayQuotaAdmissionError,
    GatewayQuotaGrant,
    GatewayQuotaOperation,
    GatewayQuotaSubject,
)
from tests.unit import _load01_quota_wave as quota_wave


def _grant(subject: GatewayQuotaSubject, sequence: int) -> GatewayQuotaGrant:
    identity = [subject.account_ref, subject.strategy_ref, subject.instrument_ref]
    subject_digest = hashlib.sha256(repr(identity).encode("ascii")).hexdigest()
    run_digest = (
        hashlib.sha256(subject.run_ref.encode("ascii")).hexdigest()
        if subject.run_ref is not None
        else None
    )
    return GatewayQuotaGrant(
        grant_id=f"grant-{sequence}",
        operation=GatewayQuotaOperation.CTP_QUERY,
        sequence=sequence,
        granted_monotonic_ns=time.monotonic_ns(),
        subject_digest=subject_digest,
        run_digest=run_digest,
    )


def test_parent_policy_and_seed_match_the_accepted_dim07_wave(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    constructor_calls: list[tuple[object, dict[str, object]]] = []
    seed_calls: list[tuple[GatewayQuotaSubject, GatewayQuotaOperation, int, int]] = []

    class FakeAdmission:
        def __init__(self, database_path, domain_ref, **settings) -> None:
            constructor_calls.append((database_path, {"domain_ref": domain_ref, **settings}))

        def admit(self, subject, operation, deadline_ns):
            seed_calls.append((subject, operation, deadline_ns, time.monotonic_ns()))
            return _grant(subject, len(seed_calls))

    monkeypatch.setattr(quota_wave, "GatewayQuotaAdmission", FakeAdmission)
    path = tmp_path / "shared-dim07.sqlite3"
    owner = quota_wave.create_quota_wave_admission(path)

    assert constructor_calls == [
        (
            path,
            {
                "domain_ref": "synthetic:dim07-hot-wave-v1",
                "capacity_per_window": 100,
                "window_seconds": 20.0,
                "reserved_capacity": 0,
                "max_queue": 256,
                "max_queue_per_subject": 2,
                "max_backoff_seconds": 1.0,
                "max_queue_wait_seconds": 40.0,
                "poll_interval_seconds": 0.025,
            },
        )
    ]
    seeds = quota_wave.seed_quota_window(owner)
    assert len(seeds) == len(seed_calls) == 100
    assert {operation for _, operation, _, _ in seed_calls} == {
        GatewayQuotaOperation.CTP_QUERY
    }
    assert {subject.account_ref for subject, _, _, _ in seed_calls} == {
        "account:shared-hot"
    }
    assert {subject.strategy_ref for subject, _, _, _ in seed_calls} == {
        "strategy:window-seed"
    }
    assert {subject.instrument_ref for subject, _, _, _ in seed_calls} == {
        "instrument:hot"
    }
    assert {subject.run_ref for subject, _, _, _ in seed_calls} == {
        f"run:window-seed:{index:03d}" for index in range(100)
    }
    assert all(
        0 < deadline - observed_ns <= 5_000_000_000
        for _, _, deadline, observed_ns in seed_calls
    )


def test_strategy_wave_starts_two_background_admissions_and_collects_them(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entered = threading.Condition()
    release = threading.Event()
    calls: list[tuple[GatewayQuotaSubject, GatewayQuotaOperation, int]] = []

    class FakeAdmission:
        def admit(self, subject, operation, deadline_ns):
            with entered:
                calls.append((subject, operation, deadline_ns))
                entered.notify_all()
            if not release.wait(timeout=2.0):
                raise AssertionError("test did not release its fake admission")
            if subject.run_ref.endswith(":0"):
                return _grant(subject, 1)
            raise GatewayQuotaAdmissionError("DEADLINE_EXCEEDED")

    monkeypatch.setattr(quota_wave, "create_quota_wave_admission", lambda _path: FakeAdmission())
    wave = quota_wave.StrategyQuotaWave(tmp_path / "worker.sqlite3", 7)
    wave.start()

    with entered:
        assert entered.wait_for(lambda: len(calls) == 2, timeout=1.0)
    assert wave.poll() == ()
    assert not wave.complete
    assert len(calls) == 2
    assert {operation for _, operation, _ in calls} == {
        GatewayQuotaOperation.CTP_QUERY
    }
    assert {subject.account_ref for subject, _, _ in calls} == {"account:shared-hot"}
    assert {subject.strategy_ref for subject, _, _ in calls} == {
        "strategy:hundred:007"
    }
    assert {subject.instrument_ref for subject, _, _ in calls} == {"instrument:hot"}
    assert {subject.run_ref for subject, _, _ in calls} == {
        "run:hundred:007:0",
        "run:hundred:007:1",
    }
    assert all(
        0 < deadline - time.monotonic_ns() <= 30_000_000_000
        for _, _, deadline in calls
    )

    release.set()
    outcomes = wave.join(timeout_seconds=2.0)
    assert wave.complete
    assert [row.outcome for row in outcomes] == ["granted", "DEADLINE_EXCEEDED"]
    assert len({row.run_digest for row in outcomes if row.run_digest is not None}) == 1


def test_partial_thread_start_still_reaps_every_started_admission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeAdmission:
        def admit(self, subject, _operation, _deadline_ns):
            return _grant(subject, 1)

    monkeypatch.setattr(quota_wave, "create_quota_wave_admission", lambda _path: FakeAdmission())
    original_start = threading.Thread.start

    def fail_second_start(thread: threading.Thread) -> None:
        if thread.name.endswith("-1"):
            raise RuntimeError("synthetic thread-start failure")
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_second_start)
    wave = quota_wave.StrategyQuotaWave(tmp_path / "partial-start.sqlite3", 11)

    with pytest.raises(RuntimeError, match="synthetic thread-start failure"):
        wave.start()

    outcomes = wave.join(timeout_seconds=2.0)
    assert len(outcomes) == 1
    assert outcomes[0].request_index == 0
    assert outcomes[0].outcome == "granted"
    assert not wave.complete
    with pytest.raises(AssertionError, match="exactly 200 typed outcomes"):
        quota_wave.summarize_quota_wave(outcomes)


def test_two_real_sqlite_admissions_close_waiters_without_provider_calls(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "dim07-focused.sqlite3"
    owner = quota_wave.create_quota_wave_admission(database_path)
    wave = quota_wave.StrategyQuotaWave(database_path, 3)

    wave.start()
    outcomes = wave.join(timeout_seconds=5.0)

    assert len(outcomes) == 2
    assert {row.outcome for row in outcomes} == {"granted"}
    assert len({row.subject_digest for row in outcomes}) == 1
    assert len({row.run_digest for row in outcomes}) == 2
    assert owner.pending_waiter_count == 0


def test_completed_wave_summary_enforces_per_strategy_fairness_and_bounds() -> None:
    outcomes: list[quota_wave.QuotaWaveOutcome] = []
    for strategy_index in range(100):
        outcomes.append(
            quota_wave.QuotaWaveOutcome(
                strategy_index,
                0,
                "granted",
                f"subject-{strategy_index}",
                f"run-{strategy_index}",
                23_000_000_000,
            )
        )
        outcomes.append(
            quota_wave.QuotaWaveOutcome(
                strategy_index,
                1,
                "DEADLINE_EXCEEDED",
                None,
                None,
                30_062_000_000,
            )
        )

    summary = quota_wave.summarize_quota_wave(outcomes)

    assert (summary.offered, summary.granted, summary.deadline_exceeded) == (200, 100, 100)
    assert summary.grants_by_strategy == (1,) * 100
    assert summary.denials_by_strategy == (1,) * 100
    assert summary.grant_p95_ns == 23_000_000_000
    assert summary.denial_p95_ns == 30_062_000_000
    with pytest.raises(AssertionError, match="exactly 200 typed outcomes"):
        quota_wave.summarize_quota_wave(outcomes[:-1])
    wrong_counts = list(outcomes)
    wrong_counts[0] = replace(
        wrong_counts[0],
        outcome="DEADLINE_EXCEEDED",
        subject_digest=None,
        run_digest=None,
    )
    with pytest.raises(AssertionError, match="100 grants and 100 deadline denials"):
        quota_wave.summarize_quota_wave(wrong_counts)
