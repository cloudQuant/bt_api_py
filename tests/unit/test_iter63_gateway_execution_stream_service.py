"""Durable event cursors and authorization around the in-process stream service."""

import json
import os
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import NativeOrderIdentity
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.ctp_gateway_return_ingestion import CtpGatewayReturnIngestor
from bt_api_py.direct_write import project_direct_strong_identity_keys
from bt_api_py.gateway_execution_stream_service import GatewayExecutionStreamService
from bt_api_py.service_registry import ServiceCallContext, ServiceResultTooLargeError
from tests.integration.test_iter63_ctp_gateway_return_ingestion import (
    ACCOUNT,
    ACCOUNT_KEY,
    DAY,
    INSTRUMENT,
    _order,
    _reader,
    _trade,
)


def _command(journal, strategy, principal, key, ref):
    row = journal.reserve(
        strategy,
        key,
        principal,
        {"symbol": "rb2701", "side": "buy", "offset": "open", "quantity": "2"},
    )
    identity = NativeOrderIdentity(
        "CTP___FUTURE",
        ref,
        {"trading_day": DAY, "order_ref": ref, "front_id": 7, "session_id": 19},
        None,
    )
    wire = {
        "exchange_name": "CTP___FUTURE",
        "client_order_id": ref,
        "fields_json": json.dumps(dict(identity.fields), sort_keys=True, separators=(",", ":")),
        "session_generation": None,
    }
    journal.begin_dispatch(
        strategy,
        key,
        row.reservation_id,
        row.digest,
        principal,
        row.version,
        wire,
        project_direct_strong_identity_keys(identity, day_scoped_ids=True),
    )


@pytest.fixture
def stream_system(tmp_path):
    directory = tmp_path / "guard"
    directory.mkdir()
    guard = LocalAccountSessionGuard(directory, ACCOUNT, "owner")
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal.sqlite", ACCOUNT, generation=generation
    )
    ledger = GatewayDirectExecutionLedger(journal, guard)
    reader, _, native = _reader()
    ingestor = CtpGatewayReturnIngestor(
        reader, ledger, account_key=ACCOUNT_KEY, instruments=(INSTRUMENT,)
    )
    _command(journal, "strategy-a", "principal-a", "key-a", "000000000001")
    _command(journal, "strategy-b", "principal-b", "key-b", "000000000002")
    native.rows["orders"] = [_order()]
    ingestor.read_and_ingest("orders", timeout=1)
    native.rows["orders"] = [_order(OrderRef="000000000002", OrderSysID="sys-2")]
    ingestor.read_and_ingest("orders", timeout=1)
    native.rows["trades"] = [_trade()]
    ingestor.read_and_ingest("trades", timeout=1)
    stream = GatewayExecutionStreamService(
        ledger,
        journal,
        {"principal-a": "strategy-a", "principal-alt": "strategy-a", "principal-b": "strategy-b"},
        execution_domain_ref="paper:demo",
    )
    yield stream, ledger, journal
    stream.close()
    guard.release()


def _context(principal="principal-a", strategy="strategy-a", *, run="run-a", fits=lambda _: True):
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal, strategy)
    return ServiceCallContext(
        identity,
        WireScope("paper:demo", account_ref=ACCOUNT, strategy_ref=strategy, run_id=run),
        "registration",
        principal,
        fits,
    )


def _call(stream, operation, args, context=None):
    return stream.handle(
        "direct.events." + operation, context=context or _context(), arguments=args
    )


