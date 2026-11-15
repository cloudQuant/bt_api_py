"""Real engine and SDK control adapter with fake state; no database or service."""

from contextlib import contextmanager
from copy import deepcopy
import os
import sqlite3
import threading

import pytest

from bt_api_risk.pretrade import engine as engine_module
from bt_api_risk.pretrade.engine import PretradeEngine
from bt_api_py.risk_service_gateway import GatewayControlAdapter


class FakeTxn:
    def __init__(self):
        self.latches = {"latch": {"active": True, "scope_kind": "instrument", "scope_value": "rb"}}
        self.control = {"cause_id": "cause", "state": "PENDING", "row_version": 1,
                        "attempts": 0, "mode": "STOP_NEW", "scope_kind": "instrument",
                        "scope_value": "SHFE:rb", "latch_id": "latch", "last_outcome": None,
                        "last_code": None, "gw_version": None}
        self.cancel = {"operation_id": "operation", "state": "WAITING_CONTROL", "row_version": 1,
                       "attempts": 0, "scope_kind": "instrument", "scope_value": "SHFE:rb",
                       "latch_id": "latch", "wait_cause_id": "cause", "last_outcome": None,
                       "last_code": None}
        self.occupancy = {"unresolved-order": "held"}
        self.audits = []

    def latest_policy(self):
        return None

    def control_intent(self, key):
        return deepcopy(self.control) if key == "cause" else None

    def cancel_intent(self, key):
        return deepcopy(self.cancel) if key == "operation" else None

    def latch(self, key):
        return deepcopy(self.latches.get(key))

    def permits_in(self, states):
        assert states == ("CLAIMED", "UNKNOWN")
        return []

    def _update(self, row, expected, fields):
        if row["row_version"] != expected:
            return False
        row.update(fields)
        row["row_version"] += 1
        return True

    def update_control_intent(self, key, expected, **fields):
        assert key == "cause"
        return self._update(self.control, expected, fields)

    def update_cancel_intent(self, key, expected, **fields):
        assert key == "operation"
        return self._update(self.cancel, expected, fields)

    def audit(self, kind, **fields):
        self.audits.append((kind, fields))


class FakeLedger:
    def __init__(self, txn):
        self.txn = txn

    @contextmanager
    def transaction(self):
        before = deepcopy(self.txn.__dict__)
        try:
            yield self.txn
        except BaseException:
            self.txn.__dict__ = before
            raise


class ForbiddenRuntime:
    def __init__(self):
        self.calls = []

    def status(self):
        self.calls.append("status")
        pytest.fail("instrument scope must not access runtime")

    def call(self, *args, **kwargs):
        self.calls.append("rpc")
        pytest.fail("instrument scope must not send RPC")


def make_engine():
    txn, runtime = FakeTxn(), ForbiddenRuntime()
    adapter = GatewayControlAdapter(runtime, mount="direct", service_id="gateway",
                                    instance_id="instance", scope_ref="scope",
                                    account_ref="account", timeout_seconds=1)
    engine = object.__new__(PretradeEngine)
    engine._ledger, engine._control = FakeLedger(txn), adapter
    engine._clock, engine._active = lambda: 123, None
    return engine, txn, runtime


def test_rejected_instrument_control_keeps_latch_and_waiting_cancel_is_superseded():
    engine, txn, runtime = make_engine()
    latch, occupancy = deepcopy(txn.latches), deepcopy(txn.occupancy)
    engine._apply_control("cause")
    assert txn.control["state"] == "REJECTED"
    assert txn.control["last_outcome"] == "REQUEST_DENIED"
    assert txn.control["last_code"] == "RISK_CONTROL_SCOPE_UNSUPPORTED"
    assert txn.cancel["state"] == "WAITING_CONTROL"
    engine._retry_intents()  # Historical retry intentionally does not scan waiting work.
    assert txn.cancel["state"] == "WAITING_CONTROL"
    engine._apply_cancel("operation")  # Explicit invocation rechecks the rejected control.
    assert txn.cancel["state"] == "SUPERSEDED"
    assert txn.cancel["attempts"] == 0
    assert txn.latches == latch and txn.occupancy == occupancy
    assert runtime.calls == []


def test_direct_instrument_cancel_is_rejected_and_keeps_latch():
    engine, txn, runtime = make_engine()
    txn.cancel.update(state="PENDING", wait_cause_id=None)
    latch, occupancy = deepcopy(txn.latches), deepcopy(txn.occupancy)
    engine._apply_cancel("operation")
    assert txn.cancel["state"] == "REJECTED"
    assert txn.cancel["last_outcome"] == "REQUEST_DENIED"
    assert txn.cancel["last_code"] == "RISK_CONTROL_SCOPE_UNSUPPORTED"
    assert txn.latches == latch and txn.occupancy == occupancy
    assert runtime.calls == []


@pytest.mark.parametrize("latch_active", [True, False])
def test_existing_unknown_intents_are_neither_resent_nor_cleared(monkeypatch, latch_active):
    engine, txn, runtime = make_engine()
    txn.control.update(state="UNKNOWN", attempts=1)
    txn.cancel.update(state="UNKNOWN", attempts=1, wait_cause_id=None)
    txn.latches["latch"]["active"] = latch_active
    before = deepcopy(txn.__dict__)
    monkeypatch.setattr(engine_module, "_v1_source_quarantined", lambda: True)
    for _ in range(2):
        engine._apply_control("cause")
        engine._apply_control("cause", query_first=True)
        engine._apply_cancel("operation")
        assert engine.maintain()["intent_retry_unavailable"] == 1
        assert engine.recover()["intent_retry_unavailable"] == 1
    assert txn.__dict__ == before
    assert runtime.calls == []


