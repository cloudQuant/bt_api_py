"""Real local-process and ServiceHost coverage for shared Gateway quotas."""

from __future__ import annotations

import multiprocessing
import socket
import sqlite3
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import bt_api_gateway.quota_admission as quota_admission_module
import pytest
import zmq
from bt_api_base.account_protocol import AccountKey, AccountObservation, AccountResourceKey
from bt_api_base.exceptions import RateLimitError
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.event_journal import GatewayEventJournal
from bt_api_gateway.quota_admission import (
    GatewayQuotaAdmission,
    GatewayQuotaAdmissionError,
    GatewayQuotaDispatchAdmission,
    GatewayQuotaOperation,
    GatewayQuotaProviderSession,
    GatewayQuotaSubject,
)
from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.gateway_operator import GatewayAccountSessionBinding, GatewayOperatorDeployment
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

_SERVICE_ID = "quota-operator-service"
_SERVICE_INSTANCE = "quota-operator-gateway"
_SCOPE_REF = "quota-operator-scope-v1"
_SECURITY_REF = "quota-operator-curve-v1"
_TRANSPORT_REF = "quota-operator-transport-v1"
_DOMAIN_REF = "paper:quota-operator"
_ACCOUNT_REF = "account:quota-operator"
_STRATEGY_REF = "strategy:quota-operator"
_PRINCIPAL_REF = "principal:quota-operator"
_ACCOUNT_OPERATIONS = frozenset(
    {
        "account.ack",
        "account.configuration",
        "account.head",
        "account.progress",
        "account.read_after",
        "account.server_time_probe",
        "account.snapshot",
    }
)


def _quota(
    database_path: Path,
    *,
    capacity: int,
    window_seconds: float,
    reserved_capacity: int = 0,
    max_queue_wait_seconds: float = 10.0,
) -> GatewayQuotaAdmission:
    return GatewayQuotaAdmission(
        database_path,
        "synthetic-provider:api-key-ip-session-v1",
        capacity_per_window=capacity,
        window_seconds=window_seconds,
        reserved_capacity=reserved_capacity,
        max_queue=32,
        max_queue_per_subject=8,
        max_backoff_seconds=2.0,
        max_queue_wait_seconds=max_queue_wait_seconds,
        poll_interval_seconds=0.005,
    )


def _quota_process(
    database_path: str,
    label: str,
    strategy_ref: str,
    ready: Any,
    release: Any,
    results: Any,
) -> None:
    try:
        limiter = _quota(Path(database_path), capacity=1, window_seconds=2.0)
        ready.put(label)
        if not release.wait(10.0):
            results.put((label, "START_DEADLINE"))
            return
        grant = limiter.admit(
            GatewayQuotaSubject("account:shared", strategy_ref=strategy_ref),
            GatewayQuotaOperation.QUERY,
            time.monotonic_ns() + 8_000_000_000,
        )
        results.put((label, grant.sequence))
    except GatewayQuotaAdmissionError as exc:
        results.put((label, exc.code))


def _quota_identity_process(
    database_path: str,
    label: str,
    strategy_ref: str,
    run_ref: str,
    instrument_ref: str,
    ready: Any,
    release: Any,
    results: Any,
) -> None:
    try:
        limiter = _quota(
            Path(database_path),
            capacity=1,
            window_seconds=2.0,
            max_queue_wait_seconds=20.0,
        )
        ready.put(label)
        if not release.wait(10.0):
            results.put((label, "START_DEADLINE"))
            return
        started_ns = time.monotonic_ns()
        grant = limiter.admit(
            GatewayQuotaSubject(
                "account:multi-run",
                strategy_ref=strategy_ref,
                instrument_ref=instrument_ref,
                run_ref=run_ref,
            ),
            GatewayQuotaOperation.QUERY,
            time.monotonic_ns() + 15_000_000_000,
        )
        results.put(
            (
                label,
                grant.sequence,
                grant.subject_digest,
                grant.run_digest,
                grant.granted_monotonic_ns - started_ns,
            )
        )
    except GatewayQuotaAdmissionError as exc:
        results.put((label, exc.code))