def test_strategy_isolation_sparse_sequences_and_run_independent_cursor(stream_system):
    stream, _, _ = stream_system
    args = {"after_sequence": 0, "expected_epoch": None, "limit": 256}
    a = _call(stream, "read_after", args)["page"]
    b = _call(stream, "read_after", args, _context("principal-b", "strategy-b"))["page"]
    assert [row["sequence"] for row in a["events"]] == [1, 3]
    assert [row["sequence"] for row in b["events"]] == [2]
    assert [row["request_key"] for row in a["events"]] == ["key-a", "key-a"]
    assert a["events"][0]["command_principal"] == "principal-a"
    serialized = json.dumps(a)
    assert all(
        value not in serialized for value in ("identity_keys", '"source_ref"', '"trade_key"')
    )
    ack = _call(stream, "ack", {"sequence": 3, "expected_epoch": a["epoch"]})["progress"]
    assert ack["acknowledged_sequence"] == 3
    again = _call(stream, "progress", {}, _context(run="run-later"))["progress"]
    assert again["consumer_ref"] == a["consumer_ref"] and again["acknowledged_sequence"] == 3
    alternate = _call(stream, "progress", {}, _context("principal-alt", "strategy-a"))["progress"]
    assert (
        alternate["consumer_ref"] != a["consumer_ref"] and alternate["acknowledged_sequence"] == 0
    )


def test_budget_denial_keeps_offer_and_ack_unchanged(stream_system):
    stream, _, _ = stream_system
    args = {"after_sequence": 0, "expected_epoch": None, "limit": 256}
    with pytest.raises(ServiceResultTooLargeError):
        _call(stream, "read_after", args, _context(fits=lambda _: False))
    progress = _call(stream, "progress", {})["progress"]
    assert progress["offered_sequence"] == 0
    page = _call(stream, "read_after", args)["page"]
    with pytest.raises(ServiceResultTooLargeError):
        _call(
            stream,
            "ack",
            {"sequence": 3, "expected_epoch": page["epoch"]},
            _context(fits=lambda _: False),
        )
    progress = _call(stream, "progress", {})["progress"]
    assert progress["acknowledged_sequence"] == 0 and progress["offered_sequence"] == 3
    assert (
        _call(stream, "ack", {"sequence": 2, "expected_epoch": page["epoch"]})["code"]
        == "INVALID_SEQUENCE"
    )
    assert (
        _call(stream, "ack", {"sequence": 3, "expected_epoch": "f" * 64})["code"]
        == "EPOCH_MISMATCH"
    )
    assert (
        _call(stream, "ack", {"sequence": 3, "expected_epoch": "wrong"})["code"]
        == "INVALID_REQUEST"
    )
    assert (
        _call(
            stream, "read_after", {"after_sequence": 2**63, "expected_epoch": "f" * 64, "limit": 1}
        )["code"]
        == "INVALID_REQUEST"
    )


def test_exact_scope_and_close_fence(stream_system):
    stream, _, _ = stream_system
    context = _context()
    for bad in (
        replace(context, identity=replace(context.identity, actor_ref="other")),
        replace(context, identity=replace(context.identity, delegate_ref="delegate")),
        replace(context, scope=replace(context.scope, execution_domain_ref="other")),
        replace(context, scope=replace(context.scope, account_ref="other")),
        replace(context, scope=replace(context.scope, run_id=None)),
    ):
        assert _call(stream, "progress", {}, bad)["code"] == "SCOPE_DENIED"
    assert _call(stream, "progress", {"strategy": "strategy-a"})["code"] == "INVALID_REQUEST"
    stream.close()
    assert _call(stream, "progress", {})["code"] == "EVENTS_UNAVAILABLE"


def test_close_waits_for_inflight_read(stream_system):
    stream, _, _ = stream_system
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    result = []

    def fits(_):
        entered.set()
        assert release.wait(5)
        return True

    worker = threading.Thread(
        target=lambda: result.append(
            _call(
                stream,
                "read_after",
                {"after_sequence": 0, "expected_epoch": None, "limit": 1},
                _context(fits=fits),
            )
        )
    )
    worker.start()
    assert entered.wait(5)
    closer = threading.Thread(target=lambda: (stream.close(), closed.set()))
    closer.start()
    assert not closed.wait(0.05)
    release.set()
    worker.join(5)
    closer.join(5)
    assert closed.is_set() and result[0]["status"] == "OK"
    assert _call(stream, "progress", {})["code"] == "EVENTS_UNAVAILABLE"


