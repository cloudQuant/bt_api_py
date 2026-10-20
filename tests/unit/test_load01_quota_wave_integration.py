from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tarfile
import threading
from dataclasses import fields
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

import pytest

from tests.unit import test_load01_hundred_strategy_30min as load_runner
from tests.unit._load01_profiles import load01_profile, market_keys_for_profile

_N6_MODULES_ABSENT_AFTER_RUNNER_IMPORT = (
    "tests.unit._load01_quota_wave" not in sys.modules
    and "tests.unit._load01_n6_quota_wave" not in sys.modules
    and "bt_api_gateway.quota_admission" not in sys.modules
)


def _git_output(*args: str, cwd: Path) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise AssertionError("could not materialize the pinned LOAD01 source archive")
    return completed.stdout


def _extract_fixed_package_archive(
    *,
    repository: Path,
    revision: str,
    destination: Path,
    package_directory: str,
) -> None:
    archive_bytes = _git_output(
        "-c", "core.autocrlf=false", "archive", "--format=tar", revision, cwd=repository
    )
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
        for member in archive.getmembers():
            member_path = PurePosixPath(member.name)
            if member_path.parts[:2] != ("src", package_directory):
                continue
            if member_path.is_absolute() or ".." in member_path.parts or not member.isfile():
                continue
            target = destination.joinpath(*member_path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise AssertionError("pinned LOAD01 package archive has an unreadable file")
            target.write_bytes(source.read())


def _materialize_fixed_n6_source_root(destination: Path) -> Path:
    checkout_root = Path(load_runner.__file__).resolve().parents[2]
    fixed_parent = load_runner._FIXED_PARENT
    gateway_repository = checkout_root / "bt_api" / "bt_api_gateway"
    base_repository = checkout_root / "bt_api" / "bt_api_base"
    gateway_revision = _git_output(
        "rev-parse", f"{fixed_parent}:bt_api/bt_api_gateway", cwd=checkout_root
    ).decode("ascii").strip()
    base_revision = _git_output(
        "rev-parse", f"{fixed_parent}:bt_api/bt_api_base", cwd=checkout_root
    ).decode("ascii").strip()

    root = destination / "fixed-source"
    _extract_fixed_package_archive(
        repository=gateway_repository,
        revision=gateway_revision,
        destination=root / "bt_api" / "bt_api_gateway",
        package_directory="bt_api_gateway",
    )
    _extract_fixed_package_archive(
        repository=base_repository,
        revision=base_revision,
        destination=root / "bt_api" / "bt_api_base",
        package_directory="bt_api_base",
    )

    for relative, expected_sha256 in load_runner._N6_QUOTA_SOURCE_PINS.items():
        if relative.startswith("bt_api/bt_api_gateway/") or relative.startswith(
            "bt_api/bt_api_base/"
        ):
            source = root / relative
            payload = source.read_bytes()
        else:
            payload = None
            try:
                payload = _git_output(
                    "show", f"{fixed_parent}:{relative}", cwd=checkout_root
                )
            except AssertionError:
                pass
            if payload is None:
                source = checkout_root / relative
                payload = source.read_bytes()
                lf_payload = payload.replace(b"\r\n", b"\n")
                if hashlib.sha256(lf_payload).hexdigest() == expected_sha256:
                    payload = lf_payload

        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise AssertionError(f"test source does not match fixed LOAD01 pin: {relative}")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return root


@pytest.fixture(scope="module", autouse=True)
def _n6_fixed_source_package_root(tmp_path_factory: pytest.TempPathFactory):
    """Load the N6 adapter from the exact LF archive bytes pinned by the runner."""

    root = _materialize_fixed_n6_source_root(tmp_path_factory.mktemp("load01-n6-source"))
    source_root_env = "BT_API_LOAD01_SOURCE_ROOT"
    prior_source_root = os.environ.get(source_root_env)
    if prior_source_root is None:
        os.environ.pop(source_root_env, None)
    os.environ[source_root_env] = str(root)

    tests_package = sys.modules["tests"]
    unit_package = sys.modules["tests.unit"]
    original_tests_path = tests_package.__path__
    original_unit_path = unit_package.__path__
    original_sys_path = list(sys.path)
    saved_module_names = {
        name
        for name in sys.modules
        if name == "bt_api_gateway"
        or name.startswith("bt_api_gateway.")
        or name == "bt_api_base"
        or name.startswith("bt_api_base.")
        or name
        in {
            "tests.unit._load01_quota_wave",
            "tests.unit._load01_n6_quota_wave",
        }
    }
    saved_modules = {name: sys.modules[name] for name in saved_module_names}
    saved_unit_attributes = {
        name: getattr(unit_package, name, None)
        for name in ("_load01_quota_wave", "_load01_n6_quota_wave")
    }

    for name in saved_module_names:
        sys.modules.pop(name, None)
    tests_package.__path__ = [str(root / "tests")]
    unit_package.__path__ = [str(root / "tests" / "unit")]
    sys.path[:0] = [
        str(root / "bt_api" / "bt_api_gateway" / "src"),
        str(root / "bt_api" / "bt_api_base" / "src"),
    ]

    try:
        import importlib

        importlib.import_module("bt_api_gateway")
        yield root
    finally:
        for name in tuple(sys.modules):
            if (
                name == "bt_api_gateway"
                or name.startswith("bt_api_gateway.")
                or name == "bt_api_base"
                or name.startswith("bt_api_base.")
                or name
                in {
                    "tests.unit._load01_quota_wave",
                    "tests.unit._load01_n6_quota_wave",
                }
            ):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
        for attribute, prior in saved_unit_attributes.items():
            if prior is None:
                vars(unit_package).pop(attribute, None)
            else:
                setattr(unit_package, attribute, prior)
        tests_package.__path__ = original_tests_path
        unit_package.__path__ = original_unit_path
        sys.path[:] = original_sys_path
        if prior_source_root is None:
            os.environ.pop(source_root_env, None)
        else:
            os.environ[source_root_env] = prior_source_root


def test_n6_loader_rejects_nonfixed_search_path_before_import(tmp_path: Path) -> None:
    unit_package = sys.modules["tests.unit"]
    original_path = unit_package.__path__
    unit_package.__path__ = [str(tmp_path)]
    try:
        with pytest.raises(AssertionError, match="package search path is not fixed"):
            load_runner._load_n6_quota_wave_module()
    finally:
        unit_package.__path__ = original_path
    assert "tests.unit._load01_quota_wave" not in sys.modules
    assert "tests.unit._load01_n6_quota_wave" not in sys.modules
    assert "bt_api_gateway.quota_admission" not in sys.modules


class _WaveEvent:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.is_set_value = False

    def is_set(self) -> bool:
        return self.is_set_value

    def set(self) -> None:
        self.calls.append("event.set")
        self.is_set_value = True


def _strategy_result_rows() -> dict[int, dict[str, Any]]:
    results: dict[int, dict[str, Any]] = {}
    for strategy_index in range(10):
        grant = {
            "strategy_index": strategy_index,
            "request_index": 0,
            "outcome": "granted",
            "subject_digest": f"subject-digest-{strategy_index:02d}",
            "run_digest": f"run-digest-{strategy_index:02d}",
            "elapsed_ns": 500_000_000,
        }
        denial = {
            "strategy_index": strategy_index,
            "request_index": 1,
            "outcome": "DEADLINE_EXCEEDED",
            "subject_digest": None,
            "run_digest": None,
            "elapsed_ns": 30_250_000_000,
        }
        results[strategy_index] = {
            "quota_admission_wave": {
                "started": True,
                "complete": True,
                "outcomes": [grant, denial],
            }
        }
    return results


def test_n6_preserves_u0_load_shape_and_remains_candidate_only() -> None:
    baseline = load01_profile("U0")
    candidate = load01_profile("N6")

    assert candidate.profile_id == "N6"
    assert candidate.accounts_by_strategy == baseline.accounts_by_strategy
    assert candidate.topic_indices_by_strategy == baseline.topic_indices_by_strategy
    assert candidate.topic_messages_per_second == baseline.topic_messages_per_second
    assert candidate.order_interval_seconds_by_strategy == baseline.order_interval_seconds_by_strategy
    assert candidate.order_phase_seconds_by_strategy == baseline.order_phase_seconds_by_strategy
    assert market_keys_for_profile("N6") == market_keys_for_profile("U0")
    assert candidate.registration_status == "CANDIDATE_NOT_P0_REGISTERED"
    assert candidate.formal_acceptance_eligible("full") is False
    assert candidate.formal_acceptance_eligible("pilot") is False
    assert len(fields(type(candidate))) == len(fields(type(baseline)))


def test_n6_seeds_only_at_t60_after_all_strategies_are_ready() -> None:
    calls: list[str] = []
    admission = object()
    event = _WaveEvent(calls)

    def seed(fake_admission: object) -> tuple[int, ...]:
        assert fake_admission is admission
        calls.append("seed")
        return tuple(range(10))

    assert load_runner._n6_quota_wave_seed_if_due(
        profile_id="U0",
        now=160.0,
        load_start=100.0,
        all_strategies_ready=True,
        admission=None,
        wave_event=None,
        seed_fn=seed,
    ) is None
    assert load_runner._n6_quota_wave_seed_if_due(
        profile_id="N6",
        now=159.99,
        load_start=100.0,
        all_strategies_ready=True,
        admission=admission,
        wave_event=event,
        seed_fn=seed,
    ) is None
    assert calls == []
    assert not event.is_set()

    with pytest.raises(RuntimeError, match="not allowed before all strategies are ready"):
        load_runner._n6_quota_wave_seed_if_due(
            profile_id="N6",
            now=160.0,
            load_start=100.0,
            all_strategies_ready=False,
            admission=admission,
            wave_event=event,
            seed_fn=seed,
        )
    assert calls == []
    assert not event.is_set()

    record = load_runner._n6_quota_wave_seed_if_due(
        profile_id="N6",
        now=160.0,
        load_start=100.0,
        all_strategies_ready=True,
        admission=admission,
        wave_event=event,
        seed_fn=seed,
    )
    assert record is not None
    assert record["scheduled_after_load_start_seconds"] == 60.0
    assert record["seed_grant_count"] == 10
    assert calls == ["seed", "event.set"]
    assert event.is_set()

    with pytest.raises(AssertionError, match="event was set before parent seeding"):
        load_runner._n6_quota_wave_seed_if_due(
            profile_id="N6",
            now=160.0,
            load_start=100.0,
            all_strategies_ready=True,
            admission=admission,
            wave_event=event,
            seed_fn=seed,
        )
    assert calls == ["seed", "event.set"]


def test_n6_seed_failure_keeps_strategy_workers_untriggered() -> None:
    calls: list[str] = []
    event = _WaveEvent(calls)

    def incomplete_seed(_admission: object) -> tuple[int, ...]:
        calls.append("seed")
        return tuple(range(9))

    with pytest.raises(AssertionError, match="exactly 10 grants"):
        load_runner._n6_quota_wave_seed_if_due(
            profile_id="N6",
            now=60.0,
            load_start=0.0,
            all_strategies_ready=True,
            admission=object(),
            wave_event=event,
            seed_fn=incomplete_seed,
        )
    assert calls == ["seed"]
    assert not event.is_set()


def test_n6_worker_results_restore_typed_fairness_summary() -> None:
    results = _strategy_result_rows()
    outcomes = load_runner._n6_quota_wave_outcomes_from_strategy_results(results)
    from tests.unit import _load01_n6_quota_wave as quota_wave

    summary = quota_wave.summarize_quota_wave(outcomes)

    assert len(outcomes) == 20
    assert all(type(row) is quota_wave.QuotaWaveOutcome for row in outcomes)
    assert summary.offered == 20
    assert summary.granted == summary.deadline_exceeded == 10
    assert summary.grants_by_strategy == (1,) * 10
    assert summary.denials_by_strategy == (1,) * 10
    assert summary.grant_p95_ns == 500_000_000
    assert summary.denial_p95_ns == 30_250_000_000


def test_n6_result_adapter_rejects_partial_or_cross_strategy_results() -> None:
    missing_strategy = _strategy_result_rows()
    del missing_strategy[9]
    with pytest.raises(AssertionError, match="all 10 strategies"):
        load_runner._n6_quota_wave_outcomes_from_strategy_results(missing_strategy)

    crossed = _strategy_result_rows()
    crossed[1]["quota_admission_wave"]["outcomes"][0]["strategy_index"] = 0
    with pytest.raises(AssertionError, match="crossed strategy ownership"):
        load_runner._n6_quota_wave_outcomes_from_strategy_results(crossed)

    incomplete = _strategy_result_rows()
    incomplete[5]["quota_admission_wave"]["complete"] = False
    with pytest.raises(AssertionError, match="did not return both quota outcomes"):
        load_runner._n6_quota_wave_outcomes_from_strategy_results(incomplete)


def test_nonquota_runner_import_does_not_load_n6_quota_modules() -> None:
    assert _N6_MODULES_ABSENT_AFTER_RUNNER_IMPORT is True


def test_n6_production_quota_defaults_are_exact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from inspect import signature

    from tests.unit import _load01_n6_quota_wave as quota_wave

    assert quota_wave.QUOTA_DOMAIN_REF == "synthetic:load01-n6-hot-wave-v1"
    assert quota_wave.STRATEGY_COUNT == 10
    assert quota_wave.REQUESTS_PER_STRATEGY == 2
    assert quota_wave.CAPACITY_PER_WINDOW == 10
    assert quota_wave.WINDOW_SECONDS == 20.0
    assert quota_wave.RESERVED_CAPACITY == 0
    assert quota_wave.MAX_QUEUE == 256
    assert quota_wave.MAX_QUEUE_PER_SUBJECT == 2
    assert quota_wave.MAX_BACKOFF_SECONDS == 1.0
    assert quota_wave.MAX_QUEUE_WAIT_SECONDS == 40.0
    assert quota_wave.POLL_INTERVAL_SECONDS == 0.025
    assert quota_wave.REQUEST_DEADLINE_SECONDS == 30.0
    assert quota_wave.SEED_DEADLINE_SECONDS == 5.0
    assert quota_wave.RESULT_COLLECTION_SECONDS == 50.0
    assert quota_wave.DENIAL_P95_CLEANUP_SECONDS == 0.75
    assert signature(quota_wave.create_quota_wave_admission).parameters[
        "window_seconds"
    ].default == 20.0
    assert signature(quota_wave.seed_quota_window).parameters[
        "seed_deadline_seconds"
    ].default == 5.0
    assert signature(quota_wave.StrategyQuotaWave).parameters[
        "window_seconds"
    ].default == 20.0
    assert signature(quota_wave.StrategyQuotaWave).parameters[
        "request_deadline_seconds"
    ].default == 30.0
    assert signature(quota_wave.StrategyQuotaWave.join).parameters[
        "timeout_seconds"
    ].default == 50.0

    calls: list[tuple[object, str, dict[str, object]]] = []

    class RecordingAdmission:
        def __init__(self, path, domain_ref, **settings) -> None:
            calls.append((path, domain_ref, settings))

    monkeypatch.setattr(quota_wave, "GatewayQuotaAdmission", RecordingAdmission)
    path = tmp_path / "load01-n6-defaults.sqlite3"
    quota_wave.create_quota_wave_admission(path)
    assert calls == [
        (
            path,
            "synthetic:load01-n6-hot-wave-v1",
            {
                "capacity_per_window": 10,
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


def test_n6_ten_strategy_wave_shares_local_sqlite_quota_and_finishes_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tests.unit import _load01_n6_quota_wave as quota_wave

    database_path = tmp_path / "load01-n6-shared.sqlite3"
    window_seconds = 3.0
    request_deadline_seconds = 5.3
    admission = quota_wave.create_quota_wave_admission(
        database_path, window_seconds=window_seconds
    )
    real_admission_type = quota_wave.GatewayQuotaAdmission
    all_requests_ready = threading.Barrier(
        quota_wave.STRATEGY_COUNT * quota_wave.REQUESTS_PER_STRATEGY
    )

    class SynchronizedAdmission:
        def __init__(self, *args, **kwargs) -> None:
            self._inner = real_admission_type(*args, **kwargs)

        def admit(self, subject, operation, deadline_ns):
            all_requests_ready.wait(timeout=5.0)
            return self._inner.admit(subject, operation, deadline_ns)

        def __getattr__(self, name: str):
            return getattr(self._inner, name)

    monkeypatch.setattr(quota_wave, "GatewayQuotaAdmission", SynchronizedAdmission)
    strategies = tuple(
        quota_wave.StrategyQuotaWave(
            database_path,
            index,
            window_seconds=window_seconds,
            request_deadline_seconds=request_deadline_seconds,
        )
        for index in range(quota_wave.STRATEGY_COUNT)
    )

    assert len({strategy.strategy_ref for strategy in strategies}) == 10
    assert all(strategy._admission is not admission for strategy in strategies)
    seeds = quota_wave.seed_quota_window(admission)
    assert len(seeds) == 10
    assert len({grant.run_digest for grant in seeds}) == 10
    assert admission.status()["grants_in_window"] == 10
    assert all(
        strategy._admission.status()["grants_in_window"] == 10
        for strategy in strategies
    )

    for strategy in strategies:
        strategy.start()
    outcomes = tuple(
        row
        for strategy in strategies
        for row in strategy.join(timeout_seconds=request_deadline_seconds + 0.5)
    )

    counts = {
        outcome: sum(row.outcome == outcome for row in outcomes)
        for outcome in {row.outcome for row in outcomes}
    }
    by_strategy = {
        index: [row.outcome for row in outcomes if row.strategy_index == index]
        for index in range(quota_wave.STRATEGY_COUNT)
    }
    assert counts == {"granted": 10, "DEADLINE_EXCEEDED": 10}, by_strategy
    assert max(row.elapsed_ns for row in outcomes) < int(
        (request_deadline_seconds + 0.25) * 1_000_000_000
    )
    summary = quota_wave.finish_quota_wave(admission, outcomes)
    assert (summary.offered, summary.granted, summary.deadline_exceeded) == (20, 10, 10)
    assert summary.grants_by_strategy == (1,) * 10
    assert summary.denials_by_strategy == (1,) * 10
    assert all(strategy.complete for strategy in strategies)
    assert admission.pending_waiter_count == 0
    assert all(strategy._admission.pending_waiter_count == 0 for strategy in strategies)