def _quota_backoff_process(
    database_path: str,
    label: str,
    ready: Any,
    release: Any,
    results: Any,
) -> None:
    class CountingProvider:
        def __init__(self) -> None:
            self.read_calls = 0

        def connect(self, **_kwargs: Any) -> None:
            return None

        def read_account(self, **_kwargs: Any) -> AccountObservation:
            self.read_calls += 1
            raise AssertionError("shared backoff should deny before provider dispatch")

        def close(self) -> None:
            return None

    try:
        limiter = _quota(Path(database_path), capacity=4, window_seconds=60.0)
        raw_provider = CountingProvider()
        provider = GatewayQuotaProviderSession(
            raw_provider,
            limiter,
            GatewayQuotaSubject("account:429-shared", strategy_ref="strategy:reader"),
        )
        ready.put(label)
        if not release.wait(10.0):
            results.put((label, "START_DEADLINE", raw_provider.read_calls))
            return
        try:
            provider.read_account(generation="spawned-reader", timeout=0.15)
        except GatewayQuotaAdmissionError as exc:
            results.put((label, exc.code, raw_provider.read_calls))
        else:
            results.put((label, "PROVIDER_RETURNED", raw_provider.read_calls))
    except BaseException as exc:
        results.put((label, "UNEXPECTED", type(exc).__name__))


def _wait_for_quota_waiters(database_path: Path, expected: int) -> None:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            with sqlite3.connect(database_path, timeout=0.1) as connection:
                count = int(connection.execute("SELECT COUNT(*) FROM waiters").fetchone()[0])
        except sqlite3.OperationalError:
            time.sleep(0.01)
            continue
        if count == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"expected {expected} quota waiters")


@pytest.mark.integration
def test_quota_admission_is_shared_and_fair_across_spawned_processes(tmp_path: Path) -> None:
    """A hot strategy cannot consume another queued strategy's next turn."""

    database_path = tmp_path / "provider-quota.sqlite3"
    limiter = _quota(database_path, capacity=1, window_seconds=2.0)
    first = limiter.admit(
        GatewayQuotaSubject("account:shared", strategy_ref="strategy-a"),
        GatewayQuotaOperation.QUERY,
        time.monotonic_ns() + 1_000_000_000,
    )
    assert first.sequence == 1

    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    release = context.Event()
    results = context.Queue()
    children = [
        context.Process(
            target=_quota_process,
            args=(str(database_path), "a-next", "strategy-a", ready, release, results),
        ),
        context.Process(
            target=_quota_process,
            args=(str(database_path), "b-next", "strategy-b", ready, release, results),
        ),
    ]
    try:
        for child in children:
            child.start()
        assert {ready.get(timeout=10.0), ready.get(timeout=10.0)} == {"a-next", "b-next"}
        release.set()

        queue_deadline = time.monotonic() + 5.0
        while limiter.pending_waiter_count != 2 and time.monotonic() < queue_deadline:
            time.sleep(0.01)
        assert limiter.pending_waiter_count == 2

        first_waiter = results.get(timeout=5.0)
        second_waiter = results.get(timeout=5.0)
        assert first_waiter == ("b-next", 2)
        assert second_waiter == ("a-next", 3)
        for child in children:
            child.join(2.0)
            assert child.exitcode == 0
        assert limiter.pending_waiter_count == 0
        assert limiter.status()["grants_in_window"] == 1
    finally:
        release.set()
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(2.0)
            if child.exitcode is not None:
                child.close()
        ready.close()
        results.close()


