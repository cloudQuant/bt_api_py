"""Registry admission to the actual local Risk COMMIT, without external I/O."""

import os
import sqlite3
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_risk.core.admission import AccountScope
from bt_api_risk.pretrade import engine as engine_module
from bt_api_risk.pretrade import ledger as ledger_module
from bt_api_risk.pretrade.engine import PretradeEngine
from bt_api_risk.pretrade.ledger import PretradeLedger
from bt_api_risk.risk_order_journal import RiskOrderJournal
from bt_api_risk.risk_order_journal_contracts import RiskOrderJournalError, RiskOrderWriterFence

import bt_api_py.service_registry as registry_module
from bt_api_py.risk_service_component import RiskOrderService
from bt_api_py.risk_service_config import RiskPrincipal
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCallContext,
    ServiceCapability,
    ServiceRegistry,
)
from tests.unit.test_service_registry import _next_request, _register

OP = "risk.cancel.request"
ARGS = {"operation_id": "operation-a", "scope_kind": "strategy", "scope_value": "strategy-a"}
SCOPE = AccountScope("synthetic", "account-a", "simulation")


class FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def acquire(self, *_args, **_kwargs):
        return True

    def release(self):
        pass


class MemoryConnection(sqlite3.Connection):
    """Real SQLite memory transaction with injectable COMMIT/close failures."""

    fault = None
    before_commit = None

    def execute(self, sql, parameters=()):
        if sql == "COMMIT":
            if self.before_commit is not None:
                self.before_commit()
            if self.fault == "before_commit":
                raise sqlite3.OperationalError("synthetic commit failure")
            result = super().execute(sql, parameters)
            if self.fault == "after_commit":
                raise sqlite3.OperationalError("synthetic lost commit response")
            if self.fault == "interrupt_after_commit":
                raise KeyboardInterrupt("synthetic post-commit interrupt")
            return result
        return super().execute(sql, parameters)

    def close(self):
        if self.fault == "close":
            raise sqlite3.OperationalError("synthetic close failure")
        # Preserve this test's isolated in-memory database across transactions.


