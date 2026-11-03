"""Iteration 60 (acceptance G2): full-account reconciliation evidence over ZMQ.

Spawned host: ServiceHost + Gateway account session worker whose provider
returns a full-position receipt, a position-stream bootstrap continuity port
(explicit FAKE verifier: no real provider source is authenticated here) and a
``GatewayReconciliationInspector`` exposed as ``account.reconciliation_cut``.
Client (this process): ``GatewayReconciliationPositionSource`` over CURVE ZMQ
-> ``StreamedAccountPositions`` -> ``SqliteStrategyLedger``.

Proves: a full-account inspection yields a complete, lease-bounded report and
the ledger reconciles BALANCED; a provider change is a residual; revoking the
principal's server-owned grant on the RUNNING host makes the next poll fail
and the report incomplete (UNVERIFIED), never BALANCED. INTEGRATION_SIM only.
"""

from __future__ import annotations

import multiprocessing
import time
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

zmq = pytest.importorskip("zmq")
pytest.importorskip("bt_api_gateway.reconciliation_authority")

from bt_api_base.account_protocol import (  # noqa: E402
    AccountBalance,
    AccountObservation,
    AccountPosition,
    AccountResourceKey,
)
from bt_api_base.market_protocol import MarketInstrumentKey  # noqa: E402
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope  # noqa: E402

from bt_api_py.account_position_stream import GatewayReconciliationPositionSource  # noqa: E402
from bt_api_py.service_config import parse_client_config  # noqa: E402
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
from tests.unit import test_service_account_integration as account_helpers  # noqa: E402
from tests.unit.test_iter60_gateway_position_config import _loaded_host_timeouts  # noqa: E402

ACCOUNT = "account-a"
STRATEGY = "strategy-reconciler"
PRINCIPAL = "principal-reconciler"
DOMAIN = account_helpers._DOMAIN_REF
RESOURCE = AccountResourceKey("spot", "cash")
OPERATIONS = frozenset({"account.reconciliation_cut"})
INSTRUMENT_ID = "synthetic-spot"


def _instrument(account_key) -> MarketInstrumentKey:
    return MarketInstrumentKey(
        provider=account_key.provider,
        venue=account_key.venue,
        environment=account_key.environment,
        product_type="spot",
        instrument_id=INSTRUMENT_ID,
        native_symbol="SYN-SPOT",
    )