@pytest.mark.integration
def test_quota_reserves_critical_capacity_and_honors_deadline_and_429(tmp_path: Path) -> None:
    limiter = _quota(
        tmp_path / "reserved-quota.sqlite3",
        capacity=2,
        window_seconds=1.0,
        reserved_capacity=1,
    )
    account = GatewayQuotaSubject("account:shared", strategy_ref="strategy-a")
    grant = limiter.admit(
        account,
        GatewayQuotaOperation.QUERY,
        time.monotonic_ns() + 500_000_000,
    )
    assert grant.operation is GatewayQuotaOperation.QUERY

    with pytest.raises(GatewayQuotaAdmissionError) as delayed_query:
        limiter.admit(
            account,
            GatewayQuotaOperation.QUERY,
            time.monotonic_ns() + 30_000_000,
        )
    assert delayed_query.value.code == "DEADLINE_EXCEEDED"
    assert limiter.pending_waiter_count == 0

    cancel_grant = limiter.admit(
        GatewayQuotaSubject("account:shared", strategy_ref="strategy-b"),
        GatewayQuotaOperation.CANCEL,
        time.monotonic_ns() + 500_000_000,
    )
    assert cancel_grant.operation is GatewayQuotaOperation.CANCEL
    assert limiter.status()["grants_in_window"] == 2

    limiter.defer_after_rate_limit(0.2)
    assert limiter.status()["backoff_remaining_seconds"] > 0.0
    with pytest.raises(GatewayQuotaAdmissionError) as backed_off_cancel:
        limiter.admit(
            account,
            GatewayQuotaOperation.CANCEL,
            time.monotonic_ns() + 30_000_000,
        )
    assert backed_off_cancel.value.code == "DEADLINE_EXCEEDED"


@pytest.mark.integration
def test_expired_quota_deadline_bounds_sqlite_cleanup_wait(tmp_path: Path) -> None:
    limiter = _quota(
        tmp_path / "busy-quota.sqlite3",
        capacity=2,
        window_seconds=1.0,
    )
    blocker = sqlite3.connect(
        tmp_path / "busy-quota.sqlite3", timeout=1.0, isolation_level=None
    )
    try:
        blocker.execute("BEGIN IMMEDIATE")
        deadline = time.monotonic_ns() + 50_000_000
        started = time.monotonic()
        with pytest.raises(GatewayQuotaAdmissionError) as denied:
            limiter.admit(
                GatewayQuotaSubject("account:shared", strategy_ref="strategy-deadline"),
                GatewayQuotaOperation.QUERY,
                deadline,
            )
        elapsed = time.monotonic() - started
        assert denied.value.code == "DEADLINE_EXCEEDED"
        assert elapsed < 0.75
    finally:
        blocker.rollback()
        blocker.close()
    assert limiter.pending_waiter_count == 0


@pytest.mark.integration
@pytest.mark.parametrize("expiry_prune", (1, 2))
def test_quota_rechecks_deadline_after_prune_before_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expiry_prune: int
) -> None:
    limiter = _quota(
        tmp_path / f"prune-deadline-{expiry_prune}.sqlite3",
        capacity=1,
        window_seconds=1.0,
    )
    original_now = time.monotonic_ns()

    class FakeQuotaTime:
        now_ns = original_now

        @classmethod
        def monotonic_ns(cls) -> int:
            return cls.now_ns

        @classmethod
        def sleep(cls, seconds: float) -> None:
            cls.now_ns += int(seconds * 1_000_000_000)

    original_prune = GatewayQuotaAdmission._prune_locked
    prune_count = 0

    def expire_during_prune(
        owner: GatewayQuotaAdmission,
        connection: sqlite3.Connection,
        now_ns: int,
    ) -> None:
        nonlocal prune_count
        original_prune(owner, connection, now_ns)
        prune_count += 1
        if prune_count == expiry_prune:
            FakeQuotaTime.now_ns += 60_000_000

    monkeypatch.setattr(quota_admission_module, "time", FakeQuotaTime)
    monkeypatch.setattr(GatewayQuotaAdmission, "_prune_locked", expire_during_prune)
    deadline = FakeQuotaTime.now_ns + 50_000_000
    with pytest.raises(GatewayQuotaAdmissionError) as denied:
        limiter.admit(
            GatewayQuotaSubject("account:shared", strategy_ref="strategy-deadline"),
            GatewayQuotaOperation.QUERY,
            deadline,
        )
    assert denied.value.code == "DEADLINE_EXCEEDED"
    assert limiter.status()["grants_in_window"] == 0
    assert limiter.pending_waiter_count == 0
    with sqlite3.connect(tmp_path / f"prune-deadline-{expiry_prune}.sqlite3") as connection:
        grant_sequence = connection.execute(
            "SELECT grant_sequence FROM quota_domains"
        ).fetchone()[0]
    assert grant_sequence == 0