@pytest.fixture
def composition(monkeypatch):
    owner_thread = object()
    monkeypatch.setattr(registry_module, "threading", SimpleNamespace(
        Lock=FakeLock, RLock=FakeLock, current_thread=lambda: owner_thread,
    ))
    db = sqlite3.connect(":memory:", isolation_level=None, factory=MemoryConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA temp_store=MEMORY")
    assert db.execute("PRAGMA temp_store").fetchone()[0] == 2
    assert db.execute("PRAGMA journal_mode=MEMORY").fetchone()[0] == "memory"
    assert db.execute("PRAGMA database_list").fetchone()[2] == ""
    db.execute("CREATE TABLE risk_order_fences(scope_key TEXT PRIMARY KEY, fencing_token INTEGER)")
    db.execute("INSERT INTO risk_order_fences VALUES(?, ?)", (SCOPE.key, 1))
    for statement in ledger_module._split_statements(ledger_module._SCHEMA):
        db.execute(statement)
    db.execute("INSERT INTO risk_pretrade_meta VALUES (?)", (ledger_module.SCHEMA_VERSION,))
    if hasattr(ledger_module, "_SOURCE_V3_SCHEMA"):
        db.execute("INSERT INTO risk_pretrade_source_guard VALUES (1, 'SOURCE_UNVERIFIED')")
    fence = RiskOrderWriterFence(SCOPE, "owner", 1, "a" * 64)
    journal = object.__new__(RiskOrderJournal)
    journal._owner_id = "owner"
    journal._connection = lambda **_: db
    journal._assert_current = lambda current: None
    ledger = object.__new__(PretradeLedger)
    ledger._journal, ledger._fence = journal, fence
    ledger._pid, ledger._thread = os.getpid(), threading.current_thread()
    ledger._v2_clock_ns, ledger._schema_version = None, 3
    engine = object.__new__(PretradeEngine)
    engine._ledger = ledger
    engine._clock = lambda: 123
    engine._apply_cancel = lambda *_: pytest.fail("local admission dispatched externally")
    principal = RiskPrincipal(
        "curve-a", "operator", "operator-a", frozenset(), frozenset({OP, "risk.cancel.status"}), b"synthetic",
        cancel_strategy_refs=frozenset({"strategy-a"}),
    )
    service = RiskOrderService(
        engine, ledger, account_scope="account-a", route="SYNTHETIC", execution_domain_ref="domain-a",
        principals={"curve-a": principal}, instrument_for=lambda *_: None, ready=lambda: True,
        strict_admin_cancel=True,
    )
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, "curve-a", "operator-a")
    scope = WireScope("domain-a", account_ref="account-a")
    capability = ServiceCapability("risk-a", "service.risk_admin", frozenset({OP}), frozenset({OP}))
    resource = ResourceGrant("risk-a", "service.risk_admin", "scope-a", scope, frozenset({OP}))
    registry = ServiceRegistry(
        ":memory:", service_id="risk-service", capabilities=(capability,),
        grants={"curve-a": (PrincipalGrant(identity, (resource,)),)},
        policy_version="policy-v1", requirements_version="requirements-v1",
        clock=lambda: 100.0, lease_seconds=10, enable_risk_admin_cancel_admission=True,
    )
    registry._db.execute("PRAGMA temp_store=MEMORY")
    assert registry._db.execute("PRAGMA temp_store").fetchone()[0] == 2
    assert registry._db.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    assert registry._db.execute("PRAGMA database_list").fetchone()[2] == ""
    registry.register_handler("risk-a", OP, lambda context, args: service.handle(OP, context=context, arguments=args))
    registration = _register(registry, principal="curve-a", identity=identity, scope=scope,
                             instance_id="risk-a", port="service.risk_admin", scope_ref="scope-a",
                             operations=(OP,))
    assert registration["ok"]

    def dispatch(arguments=ARGS):
        result = registry.handle("curve-a", _next_request(OP, {
            "registration_id": registration["result"]["registration_id"],
            "service_epoch": registration["result"]["service_epoch"],
            "instance_id": "risk-a", "arguments": arguments,
        }, identity=identity, scope=scope))
        assert result["ok"], result
        return result["result"]

    yield SimpleNamespace(db=db, journal=journal, ledger=ledger, engine=engine, service=service,
                          registry=registry, identity=identity, scope=scope, dispatch=dispatch)
    registry.close()
    sqlite3.Connection.close(db)


def row(db):
    return db.execute("SELECT state,attempts,row_version FROM risk_pretrade_cancel_intents").fetchone()


def test_real_registry_and_storage_commit_once_without_gateway(composition):
    c = composition
    registry_checks = []
    original = c.registry._risk_admin_cancel_admission_error

    def observe(binding):
        registry_checks.append("recheck")
        return original(binding)

    c.registry._risk_admin_cancel_admission_error = observe
    c.db.before_commit = lambda: registry_checks.append("COMMIT")
    result = c.dispatch()
    assert result == {"status": "OBSERVED", "operation_id": "operation-a", "state": "UNKNOWN"}
    assert tuple(row(c.db)) == ("UNKNOWN", 1, 2)
    assert c.db.execute("SELECT source FROM risk_pretrade_cancel_intents").fetchone()[0].startswith(
        "operator-local-v3:"
    )
    assert registry_checks[-1] == "COMMIT" and registry_checks[-2] == "recheck"
    assert len(registry_checks) >= 4
    assert c.dispatch() == result
    assert tuple(row(c.db)) == ("UNKNOWN", 1, 2)
    assert c.db.execute("SELECT count(*) FROM risk_pretrade_audit").fetchone()[0] == 2


@pytest.mark.parametrize("kind,value", [("strategy", "strategy-a"), ("account", ""),
                                        ("instrument", "SHFE:rb")])