def _utc(ns: int) -> str:
    from datetime import datetime, timezone

    return (
        datetime.fromtimestamp(ns / 1e9, timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


class _FullProvider:
    """FAKE provider: returns a full-account position query result."""

    def __init__(self, account_key):
        self.account_key = account_key
        self.quantity = "2"
        self.current_source = None
        self.generation = None

    def connect(self, *, account_key, generation, timeout):
        self.generation = generation

    def read_account(self, *, generation, timeout):
        from bt_api_gateway.account_position_snapshot import (
            GatewayFullPositionSnapshot,
            position_rows_sha256,
            resource_coverage_sha256,
        )

        now = time.time_ns()
        stamp = _utc(now)
        position = AccountPosition(
            resource=RESOURCE,
            instrument=_instrument(self.account_key),
            side="net",
            quantity=self.quantity,
            qty_unit="unit",
            average_price="10",
            price_currency="USD",
            observed_at_utc=stamp,
        )
        observation = AccountObservation(
            account_key=self.account_key,
            observation_id=f"obs-{uuid4().hex}",
            coverage=(RESOURCE,),
            balances=(
                AccountBalance(
                    resource=RESOURCE,
                    currency="USD",
                    total="100",
                    available="100",
                    locked="0",
                    observed_at_utc=stamp,
                ),
            ),
            positions=(position,),
            valuation=None,
            started_at_utc=stamp,
            completed_at_utc=stamp,
            consistency="multi_query",
            balances_complete=True,
            positions_complete=True,
        )
        self.current_source = GatewayFullPositionSnapshot(
            observation=observation,
            account_key_id=self.account_key.account_key_id,
            session_generation=generation,
            query_scope="ALL_ACCOUNT_POSITIONS",
            filters=(),
            permission_scope="ACCOUNT_FULL",
            permission_revision="fake-permission-1",
            pagination_exhausted=True,
            page_count=1,
            account_revision=1,
            source_schema_version="fake-schema-1",
            snapshot_version=f"fake-cut-{uuid4().hex[:8]}",
            positions_sha256=position_rows_sha256(observation),
            resource_coverage_sha256=resource_coverage_sha256(observation),
            valid_until_utc=_utc(now + 120_000_000_000),
        )
        return self.current_source

    def close(self):
        return True


class _FakeVerifier:
    """Explicit FAKE: authenticates no real source."""

    def verify_binding(self, binding):
        return True

    def verify_snapshot(self, binding, snapshot):
        return True

    def verify_event(self, binding, event):
        return True

    def verify_live(self, binding, now_ns):
        return True


class _Continuity:
    def __init__(self, provider):
        self.provider = provider
        self.bootstrap = None

    def projection(self):
        return self.bootstrap.projection()

    def assert_receipt_binding(self, receipt, projection):
        return receipt._source is self.provider.current_source


class _Grants:
    """Server-owned live grant; ``revoked`` models revocation on a running host."""

    def __init__(self):
        self.revoked = False
        # A lease is a fixed server fact for one revision, not a sliding
        # ``now + ttl``: the inspector requires it to be stable across rechecks.
        self.expires_ns = time.time_ns() + 300_000_000_000

    def current_grant(self, principal, account_ref, now_ns):
        if self.revoked:
            raise PermissionError("grant revoked")
        return ("grant-revision-1", self.expires_ns)


class _Bindings:
    def __init__(self):
        self.expires_ns = time.time_ns() + 300_000_000_000

    def current_binding(self, account_ref, account_key, journal_scope, state_scope, now_ns):
        return ("binding-revision-1", self.expires_ns)


def _install_cut(worker, provider, continuity, principal, consumer_ref):
    """Bind the bootstrap to the current receipt and acknowledge the journal cut."""

    from bt_api_base.position_bootstrap_protocol import PositionStreamBinding, PositionStreamSnapshot

    from bt_api_py.position_stream_bootstrap import PositionStreamBootstrap

    receipt = worker.read_full_position_snapshot_receipt()
    binding = PositionStreamBinding(
        account_key=worker.account_key,
        account_ref=ACCOUNT,
        route_ref="synthetic-route",
        session_ref=receipt.session_generation,
        connection_epoch=1,
        subscription_epoch=1,
        subscription_ready_ref="fake-ready",
        valid_until_ns=time.time_ns() + 60_000_000_000,
    )
    bootstrap = PositionStreamBootstrap(binding, _FakeVerifier())
    bootstrap.install_snapshot(
        PositionStreamSnapshot(
            binding=binding,
            cut_sequence=1,
            observed_ns=receipt.coverage.observed_at_ns,
            snapshot_receipt_ref=receipt.coverage.snapshot_version,
            positions=tuple(receipt.observation.positions),
        )
    )
    continuity.bootstrap = bootstrap
    journal = worker.journal
    batch, progress = journal.read_for_consumer(
        principal, worker.journal_account_scope, worker.state_scope, consumer_ref
    )
    journal.acknowledge(
        principal,
        worker.journal_account_scope,
        worker.state_scope,
        consumer_ref,
        stream_epoch=progress.stream_epoch,
        cursor=batch.next_cursor,
    )


def _host_process(control, raw_config, registry_path, database_path, guard_dir, server_keys, client_public):
    from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal
    from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker
    from bt_api_gateway.reconciliation_authority import GatewayReconciliationInspector
    from bt_api_gateway.service_component import GatewayServiceAccountScope
    from bt_api_gateway.session_guard import LocalAccountSessionGuard

    from bt_api_py.service_components import ComponentResources
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

    host = None
    account_key = account_helpers._ACCOUNT_KEYS[ACCOUNT]
    provider = _FullProvider(account_key)
    continuity = _Continuity(provider)
    grants_port = _Grants()
    journal_scope, state_scope = f"journal:{ACCOUNT}", f"state:{ACCOUNT}"
    principal = GatewayPrincipal(
        principal_id=PRINCIPAL,
        account_scopes=frozenset({journal_scope}),
        strategy_scopes=frozenset({state_scope}),
        allowed_kinds=frozenset({GatewayCommandKind.READ}),
    )
    consumer_ref = "server-bound-reconciliation-consumer"
    holder: dict[str, Any] = {}
    try:
        config = parse_service_config(raw_config)

        def resources(component, _instance_id, options, _scope):
            journal = GatewayEventJournal(database_path)
            worker = GatewayAccountSessionWorker(
                provider,
                journal,
                account_key=account_key,
                journal_account_scope=journal_scope,
                state_scope=state_scope,
                guard=LocalAccountSessionGuard(
                    Path(guard_dir), account_key.account_key_id, owner_ref="recon-host"
                ),
                required_coverage=(RESOURCE,),
                refresh_interval_seconds=60,
                max_staleness_seconds=120,
                request_timeout_seconds=2,
            )
            inspector = GatewayReconciliationInspector(
                worker,
                continuity,
                grants_port,
                account_ref=ACCOUNT,
                required_resources=(RESOURCE,),
                principals={PRINCIPAL: (principal, consumer_ref)},
                account_bindings=_Bindings(),
            )
            holder["worker"] = worker
            return ComponentResources.owned(
                {
                    "journal": journal,
                    "accounts": {
                        ACCOUNT: GatewayServiceAccountScope(
                            account_ref=ACCOUNT,
                            execution_domain_ref=DOMAIN,
                            journal_account_scope=journal_scope,
                            account_read_strategy_scope=state_scope,
                            shared_account_facts_scope=state_scope,
                            shared_fact_strategy_refs=frozenset({STRATEGY}),
                        )
                    },
                    "principals": {PRINCIPAL: principal},
                },
                ready=lambda: True,
                close=worker.close,
                internal_ports={
                    "service.provider_session": worker,
                    "service.reconciliation": inspector,
                },
            )

        identity = WireIdentity(WireRole.STRATEGY_CLIENT, PRINCIPAL, STRATEGY)
        resource = ResourceGrant(
            "gateway-" + ACCOUNT,
            "service.account_read",
            config.scope_ref,
            WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=STRATEGY),
            OPERATIONS,
        )
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=WireIdentity(
                    WireRole.SERVICE, account_helpers._SERVICE_PRINCIPAL, config.service_id
                ),
                scope=WireScope(DOMAIN),
                scope_ref=config.scope_ref,
                scope_version="scope-recon-v1",
                security_ref=f"curve-{ACCOUNT}-v1",
                security_version="curve-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="policy-recon-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="requirements-recon-v1",
                curve_public_key=server_keys[0],
                curve_secret_key=server_keys[1],
                principals={client_public: PRINCIPAL},
                grants={PRINCIPAL: (PrincipalGrant(identity, (resource,)),)},
            ),
            registry_path,
            resources,
        )
        host.start()
        worker = holder["worker"]
        _install_cut(worker, provider, continuity, principal, consumer_ref)
        control.send(("ready", host.endpoint))
        while True:
            host.serve_once(10)
            if not control.poll():
                continue
            command = control.recv()
            if command[0] == "revoke":
                grants_port.revoked = True
                control.send(("revoked",))
            elif command[0] == "refresh":
                provider.quantity = command[1]
                worker.refresh()
                _install_cut(worker, provider, continuity, principal, consumer_ref)
                control.send(("refreshed",))
            elif command[0] == "close":
                host.close()
                control.send(("closed",))
                return
    except BaseException as exc:
        if host is not None:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__), repr(exc)[:300]))
        raise
    finally:
        control.close()