@pytest.mark.integration
def test_provider_rate_limit_backoff_is_shared_by_peer_admission_owner(tmp_path: Path) -> None:
    database_path = tmp_path / "rate-limited-provider.sqlite3"
    first_owner = _quota(database_path, capacity=4, window_seconds=1.0)
    peer_owner = _quota(database_path, capacity=4, window_seconds=1.0)

    class RateLimitedProvider:
        def __init__(self) -> None:
            self.read_calls = 0

        def connect(self, **_kwargs) -> None:
            return None

        def read_account(self, **_kwargs) -> AccountObservation:
            self.read_calls += 1
            raise RateLimitError("synthetic", retry_after=1e308, detail="opaque provider detail")

        def close(self) -> None:
            return None

    raw_provider = RateLimitedProvider()
    provider = GatewayQuotaProviderSession(
        raw_provider,
        first_owner,
        GatewayQuotaSubject("account:shared", strategy_ref="strategy-a"),
    )
    with pytest.raises(GatewayQuotaAdmissionError) as invalid_timeout:
        provider.read_account(generation="generation-test", timeout=1e308)
    assert invalid_timeout.value.code == "INVALID_DEADLINE"
    assert raw_provider.read_calls == 0
    assert first_owner.status()["grants_in_window"] == 0
    with pytest.raises(RateLimitError):
        provider.read_account(generation="generation-test", timeout=0.5)
    assert 0.0 < peer_owner.status()["backoff_remaining_seconds"] <= 2.0
    assert peer_owner.status()["grants_in_window"] == 1
    assert raw_provider.read_calls == 1


