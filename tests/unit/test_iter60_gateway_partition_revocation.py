"""Iteration 60 acceptance report G3: Gateway account partition, revocation, replay.

INTEGRATION_SIM with real processes and sockets: one spawned Gateway account
service (session worker, journal, CURVE ROUTER) serves ten strategies, each
with its own CURVE key, principal, grant and view scope.

1. Partition: every strategy's ``GatewayAccountPositionStream`` receives only
   its own view; a forged read of another strategy's scope is denied.
2. Revocation: after the service restarts without one principal's grant, that
   strategy's stream is interrupted and the strategy ledger never reports
   ``BALANCED`` for it again; strategies still granted can receive their view.
3. Replay: a consumer restarted on the same projection path resumes from its
   durable cursor, with no gap and no duplicate.
"""

from __future__ import annotations

import multiprocessing
import time
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

zmq = pytest.importorskip("zmq")

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope  # noqa: E402
from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal  # noqa: E402
from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker  # noqa: E402
from bt_api_gateway.account_view_projection import GatewayAccountViewPolicy  # noqa: E402
from bt_api_gateway.service_component import GatewayServiceAccountScope  # noqa: E402
from bt_api_gateway.session_guard import LocalAccountSessionGuard  # noqa: E402

from bt_api_py.account_position_stream import (  # noqa: E402
    GatewayAccountPositionStream,
    gateway_position_reducers,
)
from bt_api_py.service_account_consumer import ServiceAccountConsumer  # noqa: E402
from bt_api_py.service_components import ComponentResources  # noqa: E402
from bt_api_py.service_config import parse_client_config, parse_service_config  # noqa: E402
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority  # noqa: E402
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant  # noqa: E402
from bt_api_py.service_runtime import (  # noqa: E402
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from bt_api_py.strategy_ledger import (  # noqa: E402
    InstrumentTerms,
    LedgerIntent,
    SqliteStrategyLedger,
    StrategyBudget,
)
from tests.unit import test_service_account_consumer_integration as consumer_helpers  # noqa: E402
from tests.unit import test_service_account_integration as account_helpers  # noqa: E402
from tests.unit.test_iter60_gateway_position_config import _loaded_host_timeouts  # noqa: E402
from tests.unit import test_service_account_views_integration as views  # noqa: E402

INSTRUMENT = "synthetic-perpetual"
STRATEGIES = tuple(f"strategy-g3-{index}" for index in range(10))
_POSITIONS = frozenset({"positions"})
_POSITIONS_BALANCES = frozenset({"positions", "balances"})


def _principal(strategy: str) -> str:
    return f"principal-{strategy}"


def _view(strategy: str) -> str:
    return f"view:{strategy}"


def _fact_kinds(strategy: str) -> frozenset[str]:
    # Half of the views also carry balances, so views differ in shape too.
    return _POSITIONS_BALANCES if int(strategy.rsplit("-", 1)[1]) % 2 else _POSITIONS


def _resources(provider, *, database_path, guard_directory, granted, worker_holder):
    def resources(component, _instance_id, options, _scope):
        if component != "gateway" or options.get("role") != "account_session_worker":
            raise AssertionError("only the account-view gateway is configured")
        journal = GatewayEventJournal(database_path)
        account_scope = GatewayServiceAccountScope(
            account_ref=views._ACCOUNT_REF,
            execution_domain_ref=views._DOMAIN_REF,
            journal_account_scope=views._JOURNAL_SCOPE,
            strategy_scope_map={strategy: _view(strategy) for strategy in STRATEGIES},
        )
        principals = {
            _principal(strategy): GatewayPrincipal(
                principal_id=_principal(strategy),
                account_scopes=frozenset({views._JOURNAL_SCOPE}),
                strategy_scopes=frozenset({_view(strategy)}),
                allowed_kinds=frozenset({GatewayCommandKind.READ}),
            )
            for strategy in granted
        }
        worker = GatewayAccountSessionWorker(
            provider,
            journal,
            account_key=account_helpers._ACCOUNT_KEYS[views._ACCOUNT_REF],
            journal_account_scope=views._JOURNAL_SCOPE,
            state_scope=views._STATE_SCOPE,
            guard=LocalAccountSessionGuard(
                Path(guard_directory),
                account_helpers._ACCOUNT_KEYS[views._ACCOUNT_REF].account_key_id,
                owner_ref="g3-account-view-host",
            ),
            required_coverage=views._COVERAGE,
            # Every strategy keeps a policy, granted or not: policies are the
            # account's view layout, grants decide who may read them.
            view_policies=tuple(
                GatewayAccountViewPolicy(_view(strategy), (views._LINEAR,), _fact_kinds(strategy))
                for strategy in STRATEGIES
            ),
            refresh_interval_seconds=60,
            max_staleness_seconds=3_600,
            request_timeout_seconds=2,
        )
        worker_holder["worker"] = worker
        return ComponentResources.owned(
            {
                "journal": journal,
                "accounts": {views._ACCOUNT_REF: account_scope},
                "principals": principals,
            },
            ready=lambda: True,
            close=worker.close,
            internal_ports={"service.provider_session": worker},
        )

    return resources


def _service_process(
    control,
    endpoint: str,
    raw_config: dict[str, Any],
    registry_path: str,
    journal_path: str,
    guard_directory: str,
    server_keys: tuple[bytes, bytes],
    audience: tuple[tuple[bytes, str], ...],
    first_sequence: int,
    anchor_utc: str,
) -> None:
    """Spawned Gateway host; ``audience`` = (CURVE public key, strategy) granted."""

    host = None
    provider = views._SyntheticViewProvider(first_sequence=first_sequence, anchor_utc=anchor_utc)
    try:
        config = parse_service_config(raw_config)
        service = account_helpers._account_service_info(
            views._ACCOUNT_REF, endpoint, "account-view-curve-v1"
        )
        granted = tuple(strategy for _key, strategy in audience)
        worker_holder: dict[str, GatewayAccountSessionWorker] = {}
        identities = {
            strategy: WireIdentity(WireRole.STRATEGY_CLIENT, _principal(strategy), strategy)
            for strategy in granted
        }
        grants = {
            _principal(strategy): (
                PrincipalGrant(
                    identities[strategy],
                    (
                        ResourceGrant(
                            service["instance_id"],
                            "service.account_read",
                            service["scope_ref"],
                            WireScope(
                                views._DOMAIN_REF,
                                account_ref=views._ACCOUNT_REF,
                                strategy_ref=strategy,
                            ),
                            views._OPERATIONS,
                        ),
                    ),
                ),
            )
            for strategy in granted
        }
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=WireIdentity(
                    WireRole.SERVICE, service["principal_ref"], service["service_id"]
                ),
                scope=WireScope(views._DOMAIN_REF),
                scope_ref=config.scope_ref,
                scope_version="account-view-scope-v1",
                security_ref=service["security_ref"],
                security_version="account-view-curve-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="account-view-policy-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="account-view-requirements-v1",
                curve_public_key=server_keys[0],
                curve_secret_key=server_keys[1],
                principals={key: _principal(strategy) for key, strategy in audience},
                grants=grants,
            ),
            registry_path,
            _resources(
                provider,
                database_path=journal_path,
                guard_directory=guard_directory,
                granted=granted,
                worker_holder=worker_holder,
            ),
        )
        host.start()
        worker = worker_holder["worker"]
        control.send(("ready", host.endpoint, provider.connect_calls, provider.read_calls))
        while True:
            host.serve_once(10)
            if not control.poll():
                continue
            command = control.recv()
            if command[0] == "refresh":
                snapshot = worker.refresh()
                control.send(("refresh-requested", snapshot.cursor, provider.read_calls))
            elif command[0] == "close":
                host.close()
                control.send(("closed", host.state))
                return
            else:
                raise AssertionError("unknown g3 host command")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _wait_for(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class _Fixture:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.context = multiprocessing.get_context("spawn")
        self.endpoint = account_helpers._free_endpoints(1)[0]
        self.server_keys = zmq.curve_keypair()
        self.service = account_helpers._account_service_info(
            views._ACCOUNT_REF, self.endpoint, "account-view-curve-v1"
        )
        self.keys = {strategy: zmq.curve_keypair() for strategy in STRATEGIES}
        self.raw_service_config = account_helpers._service_config(self.service)
        self.guards = tmp_path / "guards"
        self.guards.mkdir()
        self.anchor = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        self.records: list[dict[str, Any]] = []
        self.host = None

    def start_host(self, granted, first_sequence, label):
        audience = tuple((self.keys[strategy][0], strategy) for strategy in granted)
        record = account_helpers._start_process(
            self.context,
            _service_process,
            (
                self.endpoint,
                self.raw_service_config,
                str(self.tmp_path / "registry.sqlite3"),
                str(self.tmp_path / "journal.sqlite3"),
                str(self.guards),
                self.server_keys,
                audience,
                first_sequence,
                self.anchor,
            ),
            label,
        )
        self.records.append(record)
        ready = account_helpers._receive(record["pipe"], timeout=30)
        assert ready[0] == "ready", ready
        self.host = record
        return record

    def close_host(self):
        closed = account_helpers._close_normally(self.host, expected_tag="closed")
        assert closed[0] == "closed", closed

    def runtime(self, strategy):
        # Loaded-host RPC latency budget (T2 root cause; no product check relaxed).
        raw_client_config = _loaded_host_timeouts(
            account_helpers._client_config(
                strategy, {views._ACCOUNT_REF: self.service}, (views._ACCOUNT_REF,)
            )
        )
        public_key, secret_key = self.keys[strategy]
        runtime = ServiceClientRuntime(
            parse_client_config(raw_client_config),
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, _principal(strategy), strategy),
            scope_resolver=lambda _scope_ref: WireScope(
                views._DOMAIN_REF, account_ref=views._ACCOUNT_REF, strategy_ref=strategy
            ),
            peer_resolver=lambda _service_id: self.service["peer"],
            transport_factory=ZmqTransportFactory(
                {
                    self.service["security_ref"]: ClientCurveKeys(
                        self.server_keys[0], public_key, secret_key
                    )
                }
            ),
            run_id=uuid4().hex,
            requested_operations={"store.account": views._OPERATIONS},
        )
        runtime.start()
        return runtime

    def consumer(self, strategy, runtime, path=None):
        snapshot_reducer, event_reducer = gateway_position_reducers()
        return ServiceAccountConsumer(
            runtime,
            "store.account",
            account_ref=views._ACCOUNT_REF,
            path=str(path or self.tmp_path / f"{strategy}-projection.sqlite3"),
            projection_version="sdk.account.view.v1/iter60-g3-v1",
            initial_state={"view_scope": _view(strategy), "positions": {}},
            expected_account_scope=views._JOURNAL_SCOPE,
            expected_strategy_scope=_view(strategy),
            snapshot_reducer=snapshot_reducer,
            event_reducer=event_reducer,
        )

    def stream(self, strategy, path=None):
        def factory():
            runtime = self.runtime(strategy)
            return self.consumer(strategy, runtime, path), runtime

        return GatewayAccountPositionStream(
            factory, account_ref=views._ACCOUNT_REF, poll_interval_seconds=0.05
        )

    def ledger(self, strategy):
        ledger = SqliteStrategyLedger(
            self.tmp_path / f"{strategy}-ledger.sqlite3",
            account_ref=views._ACCOUNT_REF,
            instruments={INSTRUMENT: InstrumentTerms(Decimal("1"), "USD", "futures")},
            budgets={strategy: StrategyBudget(Decimal("1000000"), "USD")},
        )
        ledger.reserve(LedgerIntent("seed", strategy, INSTRUMENT, "BUY", Decimal("2"), Decimal("10")))
        ledger.apply_execution("seed", cumulative_quantity=2, average_price=10)
        return ledger

    def reap(self):
        forced: list[str] = []
        failures: list[str] = []
        for record in reversed(self.records):
            account_helpers._bounded_reap_spawned_process(record, forced, failures)
        account_helpers._assert_no_fallback_cleanup(forced, failures)