def test_late_poll_cannot_reopen_closed_reservation_gate():
    from bt_api_py.ctp_gateway_return_maintenance import CtpGatewayReturnMaintenance

    entered, release = threading.Event(), threading.Event()

    class Buffer:
        def status(self):
            return SimpleNamespace(gap=False, record_count=0)

        def offer(self, *, limit):
            entered.set()
            assert release.wait(5)
            return SimpleNamespace(records=())

        def ack(self, batch):
            pass

    maintenance = CtpGatewayReturnMaintenance.__new__(CtpGatewayReturnMaintenance)
    maintenance._lock = threading.Lock()
    maintenance._pid = os.getpid()
    maintenance._closing = threading.Event()
    maintenance._open = True
    maintenance._status = "HEALTHY"
    maintenance._done = {"orders", "trades"}
    maintenance._stale_seen = False
    maintenance._generation = 1
    maintenance._settings = SimpleNamespace(callback_batch_limit=1)
    maintenance._session = SimpleNamespace(return_buffer=Buffer())
    maintenance._provider = SimpleNamespace(session_generation=lambda: 1)
    maintenance._ingestor = SimpleNamespace(assert_session_current=lambda: None)
    maintenance._ledger = SimpleNamespace(reconcile_pending=lambda **_: ())
    worker = threading.Thread(target=maintenance.poll)
    worker.start()
    assert entered.wait(5)
    maintenance.close()
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert maintenance.status() == {"status": "OWNER_CLOSED", "reservation_open": False}
    assert not maintenance.dispatch_ready()


def test_events_only_recovery_does_not_include_release():
    from bt_api_py.gateway_direct_service import (
        EVENT_OPERATIONS,
        execution_events_requested_operations,
        recovery_requested_operations,
    )

    assert execution_events_requested_operations()["broker.direct_write"] == EVENT_OPERATIONS
    assert "direct.release" not in execution_events_requested_operations()["broker.direct_write"]
    assert recovery_requested_operations(include_execution_events=True)["broker.direct_write"] == (
        recovery_requested_operations()["broker.direct_write"] | EVENT_OPERATIONS
    )


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1, True, "1"])
def test_maintenance_invalid_close_timeout_does_not_close_gate(timeout):
    from bt_api_py.ctp_gateway_return_maintenance import CtpGatewayReturnMaintenance

    maintenance = CtpGatewayReturnMaintenance.__new__(CtpGatewayReturnMaintenance)
    maintenance._pid = os.getpid()
    maintenance._closing = threading.Event()
    maintenance._lock = threading.Lock()
    maintenance._open, maintenance._status = True, "HEALTHY"
    with pytest.raises(ValueError, match="INVALID_CLOSE_TIMEOUT"):
        maintenance.close(timeout)
    assert maintenance.status() == {"status": "HEALTHY", "reservation_open": True}


def test_maintenance_close_checks_pid_before_event_or_gate():
    from bt_api_py.ctp_gateway_return_maintenance import CtpGatewayReturnMaintenance

    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError("synchronization accessed before PID check")

    maintenance = CtpGatewayReturnMaintenance.__new__(CtpGatewayReturnMaintenance)
    maintenance._pid = os.getpid() + 1
    maintenance._closing = Forbidden()
    maintenance._lock = Forbidden()
    with pytest.raises(RuntimeError, match="PROCESS_BOUNDARY"):
        maintenance.begin_close()
    with pytest.raises(RuntimeError, match="PROCESS_BOUNDARY"):
        maintenance.close(float("nan"))