class _Provider:
    def __init__(self, account_key: AccountKey) -> None:
        self.account_key = account_key
        self.synthetic_credential = "SYNTHETIC-CREDENTIAL-SENTINEL"
        self.calls: list[str] = []
        self.generation: str | None = None

    def connect(self, *, account_key: AccountKey, generation: str, timeout: float) -> None:
        assert account_key == self.account_key and timeout > 0.0
        self.calls.append("connect")
        self.generation = generation

    def read_account(self, *, generation: str, timeout: float) -> AccountObservation:
        assert generation == self.generation and timeout > 0.0
        self.calls.append("read_account")
        timestamp = datetime.now(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
        return AccountObservation(
            account_key=self.account_key,
            observation_id="quota-observation-1",
            coverage=(AccountResourceKey("SWAP", "cross"),),
            balances=(),
            positions=(),
            valuation=None,
            started_at_utc=timestamp,
            completed_at_utc=timestamp,
            consistency="provider_atomic",
            balances_complete=True,
            positions_complete=True,
        )

    def read_account_configuration(self, *, generation: str, timeout: float) -> dict[str, object]:
        assert generation == self.generation and timeout > 0.0
        self.calls.append("read_account_configuration")
        return {"canTrade": True, "canDeposit": None, "canWithdraw": None, "dualSidePosition": None}

    def close(self) -> None:
        self.calls.append("close")
        self.generation = None


@pytest.mark.integration
def test_operator_host_applies_quota_to_account_login_and_reads(tmp_path: Path) -> None:
    rpc_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        rpc_probe.bind(("127.0.0.1", 0))
        rpc_bind = f"tcp://127.0.0.1:{rpc_probe.getsockname()[1]}"
    finally:
        rpc_probe.close()
    raw_config = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SCOPE_REF,
        "transport_ref": _TRANSPORT_REF,
        "registration_policy_ref": "quota-registration-v1",
        "service_requirements_ref": "quota-requirements-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _SERVICE_INSTANCE,
                    "bind_ports": ["service.account_read", "service.provider_session"],
                    "options": {
                        "role": "account_session_worker",
                        "account_session_ref": "session:quota-account-v1",
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": rpc_bind},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {"high_water_mark": 32, "max_message_bytes": 262_144},
                }
            },
        },
    }
    config = parse_service_config(raw_config)
    server_public, server_secret = zmq.curve_keypair()
    client_public, _client_secret = zmq.curve_keypair()
    scope = WireScope(_DOMAIN_REF)
    client_scope = WireScope(_DOMAIN_REF, account_ref=_ACCOUNT_REF, strategy_ref=_STRATEGY_REF)
    grant = ResourceGrant(
        _SERVICE_INSTANCE,
        "service.account_read",
        _SCOPE_REF,
        client_scope,
        _ACCOUNT_OPERATIONS,
    )
    authority = TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, "principal:quota-service", _SERVICE_ID),
        scope=scope,
        scope_ref=config.scope_ref,
        scope_version="quota-scope-v1",
        security_ref=_SECURITY_REF,
        security_version="quota-curve-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="quota-registration-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="quota-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: _PRINCIPAL_REF},
        grants={
            _PRINCIPAL_REF: (
                PrincipalGrant(
                    WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_REF, _STRATEGY_REF),
                    (grant,),
                ),
            )
        },
    )
    account_key = AccountKey("synthetic", "loopback", "paper", "quota-owner-account")
    provider = _Provider(account_key)
    journal = GatewayEventJournal(tmp_path / "operator-quota-state.sqlite3")
    account_scope = GatewayServiceAccountScope(
        _ACCOUNT_REF,
        _DOMAIN_REF,
        "journal:quota-account",
        strategy_scope_map={_STRATEGY_REF: "state:quota-strategy"},
        account_read_strategy_scope="state:quota-account",
    )
    principal = GatewayPrincipal(
        _PRINCIPAL_REF,
        frozenset({"journal:quota-account"}),
        frozenset({"state:quota-strategy", "state:quota-account"}),
        frozenset({GatewayCommandKind.READ}),
    )
    guard_directory = tmp_path / "quota-session-guard"
    guard_directory.mkdir()
    limiter = _quota(
        tmp_path / "operator-provider-quota.sqlite3",
        capacity=8,
        window_seconds=60.0,
        reserved_capacity=2,
    )
    binding = GatewayAccountSessionBinding(
        account_session_ref="session:quota-account-v1",
        journal=journal,
        account_key=account_key,
        account_scope=account_scope,
        state_scope="state:quota-account",
        guard=LocalAccountSessionGuard(
            guard_directory, account_key.account_key_id, "quota-operator-test"
        ),
        required_coverage=(AccountResourceKey("SWAP", "cross"),),
        principals={_PRINCIPAL_REF: principal},
        quota_admission=limiter,
    )

    class Owner:
        def create_provider(self, account_session_ref: str) -> _Provider:
            assert account_session_ref == "session:quota-account-v1"
            return provider

    deployment = GatewayOperatorDeployment(
        config,
        authority,
        tmp_path / "operator-quota-registry.sqlite3",
        resource_provider=lambda *_args: pytest.fail("Gateway account role uses operator factory"),
        session_owner=Owner(),
        account_session=binding,
    )
    try:
        deployment.start()
        assert deployment.account_session_status["ready"] is True
        assert provider.calls == ["connect", "read_account"]
        facts = deployment.account_session_worker.read_account_configuration()
        assert facts.can_trade is True
        assert provider.calls == ["connect", "read_account", "read_account_configuration"]
        assert limiter.status()["grants_in_window"] == 3
        with sqlite3.connect(tmp_path / "operator-provider-quota.sqlite3") as connection:
            operations = [row[0] for row in connection.execute("SELECT operation FROM grants")]
        assert operations == ["login", "query", "query"]
        assert provider.synthetic_credential.encode("ascii") not in (
            tmp_path / "operator-provider-quota.sqlite3"
        ).read_bytes()
    finally:
        if deployment.state not in {"closed", "failed"}:
            with suppress(BaseException):
                deployment.close()
    assert provider.calls == ["connect", "read_account", "read_account_configuration", "close"]


