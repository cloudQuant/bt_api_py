"""R16 loopback CURVE/Registry/SQLite cancel path with a synthetic provider.

The provider below never opens a venue connection. SENT is a local dispatch
observation, not an exchange cancellation or account acceptance.
"""

import multiprocessing
import os
import pickle
import queue
import sqlite3
import sys
import threading
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import zmq
from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
from bt_api_base.direct_write_protocol import (
    DirectExecutionEventKind,
    DirectSubmitResult,
    NativeOrderIdentity,
    WriteOperation,
)
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from iter63_prepared_support import compose_claimed_dispatcher

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.gateway_direct_client import (
    GatewayClientRoute,
    GatewayDirectClient,
    validate_gateway_cancel_record_progress,
)
from bt_api_py.gateway_direct_dispatch import compose_gateway_direct_cancel_dispatcher
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from bt_api_py.write_admission import WriteAdmission
from tests.integration.test_iter63_gateway_direct_dispatch import ROUTE, Provider
from tests.integration.test_iter63_gateway_direct_service import (
    ACCOUNT,
    CLIENT_A,
    CLIENT_B,
    DOMAIN,
    SERVICE,
    _client_config,
    _service_config,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_direct_client import MemoryHost

OPS = frozenset(
    {
        "direct.reserve",
        "direct.release",
        "direct.command_status",
        "direct.dispatch",
        "direct.cancel",
        "direct.cancel_status",
    }
)
MOUNT = "broker.direct_write"
INSTRUMENT = MarketInstrumentKey("synthetic", "venue", "paper", "future", "rb", "rb")


def eventually(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for owner"
        time.sleep(0.01)


class CancelHost:
    """Independent SQLite plan/record store; each read uses a fresh connection."""

    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS cancel_host (kind TEXT, id TEXT, value BLOB, PRIMARY KEY(kind,id))"
            )

    def _load(self, kind, key):
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT value FROM cancel_host WHERE kind=? AND id=?", (kind, key)
            ).fetchone()
        return None if row is None else pickle.loads(row[0])

    def save_cancel_plan(self, plan):
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO cancel_host VALUES (?, ?, ?)",
                ("plan", plan.operation_id, pickle.dumps(plan)),
            )
            winner = pickle.loads(
                db.execute(
                    "SELECT value FROM cancel_host WHERE kind=? AND id=?",
                    ("plan", plan.operation_id),
                ).fetchone()[0]
            )
        return winner

    def load_cancel_plan(self, operation_id):
        return self._load("plan", operation_id)

    def save_cancel_record(self, operation_id, record):
        if record.operation_id != operation_id:
            raise ValueError("CANCEL_OBSERVATION_CONFLICT")
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM cancel_host WHERE kind=? AND id=?",
                ("record", operation_id),
            ).fetchone()
            previous = None if row is None else pickle.loads(row[0])
            if previous is not None and previous.version > record.version:
                validate_gateway_cancel_record_progress(record, previous)
                winner = previous
            else:
                validate_gateway_cancel_record_progress(previous, record)
                db.execute(
                    "INSERT OR REPLACE INTO cancel_host VALUES (?, ?, ?)",
                    ("record", operation_id, pickle.dumps(record)),
                )
                winner = record
        return winner

    def load_cancel_record(self, operation_id):
        return self._load("record", operation_id)


class SyntheticProvider(Provider):
    def __init__(self, path):
        super().__init__(path)
        self.day, self.generation = "20300101", 1
        self.prepared, self.sent, self.released = [], [], []
        self.identity_override = None
        self.on_send = lambda: None

    def trading_day(self):
        return self.day

    def prepare_cancel_order(self, target, symbol):
        number = len(self.prepared) + 1
        prepared = PreparedCancelRequest(
            f"cancel-prepared-{number}",
            target,
            symbol,
            self.day,
            self.generation,
            {"RequestID": number, "OrderActionRef": number},
            (f"cancel-native-{self.day}-{number}",),
        )
        self.prepared.append(prepared)
        return prepared

    def release_cancel_reservation(self, prepared):
        self.released.append(prepared)

    def submit_prepared_cancel(self, prepared, grant, verifier):
        verifier.consume(
            grant,
            exchange_name=ROUTE,
            operation=WriteOperation.CANCEL,
            session_generation=self.generation,
        )
        self.sent.append(prepared)
        self.on_send()
        return DirectSubmitResult(True, self.identity_override or prepared.target_identity)