def test_local_only_source_survives_default_mode_restart_without_remote_status(composition, kind, value):
    c = composition
    original = c.service._principals["curve-a"]
    principal = RiskPrincipal(
        original.principal_ref, original.role, original.actor_ref, original.strategy_refs,
        original.operations, original.public_key, cancel_account_allowed=True,
        cancel_strategy_refs=frozenset({"strategy-a"}), cancel_instrument_refs=frozenset({"SHFE:rb"}),
    )
    c.service._principals = {"curve-a": principal}
    args = dict(ARGS, scope_kind=kind, scope_value=value)
    assert c.dispatch(args)["state"] == "UNKNOWN"
    restarted = RiskOrderService(
        c.engine, c.ledger, account_scope="account-a", route="SYNTHETIC", execution_domain_ref="domain-a",
        principals={"curve-a": principal}, instrument_for=lambda *_: None, ready=lambda: True,
        cancel_status=lambda *_args, **_kwargs: pytest.fail("local-only intent queried Gateway"),
    )
    context = ServiceCallContext(c.identity, c.scope, "synthetic", "curve-a")
    result = restarted.handle("risk.cancel.status", context=context, arguments={"operation_id": "operation-a"})
    assert result == {"status": "OBSERVED", "operation_id": "operation-a", "state": "UNKNOWN", "gateway": None}
    duplicate = restarted.handle(OP, context=context, arguments=args)
    assert duplicate == {"status": "OBSERVED", "operation_id": "operation-a", "state": "UNKNOWN"}
    changed = restarted.handle(OP, context=context, arguments=dict(args, scope_kind="account", scope_value=""))
    if kind == "account":
        assert changed == duplicate
    else:
        assert changed == {"status": "REQUEST_REJECTED", "code": "risk_request_conflict"}
    assert tuple(row(c.db)) == ("UNKNOWN", 1, 2)


def test_local_only_source_does_not_authorize_a_different_actor(composition):
    c = composition
    assert c.dispatch()["state"] == "UNKNOWN"
    other = RiskPrincipal(
        "curve-a", "operator", "replacement-actor", frozenset(), frozenset({OP, "risk.cancel.status"}),
        b"synthetic", cancel_strategy_refs=frozenset({"strategy-a"}),
    )
    restarted = RiskOrderService(
        c.engine, c.ledger, account_scope="account-a", route="SYNTHETIC", execution_domain_ref="domain-a",
        principals={"curve-a": other}, instrument_for=lambda *_: None, ready=lambda: True,
        cancel_status=lambda *_args, **_kwargs: pytest.fail("different actor queried Gateway"),
    )
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, "curve-a", "replacement-actor")
    context = ServiceCallContext(identity, c.scope, "synthetic", "curve-a")
    assert restarted.handle("risk.cancel.status", context=context, arguments={"operation_id": "operation-a"}) == {
        "status": "NOT_FOUND", "operation_id": "operation-a",
    }
    assert restarted.handle(OP, context=context, arguments=ARGS) == {
        "status": "REQUEST_REJECTED", "code": "risk_request_conflict",
    }
    assert tuple(row(c.db)) == ("UNKNOWN", 1, 2)


def test_commit_guard_runs_after_finalizers_and_immediately_before_commit(composition, monkeypatch):
    c = composition
    events = []
    original_deadlines = ledger_module.LedgerTxn._v2_check_deadlines
    original_check = c.registry._risk_admin_cancel_admission_error

    def deadline(txn, *, committed):
        events.append("after_commit" if committed else "finalizer")
        return original_deadlines(txn, committed=committed)

    def authority(binding):
        events.append("registry_recheck")
        return original_check(binding)

    monkeypatch.setattr(ledger_module.LedgerTxn, "_v2_check_deadlines", deadline)
    c.registry._risk_admin_cancel_admission_error = authority
    c.db.before_commit = lambda: events.append("commit")
    assert c.dispatch()["state"] == "UNKNOWN"
    finalizer = events.index("finalizer")
    commit = events.index("commit")
    assert "registry_recheck" in events[finalizer + 1:commit]
    assert events[commit - 1] == "registry_recheck"
    assert events[commit + 1] == "after_commit"