@pytest.fixture
def memory_engine():
    """Use a single checked memory DB; replace only the external fence owner."""
    from bt_api_risk.core.admission import AccountScope
    from bt_api_risk.pretrade import ledger as ledger_module
    from bt_api_risk.pretrade.ledger import PretradeLedger
    from bt_api_risk.risk_order_journal import RiskOrderJournal
    from bt_api_risk.risk_order_journal_contracts import RiskOrderWriterFence

    class MemoryConnection(sqlite3.Connection):
        def close(self):
            pass  # The fake journal borrows the same isolated connection per transaction.

    db = sqlite3.connect(":memory:", isolation_level=None, factory=MemoryConnection)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA temp_store=MEMORY")
        assert db.execute("PRAGMA temp_store").fetchone()[0] == 2
        assert db.execute("PRAGMA journal_mode=MEMORY").fetchone()[0] == "memory"
        assert all(row[2] == "" for row in db.execute("PRAGMA database_list").fetchall())
        scope = AccountScope("synthetic", "account-a", "simulation")
        db.execute("CREATE TABLE risk_order_fences(scope_key TEXT PRIMARY KEY, fencing_token INTEGER)")
        db.execute("INSERT INTO risk_order_fences VALUES(?, ?)", (scope.key, 1))
        for statement in ledger_module._split_statements(ledger_module._SCHEMA):
            db.execute(statement)
        db.execute("INSERT INTO risk_pretrade_meta VALUES (?)", (ledger_module.SCHEMA_VERSION,))
        db.execute("INSERT INTO risk_pretrade_source_guard VALUES (1, 'SOURCE_UNVERIFIED')")
        fence = RiskOrderWriterFence(scope, "owner", 1, "a" * 64)
        journal = object.__new__(RiskOrderJournal)
        journal._owner_id, journal._connection = "owner", lambda **_: db
        journal._assert_current = lambda _: None  # No OS fence or external owner.
        ledger = object.__new__(PretradeLedger)
        ledger._journal, ledger._fence = journal, fence
        ledger._pid, ledger._thread = os.getpid(), threading.current_thread()
        ledger._v2_clock_ns, ledger._schema_version = None, 3
        engine, _, runtime = make_engine()
        engine._ledger = ledger
        with ledger.transaction() as txn:
            latch, created = txn.ensure_latch(scope_kind="instrument", scope_value="rb", control="counters",
                                               code="risk_order_count_limit", reason="synthetic",
                                               trading_day="20300101", now_ns=1)
            assert created
            latch_id = latch["latch_id"]
            assert txn.ensure_control_intent(cause_id="cause", mode="STOP_NEW", scope_kind="instrument",
                                             scope_value="SHFE:rb", latch_id=latch_id, rule="synthetic", now_ns=1)
            assert txn.ensure_cancel_intent(operation_id="operation", source="synthetic", scope_kind="instrument",
                                            scope_value="SHFE:rb", latch_id=latch_id,
                                            wait_cause_id="cause", now_ns=1)
        yield engine, ledger, runtime, latch_id
        assert all(row[2] == "" for row in db.execute("PRAGMA database_list").fetchall())
        assert db.execute("PRAGMA temp_store").fetchone()[0] == 2
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    finally:
        sqlite3.Connection.close(db)


def test_memory_ledger_rejection_keeps_latch_and_explicit_cancel_supersedes(memory_engine):
    engine, ledger, runtime, latch_id = memory_engine
    engine._apply_control("cause")
    with ledger.transaction() as txn:
        assert txn.control_intent("cause")["state"] == "REJECTED"
        assert txn.cancel_intent("operation")["state"] == "WAITING_CONTROL"
        assert txn.latch(latch_id)["active"] == 1
    engine._apply_cancel("operation")
    with ledger.transaction() as txn:
        assert txn.control_intent("cause")["last_code"] == "RISK_CONTROL_SCOPE_UNSUPPORTED"
        assert txn.cancel_intent("operation")["state"] == "SUPERSEDED"
        assert txn.latch(latch_id)["active"] == 1
    assert runtime.calls == []


def test_memory_ledger_existing_unknowns_survive_maintenance(memory_engine, monkeypatch):
    engine, ledger, runtime, latch_id = memory_engine
    with ledger.transaction() as txn:
        assert txn.update_control_intent("cause", 1, state="UNKNOWN", attempts=1, updated_ns=2)
        assert txn.update_cancel_intent("operation", 1, state="UNKNOWN", attempts=1, updated_ns=2)
        before = tuple(dict(row) for row in (txn.control_intent("cause"),
                                              txn.cancel_intent("operation"), txn.latch(latch_id)))
    monkeypatch.setattr(engine_module, "_v1_source_quarantined", lambda: True)
    engine._apply_control("cause")
    engine._apply_cancel("operation")
    assert engine.maintain()["intent_retry_unavailable"] == 1
    assert engine.recover()["intent_retry_unavailable"] == 1
    with ledger.transaction() as txn:
        after = tuple(dict(row) for row in (txn.control_intent("cause"),
                                             txn.cancel_intent("operation"), txn.latch(latch_id)))
    assert after == before
    assert runtime.calls == []