def seed_position(journal, ledger):
    row = journal.reserve(
        "strategy-a",
        "seed-trade",
        CLIENT_A.principal_ref,
        {"symbol": "rb", "side": "buy", "offset": "open", "quantity": "2"},
    )
    native = {
        "exchange_name": "TEST",
        "client_order_id": "seed",
        "session_generation": 1,
        "fields_json": "{}",
    }
    journal.begin_dispatch(
        "strategy-a",
        "seed-trade",
        row.reservation_id,
        row.digest,
        CLIENT_A.principal_ref,
        row.version,
        native,
        ("seed-anchor",),
    )
    fact = GatewayReturnFact(
        ACCOUNT,
        "TEST",
        INSTRUMENT,
        "20300101",
        DirectExecutionEventKind.TRADE,
        "seed-trade-fact",
        identity=NativeOrderIdentity("TEST", "seed", {"trading_day": "20300101"}, 1),
        identity_keys=("seed-anchor",),
        binding_keys=("seed-anchor",),
        venue_key="seed-anchor",
        trade_key=("venue", "seed-trade"),
        side="buy",
        offset="open",
        trade_quantity="1",
        trade_price="10",
    )
    assert ledger.ingest(fact).disposition == "ATTRIBUTED"


def order(key, symbol="rb"):
    return OrderRequest(
        symbol=symbol,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        account_id=ACCOUNT,
        client_order_id=key,
        price=Decimal("10"),
        idempotency_key=key,
    )