@pytest.mark.integration
def test_account_binding_exposes_one_explicit_dispatch_callback(tmp_path: Path) -> None:
    account_key = AccountKey("synthetic", "loopback", "paper", "quota-dispatch-account")
    journal = GatewayEventJournal(tmp_path / "dispatch-binding-journal.sqlite3")
    account_scope = GatewayServiceAccountScope(
        _ACCOUNT_REF,
        _DOMAIN_REF,
        "journal:dispatch-account",
        strategy_scope_map={_STRATEGY_REF: "state:dispatch-strategy"},
        account_read_strategy_scope="state:dispatch-account",
    )
    principal = GatewayPrincipal(
        _PRINCIPAL_REF,
        frozenset({"journal:dispatch-account"}),
        frozenset({"state:dispatch-account", "state:dispatch-strategy"}),
        frozenset({GatewayCommandKind.READ}),
    )
    guard_directory = tmp_path / "dispatch-binding-guard"
    guard_directory.mkdir()
    limiter = _quota(
        tmp_path / "dispatch-binding-quota.sqlite3",
        capacity=4,
        window_seconds=60.0,
        reserved_capacity=1,
    )
    binding = GatewayAccountSessionBinding(
        account_session_ref="session:dispatch-binding-v1",
        journal=journal,
        account_key=account_key,
        account_scope=account_scope,
        state_scope="state:dispatch-account",
        guard=LocalAccountSessionGuard(
            guard_directory, account_key.account_key_id, "quota-dispatch-binding-test"
        ),
        required_coverage=(AccountResourceKey("SWAP", "cross"),),
        principals={_PRINCIPAL_REF: principal},
        quota_admission=limiter,
    )

    callback = binding.dispatch_quota_admission
    assert type(callback) is GatewayQuotaDispatchAdmission
    assert callback is binding.dispatch_quota_admission
    assert callback._admission is limiter
    assert (
        callback(
            _ACCOUNT_REF,
            _STRATEGY_REF,
            "run:dispatch-binding",
            "BTC-PERP",
            "CLOSE",
            time.monotonic_ns() + 5_000_000_000,
        )
        is None
    )
    assert limiter.status()["grants_in_window"] == 1