@pytest.mark.parametrize("operation", ["direct.events.read_after", "direct.events.ack"])
def test_registry_grants_and_migration_gate_protect_cursor(stream_system, tmp_path, operation):
    from bt_api_py.gateway_direct_service import EVENT_OPERATIONS
    from bt_api_py.service_migration_gate import arm_migration_cutover, read_migration_marker
    from bt_api_py.service_registry import (
        PrincipalGrant,
        ResourceGrant,
        ServiceCapability,
        ServiceRegistry,
    )
    from tests.unit.test_service_registry_freeze import _request

    stream, _, _ = stream_system
    initial_page = None
    if operation == "direct.events.ack":
        initial_page = _call(
            stream,
            "read_after",
            {
                "after_sequence": 0,
                "expected_epoch": None,
                "limit": 10,
            },
        )["page"]
    path = tmp_path / "registry.sqlite"
    arm_migration_cutover(
        path, source_epoch="old", new_epoch="new", migration_id="events-migration"
    )
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "strategy-a")
    scope = WireScope("paper:demo", account_ref=ACCOUNT, strategy_ref="strategy-a", run_id="run-a")
    capability = ServiceCapability("gateway-a", "service.direct_write", EVENT_OPERATIONS)
    grant = ResourceGrant(
        "gateway-a",
        "service.direct_write",
        "scope-a",
        replace(scope, run_id=None),
        EVENT_OPERATIONS,
    )
    registry = ServiceRegistry(
        path,
        service_id="gateway-service",
        capabilities=(capability,),
        grants={"principal-a": (PrincipalGrant(identity, (grant,)),)},
        policy_version="policy",
        requirements_version="requirements",
        migration_deployment_epoch="new",
    )
    for event_operation in EVENT_OPERATIONS:
        registry.register_handler(
            "gateway-a",
            event_operation,
            lambda context, arguments, event_operation=event_operation: stream.handle(
                event_operation, context=context, arguments=arguments
            ),
        )
    registration = registry.handle(
        "principal-a",
        _request(
            "service.register",
            {
                "registration_key": "registration-a",
                "client_session_id": "client-a",
                "instance_id": "gateway-a",
                "port": "service.direct_write",
                "scope_ref": "scope-a",
                "operations": sorted(EVENT_OPERATIONS),
            },
            identity=identity,
            scope=scope,
        ),
    )
    assert registration["ok"] is True
    receipt = registration["result"]

    def call(operation, arguments):
        return registry.handle(
            "principal-a",
            _request(
                operation,
                {
                    "registration_id": receipt["registration_id"],
                    "service_epoch": receipt["service_epoch"],
                    "instance_id": "gateway-a",
                    "arguments": arguments,
                },
                identity=identity,
                scope=scope,
            ),
        )

    try:
        initial = call("direct.events.progress", {})["result"]["progress"]
        assert initial["offered_sequence"] == (3 if initial_page else 0)
        args = (
            {"after_sequence": 0, "expected_epoch": None, "limit": 10}
            if initial_page is None
            else {"sequence": 3, "expected_epoch": initial_page["epoch"]}
        )
        blocked = call(operation, args)
        assert blocked["code"] == "MIGRATION_WRITE_DISABLED"
        current = _call(stream, "progress", {})["progress"]
        assert current["offered_sequence"] == initial["offered_sequence"]
        assert current["acknowledged_sequence"] == 0
        assert read_migration_marker(path).status == "COMMITTED"
        registry.enable_migration_writes(
            migration_id="events-migration", deployment_epoch="new", readiness_sha256="a" * 64
        )
        result = call(operation, args)
        assert result["ok"]
        if initial_page:
            assert result["result"]["progress"]["acknowledged_sequence"] == 3
        else:
            assert "page" in result["result"], result
            assert result["result"]["page"]["offered_sequence"] == 3, result
        marker = read_migration_marker(path)
        assert marker.status == "SEALED" and marker.first_write_fact_kind == "cursor"
        forged = registry.handle(
            "principal-a",
            _request(
                "direct.events.progress",
                {
                    "registration_id": receipt["registration_id"],
                    "service_epoch": receipt["service_epoch"],
                    "instance_id": "gateway-a",
                    "arguments": {},
                },
                identity=replace(identity, actor_ref="other"),
                scope=scope,
            ),
        )
        assert not forged["ok"]
    finally:
        registry.close()