@pytest.fixture
def gateway(tmp_path):
    endpoint = _free_endpoint()
    server_public, server_secret = zmq.curve_keypair()
    a_public, a_secret = zmq.curve_keypair()
    b_public, b_secret = zmq.curve_keypair()
    config = _service_config(endpoint).to_public_dict()
    config["components"]["gateway"]["settings"]["options"].update(
        dispatch_enabled=True, cancel_enabled=True
    )
    config = parse_service_config(config)
    (tmp_path / "guard").mkdir()
    shared = {}

    def owner_factory():
        guard = LocalAccountSessionGuard(tmp_path / "guard", ACCOUNT, "owner")
        generation = guard.acquire()
        journal = GatewayDirectCommandJournal.create(
            tmp_path / "orders.sqlite", ACCOUNT, generation=generation
        )
        ledger = GatewayDirectExecutionLedger(journal, guard)
        seed_position(journal, ledger)
        provider = SyntheticProvider(tmp_path / "orders.sqlite")
        coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda _: provider)
        dispatcher = compose_claimed_dispatcher(
            coordinator, ROUTE, journal, guard, lambda _: True, expected_local_provider=provider
        )
        cancel_dispatcher = compose_gateway_direct_cancel_dispatcher(
            coordinator,
            ROUTE,
            journal,
            guard,
            dispatcher,
            lambda _: True,
            expected_local_provider=provider,
        )
        shared.update(journal=journal, provider=provider, ledger=ledger)
        return GatewayDirectOwnerResources(
            dispatcher, journal, guard.release, cancel_dispatcher=cancel_dispatcher
        )

    owner = GatewayDirectOwner(
        owner_factory, queue_size=5, max_queue_wait_seconds=30, busy_timeout_seconds=30
    )
    assert owner.start(5)
    journal, provider = shared["journal"], shared["provider"]
    binding = GatewayDirectServiceBinding(
        journal,
        {
            CLIENT_A.principal_ref: GatewayDirectGrant("strategy-a", frozenset({"rb"})),
            CLIENT_B.principal_ref: GatewayDirectGrant("strategy-b", frozenset({"sa"})),
        },
        owner=owner,
        cancel_enabled=True,
    )

    shared["binding"] = binding

    def grant(identity):
        return PrincipalGrant(
            identity,
            (
                ResourceGrant(
                    "gateway-a",
                    "service.direct_write",
                    "scope-a",
                    WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref),
                    OPS,
                ),
            ),
        )

    authority = TrustedServiceAuthority(
        identity=SERVICE,
        scope=WireScope(DOMAIN),
        scope_ref="scope-a",
        scope_version="scope-v1",
        security_ref="security-a",
        security_version="security-v1",
        registration_policy_ref="direct-policy-v1",
        registration_policy_version="direct-policy-v1",
        service_requirements_ref="direct-requirements-v1",
        service_requirements_version="direct-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={a_public: CLIENT_A.principal_ref, b_public: CLIENT_B.principal_ref},
        grants={
            CLIENT_A.principal_ref: (grant(CLIENT_A),),
            CLIENT_B.principal_ref: (grant(CLIENT_B),),
        },
    )
    ready, controls, dropped = queue.Queue(), queue.Queue(), queue.Queue()
    stop = threading.Event()

    def serve():
        host = None
        try:
            host = ServiceHost(
                config,
                authority,
                tmp_path / "registry.sqlite",
                lambda *_: ComponentResources.borrowed({"binding": binding}, ready=lambda: True),
            )
            host.start()
            ready.put(("ready", None))
            while not stop.is_set():
                try:
                    work = controls.get_nowait()
                except queue.Empty:
                    work = None
                if work:
                    work(host)
                host.serve_once(10)
        except BaseException as exc:
            ready.put(("error", getattr(exc, "code", type(exc).__name__)))
        finally:
            if host is not None and host.state == "started":
                host.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    runtimes = []
    try:
        assert ready.get(timeout=10) == ("ready", None)

        def runtime(identity=CLIENT_A, run="run-a", *, operations=OPS, drop=False):
            public, secret = (a_public, a_secret) if identity == CLIENT_A else (b_public, b_secret)

            def factory():
                factory = ZmqTransportFactory(
                    {"security-a": ClientCurveKeys(server_public, public, secret)}
                )

                class Transport:
                    def __init__(self, wrapped):
                        self.wrapped, self.dropped = wrapped, False

                    def request(self, request, *, timeout):
                        response = self.wrapped.request(request, timeout=timeout)
                        if (
                            drop
                            and request.header.operation == "direct.cancel"
                            and not self.dropped
                        ):
                            self.dropped = True
                            dropped.put(True)
                            raise TimeoutError("synthetic lost response")
                        return response

                    def close(self):
                        self.wrapped.close()

                return ServiceClientRuntime(
                    _client_config(endpoint, identity),
                    identity=identity,
                    scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
                    peer_resolver=lambda _: SERVICE,
                    transport_factory=lambda *args: Transport(factory(*args)),
                    run_id=run,
                    requested_operations=None if operations is None else {MOUNT: operations},
                )

            result = ThreadedServiceClientRuntime(factory)
            result.start(timeout_seconds=10)
            runtimes.append(result)
            return result

        def client(rt, host, cancel_host, identity=CLIENT_A, run="run-a"):
            scope = WireScope(
                DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref, run_id=run
            )
            route = GatewayClientRoute(
                MOUNT, SERVICE.actor_ref, "gateway-a", "scope-a", scope, identity
            )
            command_binding = GatewayCommandBinding(
                journal.journal_id, ACCOUNT, identity.actor_ref, identity.principal_ref
            )
            return GatewayDirectClient(
                rt, binding=command_binding, route=route, host=host, cancel_host=cancel_host
            )

        yield shared, owner, controls, dropped, runtime, client, tmp_path
    finally:
        provider.on_send = lambda: None
        for rt in reversed(runtimes):
            rt.close(timeout_seconds=10)
        stop.set()
        thread.join(10)
        assert not thread.is_alive()
        assert owner.close(10)


def place(client, journal, key):
    assert client.reserve(order(key)).outcome == "OBSERVED"
    assert client.dispatch(key).outcome == "QUEUED"
    eventually(lambda: journal.get("strategy-a", key, CLIENT_A.principal_ref).status == "SENT")
    assert client.status(key).record.state == "SENT"