@pytest.mark.integration
def test_quota_fairness_aggregates_runs_under_strategy_and_instrument(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "multi-run-fairness.sqlite3"
    limiter = _quota(
        database_path,
        capacity=1,
        window_seconds=2.0,
        max_queue_wait_seconds=20.0,
    )
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    results = context.Queue()
    workers = [
        ("hot-run-a", "strategy:hot", "run:a"),
        ("hot-run-b", "strategy:hot", "run:b"),
        ("cold-strategy", "strategy:cold", "run:cold"),
    ]
    children: list[Any] = []
    releases: list[Any] = []
    try:
        for label, strategy_ref, run_ref in workers:
            release = context.Event()
            releases.append(release)
            child = context.Process(
                target=_quota_identity_process,
                args=(
                    str(database_path),
                    label,
                    strategy_ref,
                    run_ref,
                    "BTC-PERP",
                    ready,
                    release,
                    results,
                ),
            )
            child.start()
            children.append(child)
            assert ready.get(timeout=10.0) == label

        seeded = limiter.admit(
            GatewayQuotaSubject(
                "account:multi-run",
                strategy_ref="strategy:hot",
                instrument_ref="BTC-PERP",
                run_ref="run:seed",
            ),
            GatewayQuotaOperation.QUERY,
            time.monotonic_ns() + 1_000_000_000,
        )
        for release in releases:
            release.set()
        _wait_for_quota_waiters(database_path, len(workers))

        first = results.get(timeout=5.0)
        assert first[0] == "cold-strategy", first
        outcomes = [first]
        for _ in range(2):
            outcomes.append(results.get(timeout=5.0))

        assert [item[1] for item in outcomes] == [2, 3, 4]
        hot = [item for item in outcomes if item[0].startswith("hot-")]
        assert {item[2] for item in hot} == {seeded.subject_digest}
        assert len({item[3] for item in hot}) == 2
        assert all(0 <= item[4] < 15_000_000_000 for item in outcomes)
        for child in children:
            child.join(2.0)
            assert child.exitcode == 0
        assert limiter.pending_waiter_count == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(2.0)
            if child.exitcode is not None:
                child.close()
        ready.close()
        results.close()


@pytest.mark.integration
def test_provider_session_keeps_login_until_provider_dispatch_starts(
    tmp_path: Path,
) -> None:
    limiter = _quota(
        tmp_path / "login-dispatch-quota.sqlite3",
        capacity=3,
        window_seconds=0.15,
    )
    subject = GatewayQuotaSubject("account:login", strategy_ref="strategy:login")
    for _ in range(3):
        limiter.admit(
            subject,
            GatewayQuotaOperation.QUERY,
            time.monotonic_ns() + 1_000_000_000,
        )

    account_key = AccountKey("synthetic", "loopback", "paper", "login-transition")
    raw_provider = _Provider(account_key)
    session = GatewayQuotaProviderSession(raw_provider, limiter, subject)
    with pytest.raises(GatewayQuotaAdmissionError) as deferred_login:
        session.connect(account_key=account_key, generation="generation-1", timeout=0.03)
    assert deferred_login.value.code == "DEADLINE_EXCEEDED"
    assert raw_provider.calls == []

    time.sleep(0.18)
    session.connect(account_key=account_key, generation="generation-1", timeout=0.5)
    session.connect(account_key=account_key, generation="generation-1", timeout=0.5)
    assert raw_provider.calls == ["connect", "connect"]
    with sqlite3.connect(tmp_path / "login-dispatch-quota.sqlite3") as connection:
        operations = [row[0] for row in connection.execute("SELECT operation FROM grants")]
    assert operations == ["login", "reconnect"]


@pytest.mark.integration
def test_provider_429_backoff_blocks_spawned_process_dispatch(tmp_path: Path) -> None:
    database_path = tmp_path / "spawned-provider-backoff.sqlite3"
    owner = _quota(database_path, capacity=4, window_seconds=60.0)
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    release = context.Event()
    results = context.Queue()
    children: list[Any] = []

    class RateLimitedProvider:
        def __init__(self) -> None:
            self.read_calls = 0

        def connect(self, **_kwargs: Any) -> None:
            return None

        def read_account(self, **_kwargs: Any) -> AccountObservation:
            self.read_calls += 1
            raise RateLimitError("synthetic", retry_after=0.8, detail="opaque provider detail")

        def close(self) -> None:
            return None

    provider_raw = RateLimitedProvider()
    provider = GatewayQuotaProviderSession(
        provider_raw,
        owner,
        GatewayQuotaSubject("account:429-shared", strategy_ref="strategy:writer"),
    )
    try:
        for index in range(4):
            label = f"backoff-storm-{index}"
            child = context.Process(
                target=_quota_backoff_process,
                args=(str(database_path), label, ready, release, results),
            )
            child.start()
            children.append(child)
            assert ready.get(timeout=10.0) == label
        with pytest.raises(RateLimitError):
            provider.read_account(generation="429-source", timeout=0.5)
        assert provider_raw.read_calls == 1
        assert owner.status()["backoff_remaining_seconds"] > 0.5
        release.set()
        results_seen = [results.get(timeout=5.0) for _ in children]
        assert sorted(results_seen) == [
            (f"backoff-storm-{index}", "DEADLINE_EXCEEDED", 0)
            for index in range(4)
        ]
        for child in children:
            child.join(2.0)
            assert child.exitcode == 0
        assert owner.pending_waiter_count == 0
        assert owner.status()["grants_in_window"] == 1
    finally:
        release.set()
        for child in children:
            if child.is_alive():
                child.terminate()
            child.join(2.0)
            if child.exitcode is not None:
                child.close()
        ready.close()
        results.close()