def test_grant_revoked_in_late_fence_callback_rolls_back_and_never_sends(composition):
    c = composition
    original = c.journal._assert_current_in
    calls = []

    def check(connection, fence):
        original(connection, fence)
        calls.append(1)
        if len(calls) == 3:
            c.registry._risk_admin_cancel_admission_enabled = False

    c.journal._assert_current_in = check
    assert c.dispatch()["state"] == "UNKNOWN"
    assert len(calls) == 3
    assert row(c.db) is None


def test_installed_risk_grant_replacement_before_commit_rolls_back(composition):
    c = composition
    original = c.journal._assert_current_in
    calls = []

    def check(connection, fence):
        original(connection, fence)
        calls.append(1)
        if len(calls) == 3:
            c.service._principals = {}

    c.journal._assert_current_in = check
    assert c.dispatch()["state"] == "UNKNOWN"
    assert len(calls) == 3 and row(c.db) is None


@pytest.mark.parametrize("fault,committed", [("before_commit", False), ("after_commit", True), ("close", True)])
def test_commit_or_close_failure_is_unknown_and_never_retried(composition, fault, committed):
    c = composition
    c.db.fault = fault
    assert c.dispatch()["state"] == "UNKNOWN"
    c.db.fault = None
    assert (row(c.db) is not None) is committed
    if committed:
        assert tuple(row(c.db)) == ("UNKNOWN", 1, 2)
        assert c.dispatch()["state"] == "UNKNOWN"
        assert tuple(row(c.db)) == ("UNKNOWN", 1, 2)


def test_scope_exit_failure_after_commit_keeps_local_claim_unknown(composition):
    c = composition
    original_snapshot = c.registry._read_snapshot
    committed = []

    @contextmanager
    def fail_after_commit():
        with original_snapshot():
            yield
        if committed:
            raise RuntimeError("synthetic Registry snapshot close failure")

    c.registry._read_snapshot = fail_after_commit
    c.db.before_commit = lambda: committed.append(True)
    assert c.dispatch()["state"] == "UNKNOWN"
    assert tuple(row(c.db)) == ("UNKNOWN", 1, 2)


def test_interrupt_at_commit_preserves_abort_and_does_not_send(composition):
    c = composition
    def interrupt():
        raise KeyboardInterrupt("synthetic interrupt")

    c.db.before_commit = interrupt
    with pytest.raises(KeyboardInterrupt, match="synthetic interrupt"):
        c.dispatch()
    assert row(c.db) is None


def test_interrupt_after_commit_leaves_unknown_and_is_not_resent(composition):
    c = composition
    c.db.fault = "interrupt_after_commit"
    with pytest.raises(KeyboardInterrupt, match="synthetic post-commit interrupt"):
        c.dispatch()
    c.db.fault = None
    assert tuple(row(c.db)) == ("UNKNOWN", 1, 2)
    assert c.dispatch()["state"] == "UNKNOWN"
    assert tuple(row(c.db)) == ("UNKNOWN", 1, 2)


def test_replaced_ledger_transaction_is_rejected_before_use(composition):
    c = composition
    c.ledger.transaction = lambda **_: pytest.fail("replacement ledger transaction used")
    result = c.dispatch()
    assert result == {"status": "REQUEST_REJECTED", "code": "risk_unauthorized"}
    assert row(c.db) is None


@pytest.mark.parametrize("source,expected", [
    ("operator:curve-a", ("PENDING", 0, 1)),
    ("foreign-source", ("PENDING", 0, 1)),
])
def test_existing_legacy_or_foreign_intent_is_never_sent(composition, source, expected):
    c = composition
    c.db.execute(
        "INSERT INTO risk_pretrade_cancel_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (SCOPE.key, "operation-a", source, "strategy", "strategy-a", None, None,
         "PENDING", 0, None, None, 1, 1, 1),
    )
    assert c.dispatch()["state"] == "UNKNOWN"
    assert tuple(row(c.db)) == expected
    assert c.db.execute("SELECT count(*) FROM risk_pretrade_audit").fetchone()[0] == 0


