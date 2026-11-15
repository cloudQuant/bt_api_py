"""Authenticated audit reads with an in-memory ledger and no host or network."""

import asyncio
import inspect
import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_risk.core.admission import AccountScope
from bt_api_risk.pretrade import ledger
from bt_api_risk.pretrade.engine import PretradeEngine
from bt_api_risk.web.app import _check_audit, _parse_audit, _RefusalError

from bt_api_py.risk_service_component import RiskOrderService


class MemoryLedger:
    def __init__(self, connection, scope):
        self.connection = connection
        self.scope = scope
        self.fail_audit = False

    @contextmanager
    def transaction(self):
        self.connection.execute("SAVEPOINT audit_test")
        try:
            fence = SimpleNamespace(scope=self.scope, owner_id="unit", fencing_token=1)
            args = (self.connection, self.scope.key, fence, None)
            if "schema_version" in inspect.signature(ledger.LedgerTxn).parameters:
                args += (3,)
            txn = ledger.LedgerTxn(*args)
            if self.fail_audit:
                def fail(*args, **kwargs):
                    raise sqlite3.OperationalError("synthetic audit write failure")
                txn.audit = fail
            yield txn
            self.connection.execute("RELEASE SAVEPOINT audit_test")
        except BaseException:
            self.connection.execute("ROLLBACK TO SAVEPOINT audit_test")
            self.connection.execute("RELEASE SAVEPOINT audit_test")
            raise


@pytest.fixture
def environment():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA temp_store=MEMORY")
    assert connection.execute("PRAGMA temp_store").fetchone()[0] == 2
    assert connection.execute("PRAGMA journal_mode=MEMORY").fetchone()[0] == "memory"
    assert next(row[2] for row in connection.execute("PRAGMA database_list") if row[1] == "main") == ""
    connection.row_factory = sqlite3.Row
    for statement in ledger._split_statements(ledger._SCHEMA):
        connection.execute(statement)
    connection.commit()
    scope = AccountScope("fixture", "account", "simulation")
    store = MemoryLedger(connection, scope)
    engine = PretradeEngine.__new__(PretradeEngine)
    engine._ledger = store
    engine._clock = lambda: 42
    principal = SimpleNamespace(principal_ref="operator", actor_ref="actor", role="operator",
                                operations=frozenset({"risk.audit.read"}))
    other = SimpleNamespace(principal_ref="other", actor_ref="other-actor", role="operator",
                            operations=frozenset({"risk.audit.read"}))
    service = RiskOrderService(engine, store, account_scope="account", route="fixture",
                               execution_domain_ref="domain", principals={"operator": principal, "other": other},
                               instrument_for=lambda *_: None, ready=lambda: True)
    context = SimpleNamespace(identity=WireIdentity(WireRole.APPLICATION_CLIENT, "operator", "actor"),
                              scope=WireScope("domain", account_ref="account"),
                              principal_ref="operator", result_fits=lambda _: True)
    try:
        yield connection, store, service, context
    finally:
        connection.close()


def request(service, context, after=0, limit=1, through=None, snapshot=None):
    return service.handle("risk.audit.read", context=context, arguments={
        "after": after, "limit": limit, "through": through, "snapshot": snapshot,
    })


def append(store, kind):
    with store.transaction() as txn:
        txn.audit(kind, at_ns=1, detail={})


def test_real_handler_uses_fixed_snapshot_and_audits_every_page(environment):
    connection, store, service, context = environment
    for kind in ("one", "two", "three"):
        append(store, kind)
    first = request(service, context)
    assert first["status"] == "OBSERVED" and first["has_more"]
    assert [row["kind"] for row in first["records"]] == ["one"]
    assert first["through"] == 3 and first["snapshot"] == 4
    append(store, "later")
    second = request(service, context, first["next_after"], 1, first["through"], first["snapshot"])
    third = request(service, context, second["next_after"], 1, first["through"], first["snapshot"])
    assert [row["kind"] for row in second["records"] + third["records"]] == ["two", "three"]
    assert second["has_more"] and not third["has_more"]
    access = connection.execute("SELECT kind, detail_json FROM risk_pretrade_audit "
                                "WHERE kind LIKE 'admin_audit_read_%' ORDER BY seq").fetchall()
    assert [row[0] for row in access] == ["admin_audit_read_snapshot", "admin_audit_read_page",
                                          "admin_audit_read_page"]
    assert all("operator" not in json.loads(row[1]).values() for row in access)
    assert all("actor" not in json.loads(row[1]).values() for row in access)
    next_scan = request(service, context, first["through"], 20)
    assert {row["kind"] for row in next_scan["records"]} >= {"admin_audit_read_snapshot", "later"}