def test_cancel_lost_reply_same_id_and_stop_new(gateway):
    shared, owner, controls, dropped, runtime, client, tmp = gateway
    journal, provider = shared["journal"], shared["provider"]
    host, cancel_host = MemoryHost(), CancelHost(tmp / "client.sqlite")
    rt = runtime(drop=True)
    direct = client(rt, host, cancel_host)
    place(direct, journal, "first")
    result = direct.cancel("first", operation_id="cancel-first")
    assert result.outcome == "UNKNOWN" and dropped.get(timeout=3)
    eventually(
        lambda: (
            GatewayDirectCancelJournal(journal)
            .get("strategy-a", "cancel-first", CLIENT_A.principal_ref)
            .status
            == "SENT"
        )
    )
    observed = direct.cancel_status("cancel-first")
    assert observed.outcome == "OBSERVED" and observed.record.state == "SENT"
    assert direct.cancel("first", operation_id="cancel-first").record.state == "SENT"
    assert len(provider.sent) == 1
    assert CancelHost(tmp / "client.sqlite").load_cancel_plan("cancel-first") is not None

    place(direct, journal, "second")
    conflict = direct.cancel("second", operation_id="cancel-first")
    assert conflict.outcome == "TRANSPORT_NOT_STARTED" and conflict.code == "CANCEL_NOT_DURABLE"
    assert len(provider.sent) == 1
    done = queue.Queue()
    controls.put(
        lambda host: done.put(
            host._registry.freeze_strategy(
                WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref="strategy-a", run_id="run-a"),
                "stop-a",
            )
        )
    )
    assert done.get(timeout=3)["status"] == "applied"
    blocked = direct.reserve(order("after-stop"))
    assert blocked.outcome != "OBSERVED"
    accepted = direct.cancel("second", operation_id="cancel-second")
    assert accepted.outcome in {"QUEUED", "OBSERVED"}
    eventually(lambda: len(provider.sent) == 2)
    assert journal.get("strategy-a", "second", CLIENT_A.principal_ref).status == "SENT"


def test_queued_cancel_rechecks_revoked_grant(gateway):
    shared, owner, controls, _, runtime, client, tmp = gateway
    journal, provider = shared["journal"], shared["provider"]
    direct = client(runtime(), MemoryHost(), CancelHost(tmp / "client.sqlite"))
    place(direct, journal, "one")
    place(direct, journal, "two")
    entered, unblock = threading.Event(), threading.Event()

    def hold():
        entered.set()
        assert unblock.wait(8)

    provider.on_send = hold
    try:
        assert direct.cancel("one", operation_id="hold").outcome == "QUEUED"
        assert entered.wait(5)
        assert direct.cancel("two", operation_id="queued").outcome == "QUEUED"
        revoked = threading.Event()

        def revoke(host):
            host._registry._grants = {}
            revoked.set()

        controls.put(revoke)
        assert revoked.wait(5)
        unblock.set()
        eventually(lambda: not owner.status().running and owner.status().queue_depth == 0)
        assert len(provider.sent) == 1
        assert (
            GatewayDirectCancelJournal(journal)
            .get("strategy-a", "queued", CLIENT_A.principal_ref)
            .status
            == "REQUESTED"
        )
        assert journal.get("strategy-a", "two", CLIENT_A.principal_ref).status == "SENT"
    finally:
        unblock.set()


def test_cross_principal_and_new_run_read_only_recovery(gateway):
    shared, _, _, _, runtime, client, tmp = gateway
    journal, provider = shared["journal"], shared["provider"]
    host, cancel_host = MemoryHost(), CancelHost(tmp / "client.sqlite")
    original = client(runtime(), host, cancel_host)
    place(original, journal, "owned")
    queued = original.cancel("owned", operation_id="attempt")
    assert queued.outcome == "QUEUED" and queued.record.state == "REQUESTED"
    eventually(lambda: len(provider.sent) == 1)
    final = original.cancel_status("attempt")
    assert final.record.state == "SENT"
    assert cancel_host.save_cancel_record("attempt", queued.record) == final.record
    assert cancel_host.load_cancel_record("attempt") == final.record
    plan = cancel_host.load_cancel_plan("attempt")
    assert cancel_host.save_cancel_plan(replace(plan, request_key="other")) == plan

    other = runtime(CLIENT_B, "run-b")
    record = host.load_record("owned")
    denied = other.call(
        MOUNT,
        "direct.cancel",
        {
            "request_key": "owned",
            "reservation_id": record.reservation_id,
            "digest": record.digest,
            "operation_id": "stolen",
        },
        timeout_seconds=5,
    )
    assert denied == {"status": "REQUEST_REJECTED", "code": "NOT_FOUND"}
    assert other.call(
        MOUNT, "direct.cancel_status", {"operation_id": "attempt"}, timeout_seconds=5
    ) == {"status": "REQUEST_REJECTED", "code": "NOT_FOUND"}

    # A new run registers read-only and reopens the durable plan, without a write claim.
    recovery_rt = runtime(run="run-recovery", operations=frozenset({"direct.cancel_status"}))
    recovery = client(recovery_rt, host, CancelHost(tmp / "client.sqlite"), run="run-recovery")
    result = recovery.cancel_status("attempt")
    assert result.outcome == "OBSERVED" and result.record.state == "SENT"
    assert recovery.cancel("owned", operation_id="another").outcome == "TRANSPORT_NOT_STARTED"
    assert len(provider.sent) == 1