@pytest.fixture
def g3(tmp_path):
    fixture = _Fixture(tmp_path)
    try:
        yield fixture
    finally:
        fixture.reap()


def test_ten_strategies_partition_revocation_and_replay(g3):
    g3.start_host(STRATEGIES, 1, "g3-host-first")
    streams = {strategy: g3.stream(strategy) for strategy in STRATEGIES}
    ledgers = {strategy: g3.ledger(strategy) for strategy in STRATEGIES}
    try:
        # Registrations are started one after another: the single-threaded test
        # host answers each within the client's 1 s request timeout.
        for strategy, stream in streams.items():
            stream.start(wait_seconds=30)
            assert _wait_for(
                lambda s=stream: s.report().positions.get(INSTRUMENT) == Decimal("2.000")
            ), (
                strategy,
                stream.error,
                stream.positions.interruption,
            )

        # -- 1. partition ----------------------------------------------------
        for strategy, stream in streams.items():
            report = stream.report()
            assert report.positions == {INSTRUMENT: Decimal("2.000")}
            assert not report.complete and report.valid_until_ns is None
            assert ledgers[strategy].reconcile(report).reason == "REPORT_INCOMPLETE"

        forger = g3.runtime(STRATEGIES[0])
        try:
            binding = forger.bindings[0]
            profile = parse_client_config(
                account_helpers._client_config(
                    STRATEGIES[0], {views._ACCOUNT_REF: g3.service}, (views._ACCOUNT_REF,)
                )
            ).communication["profiles"][g3.service["client_transport_ref"]]
            public_key, secret_key = g3.keys[STRATEGIES[0]]
            for victim in STRATEGIES[1:]:
                transport = ZmqTransportFactory(
                    {
                        g3.service["security_ref"]: ClientCurveKeys(
                            g3.server_keys[0], public_key, secret_key
                        )
                    }
                )(profile, g3.service["peer"])
                try:
                    denied = consumer_helpers._direct_call(
                        transport,
                        WireIdentity(
                            WireRole.STRATEGY_CLIENT, _principal(STRATEGIES[0]), STRATEGIES[0]
                        ),
                        replace(binding.scope, strategy_ref=victim),
                        binding,
                        "account.read_after",
                        {"cursor": 0, "limit": 1, "track_delivery": True},
                    )
                finally:
                    transport.close()
                assert denied.payload["ok"] is False, (victim, denied.payload)
                assert denied.payload["code"] == "SCOPE_DENIED", (victim, denied.payload)
        finally:
            forger.close()

        # A provider change reaches every view, each through its own stream.
        refreshed = account_helpers._host_command(g3.host, "refresh")
        assert refreshed[0] == "refresh-requested", refreshed
        for strategy, stream in streams.items():
            assert _wait_for(
                lambda s=stream: s.report().positions.get(INSTRUMENT) == Decimal("3.000")
            ), strategy

        # -- 3. replay: durable cursor across a consumer restart --------------
        replay_path = g3.tmp_path / "replay-projection.sqlite3"
        replay_strategy = STRATEGIES[1]
        runtime = g3.runtime(replay_strategy)
        consumer = g3.consumer(replay_strategy, runtime, replay_path)
        first = consumer.poll()
        assert first.pending_ack is False and first.cursor >= 1
        consumer.close()
        runtime.close()
        refreshed = account_helpers._host_command(g3.host, "refresh")  # positions go flat
        assert refreshed[0] == "refresh-requested", refreshed
        runtime = g3.runtime(replay_strategy)
        consumer = g3.consumer(replay_strategy, runtime, replay_path)
        try:
            resumed = consumer.checkpoint()
            assert (resumed.stream_epoch, resumed.cursor) == (first.stream_epoch, first.cursor)
            assert resumed.projection == first.projection  # nothing lost or reapplied
            after = consumer.poll()
            assert after.stream_epoch == first.stream_epoch
            assert after.cursor == first.cursor + 1  # exactly the one new change, no gap
            assert after.projection["positions"] == {}
            again = consumer.poll()
            assert (again.cursor, again.projection) == (after.cursor, after.projection)
        finally:
            consumer.close()
            runtime.close()

        # -- 2. revocation: the service restarts without one principal ---------
        revoked = STRATEGIES[9]
        kept = STRATEGIES[0]
        for stream in streams.values():
            stream.close()
        g3.close_host()
        g3.start_host(STRATEGIES[:9], 4, "g3-host-revoked")

        revoked_stream = g3.stream(revoked)
        kept_stream = g3.stream(kept)
        try:
            revoked_stream.start(wait_seconds=30)
            kept_stream.start(wait_seconds=30)
            assert _wait_for(
                lambda: kept_stream.positions.interruption == "GATEWAY_FULL_COVERAGE_UNPROVEN"
            ), (
                kept_stream.error,
                kept_stream.positions.interruption,
            )
            # A granted filtered view is still not proof of full-account coverage.
            assert ledgers[kept].reconcile(kept_stream.report()).reason == "REPORT_INCOMPLETE"
            time.sleep(1.0)
            report = revoked_stream.report()
            assert report.complete is False, (revoked_stream.error, report)
            # The revoked key is unknown to the restarted host: CURVE refuses the
            # handshake (the request is never sent) or registration is refused.
            assert revoked_stream.error in {
                "REQUEST_NOT_SENT",
                "REQUIRED_REGISTRATION_FAILED",
            }, revoked_stream.error
            assert revoked_stream.positions.interruption == "GATEWAY_STREAM_FAILED"
            state = ledgers[revoked].reconcile(report)
            assert state.state == "UNVERIFIED" and state.reason == "REPORT_INCOMPLETE"
            reader = ledgers[revoked].reader(revoked, account_positions=revoked_stream.report)
            with pytest.raises(Exception) as info:
                reader.snapshot()
            assert getattr(info.value, "code", "") == "STRATEGY_BOOK_RECONCILIATION_UNVERIFIED"
        finally:
            revoked_stream.close()
            kept_stream.close()
        g3.close_host()
    finally:
        for stream in streams.values():
            stream.close()
        for ledger in ledgers.values():
            ledger.close()