def test_snapshot_scope_identity_and_bound_cannot_be_forged(environment):
    connection, store, service, context = environment
    append(store, "one")
    first = request(service, context)
    tampered = request(service, context, first["next_after"], 1, first["through"] + 1, first["snapshot"])
    assert tampered == {"status": "REQUEST_REJECTED", "code": "risk_request_invalid"}
    missing = request(service, context, first["next_after"], 1, first["through"], first["snapshot"] + 1)
    assert missing == {"status": "REQUEST_REJECTED", "code": "risk_request_invalid"}
    other_context = SimpleNamespace(**vars(context))
    other_context.identity = WireIdentity(WireRole.APPLICATION_CLIENT, "other", "other-actor")
    other_context.principal_ref = "other"
    assert request(service, other_context, first["next_after"], 1, first["through"],
                   first["snapshot"])["code"] == "risk_request_invalid"
    denied_context = SimpleNamespace(**vars(context))
    denied_context.scope = WireScope("domain", account_ref="other")
    assert request(service, denied_context)["code"] == "risk_unauthorized"
    before = connection.execute("SELECT COUNT(*) FROM risk_pretrade_audit").fetchone()[0]
    assert request(service, context, 0, 1, None, first["snapshot"])["code"] == "risk_request_invalid"
    assert connection.execute("SELECT COUNT(*) FROM risk_pretrade_audit").fetchone()[0] == before + 1
    rejected = connection.execute("SELECT kind, detail_json FROM risk_pretrade_audit ORDER BY seq DESC LIMIT 1").fetchone()
    assert rejected[0] == "admin_audit_read_rejected"
    assert set(json.loads(rejected[1])) == {"operator_digest"}


def test_audit_write_failure_rolls_back_and_cannot_report_success(environment):
    connection, store, service, context = environment
    append(store, "one")
    store.fail_audit = True
    result = request(service, context)
    assert result == {"status": "REQUEST_REJECTED", "code": "risk_audit_unavailable"}
    assert connection.execute("SELECT COUNT(*) FROM risk_pretrade_audit").fetchone()[0] == 1


def test_response_budget_failure_does_not_expose_page_but_records_access(environment):
    connection, store, service, context = environment
    append(store, "one")
    context.result_fits = lambda _: False
    assert request(service, context)["code"] == "risk_audit_record_too_large"
    assert connection.execute("SELECT COUNT(*) FROM risk_pretrade_audit WHERE kind=?",
                              ("admin_audit_read_snapshot",)).fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM risk_pretrade_audit WHERE kind=?",
                              ("admin_audit_read_rejected",)).fetchone()[0] == 1


def test_other_account_snapshot_cannot_be_reused(environment):
    connection, store, service, context = environment
    append(store, "own")
    other_scope = AccountScope("fixture", "other-account", "simulation")
    other_store = MemoryLedger(connection, other_scope)
    other_engine = PretradeEngine.__new__(PretradeEngine)
    other_engine._ledger, other_engine._clock = other_store, lambda: 42
    other_service = RiskOrderService(other_engine, other_store, account_scope="other-account", route="fixture",
                                     execution_domain_ref="domain", principals=service._principals,
                                     instrument_for=lambda *_: None, ready=lambda: True)
    other_context = SimpleNamespace(**vars(context))
    other_context.scope = WireScope("domain", account_ref="other-account")
    append(other_store, "other")
    foreign = request(other_service, other_context)
    result = request(service, context, 0, 1, foreign["through"], foreign["snapshot"])
    assert result == {"status": "REQUEST_REJECTED", "code": "risk_request_invalid"}