def test_cross_account_scope_is_rejected(gateway):
    shared, _, _, _, runtime, client, tmp = gateway
    host = MemoryHost()
    rt = runtime()
    direct = client(rt, host, CancelHost(tmp / "client.sqlite"))
    place(direct, shared["journal"], "account-owned")
    record = host.load_record("account-owned")
    with pytest.raises(ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"):
        rt.call(
            MOUNT,
            "direct.cancel",
            {
                "request_key": "account-owned",
                "reservation_id": record.reservation_id,
                "digest": record.digest,
                "operation_id": "cross-account",
            },
            account_ref="other-account",
            timeout_seconds=5,
        )
    with sqlite3.connect(tmp / "orders.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM direct_cancel_attempts").fetchone()[0] == 0
    assert shared["provider"].sent == []


def test_default_registration_does_not_add_cancel(gateway):
    shared, _, _, _, runtime, client, tmp = gateway
    direct = client(runtime(operations=None), MemoryHost(), CancelHost(tmp / "client.sqlite"))
    place(direct, shared["journal"], "ordinary")
    result = direct.cancel("ordinary", operation_id="not-registered")
    assert result.outcome == "TRANSPORT_NOT_STARTED" and result.code == "ROUTE_UNAVAILABLE"
    with sqlite3.connect(tmp / "orders.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM direct_cancel_attempts").fetchone()[0] == 0


def test_migration_write_fence_prevents_cancel_creation(gateway):
    shared, _, controls, _, runtime, client, tmp = gateway
    direct = client(runtime(), MemoryHost(), CancelHost(tmp / "client.sqlite"))
    place(direct, shared["journal"], "fenced")
    done = threading.Event()

    def disable(host):
        host._registry._migration_writes_enabled = False
        done.set()

    controls.put(disable)
    assert done.wait(5)
    result = direct.cancel("fenced", operation_id="blocked")
    assert result.outcome == "UNKNOWN" and result.code == "RPC_OUTCOME_UNKNOWN"
    with sqlite3.connect(tmp / "orders.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM direct_cancel_attempts").fetchone()[0] == 0
    assert shared["provider"].sent == []
    assert shared["journal"].get("strategy-a", "fenced", CLIENT_A.principal_ref).status == "SENT"


def test_native_cancel_uncertainty_preserves_original(gateway):
    shared, _, _, _, runtime, client, tmp = gateway
    provider, journal = shared["provider"], shared["journal"]
    direct = client(runtime(), MemoryHost(), CancelHost(tmp / "client.sqlite"))
    place(direct, journal, "uncertain")

    def lost_native_reply():
        raise OSError("synthetic native failure")

    provider.on_send = lost_native_reply
    assert shared["ledger"].position("strategy-a", INSTRUMENT) == "1"
    with sqlite3.connect(tmp / "orders.sqlite") as db:
        positions_before = db.execute(
            "SELECT * FROM direct_execution_positions ORDER BY rowid"
        ).fetchall()
    assert direct.cancel("uncertain", operation_id="unknown").outcome == "QUEUED"
    cancel = GatewayDirectCancelJournal(journal)
    eventually(
        lambda: cancel.get("strategy-a", "unknown", CLIENT_A.principal_ref).status == "UNKNOWN"
    )
    assert direct.cancel_status("unknown").record.state == "UNKNOWN"
    assert direct.cancel("uncertain", operation_id="unknown").record.state == "UNKNOWN"
    assert len(provider.sent) == 1
    assert journal.get("strategy-a", "uncertain", CLIENT_A.principal_ref).status == "SENT"
    assert shared["ledger"].position("strategy-a", INSTRUMENT) == "1"
    with sqlite3.connect(tmp / "orders.sqlite") as db:
        assert (
            db.execute("SELECT * FROM direct_execution_positions ORDER BY rowid").fetchall()
            == positions_before
        )


def test_symbol_authority_is_rechecked_for_existing_order(gateway):
    from types import MappingProxyType

    shared, _, controls, _, runtime, client, tmp = gateway
    direct = client(runtime(), MemoryHost(), CancelHost(tmp / "client.sqlite"))
    place(direct, shared["journal"], "symbol-order")
    ready = threading.Event()

    def change_symbols(_host):
        shared["binding"]._grants = MappingProxyType(
            {
                CLIENT_A.principal_ref: GatewayDirectGrant("strategy-a", frozenset({"sa"})),
                CLIENT_B.principal_ref: GatewayDirectGrant("strategy-b", frozenset({"sa"})),
            }
        )
        ready.set()

    controls.put(change_symbols)
    assert ready.wait(5)
    result = direct.cancel("symbol-order", operation_id="symbol-denied")
    assert result.outcome == "REQUEST_DENIED" and result.code == "SCOPE_DENIED"
    with sqlite3.connect(tmp / "orders.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM direct_cancel_attempts").fetchone()[0] == 0
    assert shared["provider"].sent == []


def test_client_plan_failure_prevents_cancel_rpc(gateway):
    shared, _, _, _, runtime, client, tmp = gateway

    class FailedCancelHost(CancelHost):
        def save_cancel_plan(self, plan):
            raise OSError("synthetic storage failure")

    direct = client(runtime(), MemoryHost(), FailedCancelHost(tmp / "client.sqlite"))
    place(direct, shared["journal"], "durability")
    result = direct.cancel("durability", operation_id="no-plan")
    assert result.outcome == "TRANSPORT_NOT_STARTED" and result.code == "CANCEL_NOT_DURABLE"
    with sqlite3.connect(tmp / "orders.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM direct_cancel_attempts").fetchone()[0] == 0
    assert shared["provider"].sent == []


def _spawned_cancel_server(root, endpoint, server_keys, client_public, messages, controls):
    """Gateway and native substitute live solely inside this spawned process."""
    root = Path(root)
    owner = host = None
    try:
        (root / "spawn-guard").mkdir()
        shared = {}

        def owner_factory():
            guard = LocalAccountSessionGuard(root / "spawn-guard", ACCOUNT, "spawn-owner")
            journal = GatewayDirectCommandJournal.create(
                root / "spawn-orders.sqlite", ACCOUNT, generation=guard.acquire()
            )
            provider = SyntheticProvider(root / "spawn-orders.sqlite")
            coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda _: provider)
            dispatcher = compose_claimed_dispatcher(
                coordinator,
                ROUTE,
                journal,
                guard,
                lambda _: True,
                expected_local_provider=provider,
            )
            cancel_dispatcher = compose_gateway_direct_cancel_dispatcher(
                coordinator,
                ROUTE,
                journal,
                guard,
                dispatcher,
                lambda _: True,
                expected_local_provider=provider,
            )
            shared.update(journal=journal, provider=provider)
            return GatewayDirectOwnerResources(
                dispatcher, journal, guard.release, cancel_dispatcher=cancel_dispatcher
            )

        owner = GatewayDirectOwner(
            owner_factory, queue_size=4, max_queue_wait_seconds=10, busy_timeout_seconds=10
        )
        assert owner.start(10)
        journal = shared["journal"]
        binding = GatewayDirectServiceBinding(
            journal,
            {CLIENT_A.principal_ref: GatewayDirectGrant("strategy-a", frozenset({"rb"}))},
            owner=owner,
            cancel_enabled=True,
        )
        grant = PrincipalGrant(
            CLIENT_A,
            (
                ResourceGrant(
                    "gateway-a",
                    "service.direct_write",
                    "scope-a",
                    WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref),
                    OPS,
                ),
            ),
        )
        authority = TrustedServiceAuthority(
            identity=SERVICE,
            scope=WireScope(DOMAIN),
            scope_ref="scope-a",
            scope_version="scope-v1",
            security_ref="security-a",
            security_version="security-v1",
            registration_policy_ref="direct-policy-v1",
            registration_policy_version="direct-policy-v1",
            service_requirements_ref="direct-requirements-v1",
            service_requirements_version="direct-requirements-v1",
            curve_public_key=server_keys[0],
            curve_secret_key=server_keys[1],
            principals={client_public: CLIENT_A.principal_ref},
            grants={CLIENT_A.principal_ref: (grant,)},
        )
        config = _service_config(endpoint).to_public_dict()
        config["components"]["gateway"]["settings"]["options"].update(
            dispatch_enabled=True, cancel_enabled=True
        )
        host = ServiceHost(
            parse_service_config(config),
            authority,
            root / "spawn-registry.sqlite",
            lambda *_: ComponentResources.borrowed({"binding": binding}, ready=lambda: True),
        )
        host.start()
        messages.put(("ready", os.getpid(), journal.journal_id))
        while True:
            try:
                command = controls.get_nowait()
            except queue.Empty:
                command = None
            if command == "stop":
                break
            if command == "count":
                messages.put(("count", len(shared["provider"].sent)))
            host.serve_once(10)
    except BaseException as error:
        import traceback

        messages.put(("error", type(error).__name__, str(error), traceback.format_exc()))
    finally:
        try:
            if host is not None and host.state == "started":
                host.close()
            if owner is not None:
                assert owner.close(10)
            messages.put(("closed", os.getpid()))
        except BaseException as error:
            import traceback

            messages.put(
                ("cleanup_error", type(error).__name__, str(error), traceback.format_exc())
            )