@pytest.mark.parametrize("kind,value", [("strategy", "strategy-a"), ("account", ""),
                                        ("instrument", "SHFE:rb")])
@pytest.mark.parametrize("state,code", [
    ("PENDING", "risk_admin_cancel_local_only"),
    ("WAITING_CONTROL", "risk_admin_cancel_local_only"),
    ("UNKNOWN", "old-finite-code"),
    ("ACCEPTED", "old-finite-code"),
    ("REJECTED", "old-finite-code"),
])
def test_existing_unbound_v2_intent_stays_local_after_mode_change(composition, kind, value, state, code):
    c = composition
    from bt_api_risk.pretrade.ports import CallerIdentity

    original = c.service._principals["curve-a"]
    principal = RiskPrincipal(
        original.principal_ref, original.role, original.actor_ref, original.strategy_refs,
        original.operations, original.public_key, cancel_account_allowed=True,
        cancel_strategy_refs=frozenset({"strategy-a"}), cancel_instrument_refs=frozenset({"SHFE:rb"}),
    )
    c.service._principals = {"curve-a": principal}
    caller = CallerIdentity("curve-a", "operator-a", "operator", frozenset(), None, frozenset({OP}))
    c.db.execute(
        "INSERT INTO risk_pretrade_cancel_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (SCOPE.key, "operation-a", caller.cancel_intent_source(), kind, value, None, None,
         state, 0, None, code, 1, 1, 1),
    )
    args = dict(ARGS, scope_kind=kind, scope_value=value)
    assert c.dispatch(args)["state"] == "UNKNOWN"
    result = c.db.execute("SELECT state,last_code FROM risk_pretrade_cancel_intents").fetchone()
    assert tuple(result) == (state, code)
    restarted = RiskOrderService(
        c.engine, c.ledger, account_scope="account-a", route="SYNTHETIC", execution_domain_ref="domain-a",
        principals={"curve-a": principal}, instrument_for=lambda *_: None, ready=lambda: True,
        cancel_status=lambda *_args, **_kwargs: pytest.fail("unbound intent queried Gateway"),
    )
    context = ServiceCallContext(c.identity, c.scope, "synthetic", "curve-a")
    assert restarted.handle("risk.cancel.status", context=context, arguments={"operation_id": "operation-a"}) == {
        "status": "OBSERVED", "operation_id": "operation-a", "state": "UNKNOWN", "gateway": None,
    }
    assert restarted.handle(OP, context=context, arguments=args) == {
        "status": "OBSERVED", "operation_id": "operation-a", "state": "UNKNOWN",
    }
    assert tuple(c.db.execute("SELECT state,last_code FROM risk_pretrade_cancel_intents").fetchone()) == (state, code)


def configure_maintenance(engine, monkeypatch):
    monkeypatch.setattr(engine_module, "_v1_source_quarantined", lambda: False, raising=False)
    engine._holding_permits_consistent = lambda txn: True
    engine._holding_parents_consistent = lambda txn: True
    engine._parent_contract_verified = lambda: True
    engine._evaluate_loss_l4 = lambda: None
    engine._recheck_ns, engine._recheck_limit = 1, 5


def test_historical_retry_hook_never_scans_control_or_cancel():
    engine = object.__new__(PretradeEngine)
    engine._ledger = SimpleNamespace(transaction=lambda: pytest.fail("historical ledger was scanned"))
    engine._apply_control = lambda *_args, **_kwargs: pytest.fail("historical control was retried")
    engine._apply_cancel = lambda *_args, **_kwargs: pytest.fail("historical cancel was retried")
    engine._retry_intents()