def test_malformed_foreign_scope_tail_cannot_be_hidden_by_snapshot(environment):
    connection, store, service, context = environment
    append(store, "own")
    connection.execute("INSERT INTO risk_pretrade_audit "
                       "(scope_key,at_ns,kind,detail_json,writer_owner_id,writer_fencing_token) "
                       "VALUES (?,?,?,?,?,?)", ("{invalid-json", 1, "foreign", "{}", "unit", 1))
    connection.commit()
    result = request(service, context, limit=10)
    assert result == {"status": "REQUEST_REJECTED", "code": "risk_audit_corrupt"}
    assert connection.execute("SELECT COUNT(*) FROM risk_pretrade_audit WHERE kind=?",
                              ("admin_audit_read_rejected",)).fetchone()[0] == 1


def test_valid_foreign_scope_tail_is_scanned_without_leaking_and_terminates(environment):
    connection, store, service, context = environment
    append(store, "own")
    foreign = MemoryLedger(connection, AccountScope("fixture", "other-account", "simulation"))
    append(foreign, "foreign")
    first = request(service, context, limit=1)
    assert first["through"] == 2 and first["has_more"]
    assert [row["kind"] for row in first["records"]] == ["own"]
    final = request(service, context, first["next_after"], 1, first["through"], first["snapshot"])
    assert final["records"] == [] and not final["has_more"]
    assert final["next_after"] == first["through"] == 2


def test_foreign_tail_polling_advances_past_fully_scanned_rows(environment, monkeypatch):
    connection, store, service, context = environment
    monkeypatch.setattr(ledger, "_AUDIT_SCAN_ROWS", 4)
    append(store, "own")
    foreign = MemoryLedger(connection, AccountScope("fixture", "other-account", "simulation"))
    for _ in range(3):
        append(foreign, "foreign")
    first = request(service, context, limit=1)
    assert first["has_more"] and first["next_after"] == 1 and first["through"] == 4
    final = request(service, context, first["next_after"], 1, first["through"], first["snapshot"])
    assert not final["has_more"] and final["records"] == [] and final["next_after"] == 4
    cursor = final["next_after"]
    for _ in range(8):
        page = request(service, context, after=cursor, limit=10)
        assert page["status"] == "OBSERVED" and not page["has_more"]
        assert page["next_after"] >= cursor
        assert all(row["seq"] > cursor and row["kind"].startswith("admin_audit_read_")
                   for row in page["records"])
        cursor = page["next_after"]


def test_corrupt_record_rejects_and_audits_rejection(environment):
    connection, store, service, context = environment
    with store.transaction() as txn:
        txn._c.execute("INSERT INTO risk_pretrade_audit "
                       "(scope_key,at_ns,kind,detail_json,writer_owner_id,writer_fencing_token) "
                       "VALUES (?,?,?,?,?,?)", (txn._scope, 1, "corrupt", "{", "unit", 1))
    assert request(service, context)["code"] == "risk_audit_corrupt"
    assert connection.execute("SELECT COUNT(*) FROM risk_pretrade_audit WHERE kind=?",
                              ("admin_audit_read_rejected",)).fetchone()[0] == 1


def test_authorized_invalid_arguments_are_audited_without_payload(environment):
    connection, _, service, context = environment
    result = service.handle("risk.audit.read", context=context, arguments={"sensitive": "not-logged"})
    assert result == {"status": "REQUEST_REJECTED", "code": "risk_request_invalid"}
    row = connection.execute("SELECT kind, detail_json FROM risk_pretrade_audit").fetchone()
    assert row[0] == "admin_audit_read_rejected"
    assert "not-logged" not in row[1]


def test_web_parser_and_reply_validate_complete_snapshot_fields(environment):
    _, store, service, context = environment
    append(store, "one")
    req = SimpleNamespace(url=SimpleNamespace(query="after=0&limit=1"))
    arguments = asyncio.run(_parse_audit(req))
    result = service.handle("risk.audit.read", context=context, arguments=arguments)
    assert _check_audit(result, arguments) == 200
    bad = dict(result, next_after=result["next_after"] + 1)
    with pytest.raises(ValueError):
        _check_audit(bad, arguments)
    req.url.query = f"after=0&limit=1&through={result['through']}&snapshot={result['snapshot']}"
    assert asyncio.run(_parse_audit(req))["snapshot"] == result["snapshot"]
    req.url.query = "after=0&through=1"
    with pytest.raises(_RefusalError):
        asyncio.run(_parse_audit(req))