def _service_config(service):
    raw = account_helpers._service_config(service)
    settings = raw["components"]["gateway"]["settings"]
    settings["options"]["reconciliation_cut"] = True
    settings["bind_ports"] = [*settings["bind_ports"], "service.reconciliation"]
    return raw


def _wait(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_full_account_cut_reconciles_and_revocation_on_a_running_host_interrupts(tmp_path):
    context = multiprocessing.get_context("spawn")
    endpoint = account_helpers._free_endpoints(1)[0]
    service = account_helpers._account_service_info(ACCOUNT, endpoint, f"curve-{ACCOUNT}-v1")
    server_keys = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    guard_dir = tmp_path / "guards"
    guard_dir.mkdir()
    record = account_helpers._start_process(
        context,
        _host_process,
        (
            _service_config(service),
            str(tmp_path / "registry.sqlite3"),
            str(tmp_path / "journal.sqlite3"),
            str(guard_dir),
            server_keys,
            client_public,
        ),
        "reconciliation-host",
    )
    ready = account_helpers._receive(record["pipe"], timeout=30)
    assert ready[0] == "ready", ready
    raw_client = _loaded_host_timeouts(
        account_helpers._client_config(STRATEGY, {ACCOUNT: service}, (ACCOUNT,))
    )

    def runtime_factory():
        runtime = ServiceClientRuntime(
            parse_client_config(raw_client),
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, PRINCIPAL, STRATEGY),
            scope_resolver=lambda _ref: WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=STRATEGY),
            peer_resolver=lambda _service_id: service["peer"],
            transport_factory=ZmqTransportFactory(
                {service["security_ref"]: ClientCurveKeys(server_keys[0], client_public, client_secret)}
            ),
            run_id=uuid4().hex,
            requested_operations={"store.account": OPERATIONS},
        )
        runtime.start()
        return runtime

    source = GatewayReconciliationPositionSource(
        runtime_factory, account_ref=ACCOUNT, poll_interval_seconds=0.05
    )
    ledger = SqliteStrategyLedger(
        tmp_path / "ledger.sqlite3",
        account_ref=ACCOUNT,
        instruments={INSTRUMENT_ID: InstrumentTerms(Decimal("1"), "USD", "spot")},
        budgets={"A": StrategyBudget(Decimal("1000000"), "USD")},
    )
    try:
        ledger.reserve(LedgerIntent("a", "A", INSTRUMENT_ID, "BUY", Decimal("2"), Decimal("10")))
        ledger.apply_execution("a", cumulative_quantity=2, average_price=10)
        source.start(wait_seconds=20)
        assert _wait(lambda: source.report().complete), (source.error, source.positions.interruption)
        report = source.report()
        assert report.positions == {INSTRUMENT_ID: Decimal("2")}
        assert report.valid_until_ns is not None and report.valid_until_ns > time.time_ns()
        assert ledger.reconcile(report).state == "BALANCED"
        assert ledger.reader("A", account_positions=source.report).snapshot().positions == {
            INSTRUMENT_ID: Decimal("2")
        }

        # The provider position changes outside the ledger: a residual.
        assert account_helpers._host_command(record, "refresh", "5")[0] == "refreshed"
        assert _wait(lambda: source.report().positions.get(INSTRUMENT_ID) == Decimal("5"))
        time.sleep(0.05)
        assert ledger.reconcile(source.report()).state in {"RESIDUAL", "IN_FLIGHT"}

        # Revoke the grant on the RUNNING host: the next poll is refused.
        assert account_helpers._host_command(record, "revoke")[0] == "revoked"
        assert _wait(lambda: not source.report().complete)
        assert source.positions.interruption == "GATEWAY_RECONCILIATION_REFUSED"
        outcome = ledger.reconcile(source.report())
        assert (outcome.state, outcome.reason) == ("UNVERIFIED", "REPORT_INCOMPLETE")
        with pytest.raises(Exception) as info:
            ledger.reader("A", account_positions=source.report).snapshot()
        assert getattr(info.value, "code", "") == "STRATEGY_BOOK_RECONCILIATION_UNVERIFIED"
    finally:
        source.close()
        ledger.close()
        with suppress(Exception):
            account_helpers._host_command(record, "close")
        process = record["process"]
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