def test_status_reports_historical_retry_unavailable(monkeypatch):
    engine = object.__new__(PretradeEngine)
    engine._active = None
    engine._store = SimpleNamespace(load_error=None)
    engine._fact = lambda *_: None
    monkeypatch.setattr(engine_module, "_v1_source_quarantined", lambda: False, raising=False)
    txn = SimpleNamespace(
        cursor=lambda: {"continuity": "ok", "epoch": 1, "last_sequence": 0,
                        "verified_from_zero": True, "gap_reason": None},
        bypass_count=lambda: 0, permits_in=lambda *_: [], permit_count=lambda: 0,
        active_latches=lambda: [], control_intents=lambda *_: [], cancel_intents=lambda *_: [],
    )

    @contextmanager
    def transaction():
        yield txn

    engine._ledger = SimpleNamespace(transaction=transaction)
    assert engine.status_summary()["intent_retry_unavailable"] == 1
    principal = RiskPrincipal("curve-a", "operator", "operator-a", frozenset(),
                              frozenset({"risk.admin.status"}), b"synthetic")
    service = RiskOrderService(
        engine, engine._ledger, account_scope="account-a", route="SYNTHETIC", execution_domain_ref="domain-a",
        principals={"curve-a": principal}, instrument_for=lambda *_: None, ready=lambda: True,
    )
    context = ServiceCallContext(WireIdentity(WireRole.APPLICATION_CLIENT, "curve-a", "operator-a"),
                                 WireScope("domain-a", account_ref="account-a"), "synthetic", "curve-a")
    result = service.handle("risk.admin.status", context=context, arguments={})
    assert result["status"] == "OBSERVED"
    assert result["summary"]["intent_retry_unavailable"] == 1


def test_real_maintain_never_retries_strict_or_historical_cancels(composition, monkeypatch):
    c = composition
    assert c.dispatch()["state"] == "UNKNOWN"
    c.db.execute(
        "INSERT INTO risk_pretrade_cancel_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (SCOPE.key, "old-operation", "operator:other", "strategy", "strategy-a", None, None,
         "PENDING", 0, None, None, 1, 1, 1),
    )
    configure_maintenance(c.engine, monkeypatch)
    c.engine.maintain()
    restarted = object.__new__(PretradeEngine)
    restarted._ledger, restarted._clock = c.ledger, lambda: 124
    restarted._apply_cancel = lambda *_: pytest.fail("historical cancel was retried")
    configure_maintenance(restarted, monkeypatch)
    restarted.maintain()
    rows = c.db.execute("SELECT operation_id,state,attempts FROM risk_pretrade_cancel_intents "
                        "ORDER BY operation_id").fetchall()
    assert [tuple(item) for item in rows] == [("old-operation", "PENDING", 0),
                                               ("operation-a", "UNKNOWN", 1)]


def test_strict_mode_rejects_missing_admission_and_ungranted_scope(composition):
    c = composition
    context = ServiceCallContext(c.identity, c.scope, "synthetic", "curve-a")
    rejected = c.service.handle(OP, context=context, arguments=ARGS)
    assert rejected == {"status": "REQUEST_REJECTED", "code": "risk_unauthorized"}
    result = c.dispatch(dict(ARGS, scope_value="strategy-b"))
    assert result == {"status": "REQUEST_REJECTED", "code": "risk_unauthorized"}
    assert row(c.db) is None


def test_strict_ledger_rejects_non_native_transaction_manager(composition):
    c = composition
    c.journal._transaction = lambda **_: pytest.fail("untrusted transaction manager used")
    assert c.dispatch()["state"] == "UNKNOWN"
    assert row(c.db) is None


def test_storage_guard_requires_none_and_rolls_back(composition):
    c = composition
    with (pytest.raises(RiskOrderJournalError, match="PRETRADE_ADMIN_CANCEL_COMMIT_UNCERTAIN"),
          c.ledger.transaction(_before_commit=lambda: False) as txn):
        txn.ensure_cancel_intent(operation_id="not-committed", source="synthetic", scope_kind="account",
                                 scope_value="", latch_id=None, wait_cause_id=None, now_ns=1)
    assert row(c.db) is None