def test_spawned_gateway_cancel_and_status_keep_single_native_send(tmp_path):
    context = multiprocessing.get_context("spawn")
    messages, controls = context.Queue(), context.Queue()
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    endpoint = _free_endpoint()
    process = context.Process(
        target=_spawned_cancel_server,
        args=(str(tmp_path), endpoint, server_keys, client_keys[0], messages, controls),
    )
    runtime = None
    process.start()
    try:
        ready = messages.get(timeout=20)
        assert ready[0] == "ready", ready
        assert ready[1] == process.pid != os.getpid()
        before = {
            name for name in sys.modules if name == "bt_api_ctp" or name.startswith("bt_api_ctp.")
        }

        def runtime_factory():
            return ServiceClientRuntime(
                _client_config(endpoint, CLIENT_A),
                identity=CLIENT_A,
                scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
                peer_resolver=lambda _: SERVICE,
                transport_factory=ZmqTransportFactory(
                    {"security-a": ClientCurveKeys(server_keys[0], *client_keys)}
                ),
                run_id="spawn-run",
                requested_operations={MOUNT: OPS},
            )

        runtime = ThreadedServiceClientRuntime(runtime_factory)
        runtime.start(timeout_seconds=10)
        scope = WireScope(
            DOMAIN, account_ref=ACCOUNT, strategy_ref="strategy-a", run_id="spawn-run"
        )
        route = GatewayClientRoute(
            MOUNT, SERVICE.actor_ref, "gateway-a", "scope-a", scope, CLIENT_A
        )
        binding = GatewayCommandBinding(ready[2], ACCOUNT, "strategy-a", CLIENT_A.principal_ref)
        direct = GatewayDirectClient(
            runtime,
            binding=binding,
            route=route,
            host=MemoryHost(),
            cancel_host=CancelHost(tmp_path / "spawn-client.sqlite"),
        )
        assert direct.reserve(order("spawn-order")).outcome == "OBSERVED"
        assert direct.dispatch("spawn-order").outcome == "QUEUED"
        eventually(lambda: direct.status("spawn-order").record.state == "SENT")
        assert direct.cancel("spawn-order", operation_id="spawn-cancel").outcome == "QUEUED"
        eventually(lambda: direct.cancel_status("spawn-cancel").record.state == "SENT")
        assert direct.cancel("spawn-order", operation_id="spawn-cancel").record.state == "SENT"
        controls.put("count")
        assert messages.get(timeout=5) == ("count", 1)
        after = {
            name for name in sys.modules if name == "bt_api_ctp" or name.startswith("bt_api_ctp.")
        }
        assert after == before
    finally:
        if runtime is not None:
            runtime.close(timeout_seconds=10)
        controls.put("stop")
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(5)
            pytest.fail("synthetic Gateway required termination")
        assert process.exitcode == 0
        close_message = messages.get(timeout=3)
        assert close_message == ("closed", process.pid), close_message
        controls.close()
        messages.close()
