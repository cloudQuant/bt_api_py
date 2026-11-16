"""Every admin refusal is appended once with bounded, non-payload detail.

UNIT/FAKE: a real in-memory Risk ledger plus the SDK admin entry. No service,
network, account or provider is contacted. Refusals must stay refusals.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from bt_api_base.risk_service_protocol import RISK_ADMIN_OPERATIONS
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_risk.core.admission import AccountScope
from bt_api_risk.pretrade import CallerIdentity, RiskAdminError, ledger
from bt_api_risk.pretrade.engine import PretradeEngine

from bt_api_py.risk_service_component import RiskOrderService
from bt_api_py.risk_service_config import RiskPrincipal
from bt_api_py.service_registry import ServiceCallContext

SCOPE = AccountScope("synthetic", "account-a", "simulation")
OPERATOR = "operator-a"
ACTOR = "operator-actor"


class MemoryLedger:
    def __init__(self, txn):
        self.txn = txn

    @contextmanager
    def transaction(self):
        yield self.txn


@contextmanager
def setup():
    connection = sqlite3.connect(":memory:")
    try:
        connection.row_factory = sqlite3.Row
        for statement in ledger._split_statements(ledger._SCHEMA):
            connection.execute(statement)
        fence = SimpleNamespace(scope=SCOPE, owner_id="synthetic", fencing_token=1)
        args = (connection, SCOPE.key, fence, None)
        if "schema_version" in inspect.signature(ledger.LedgerTxn).parameters:
            args += (3,)
        txn = ledger.LedgerTxn(*args)
        engine = object.__new__(PretradeEngine)
        engine._ledger = MemoryLedger(txn)
        engine._clock = lambda: 100
        engine._active = None
        engine._store = SimpleNamespace(load_error=None)
        engine._fact = lambda name, expected: None
        principal = RiskPrincipal(
            OPERATOR, "operator", ACTOR, frozenset(), frozenset(RISK_ADMIN_OPERATIONS), b"synthetic",
        )
        service = RiskOrderService(
            engine, engine._ledger, account_scope="account-a", route="SYNTHETIC",
            execution_domain_ref="domain-a", principals={OPERATOR: principal},
            instrument_for=lambda *_: None, ready=lambda: True,
        )
        yield engine, service, txn, connection
    finally:
        connection.rollback()
        connection.close()


def context(principal=OPERATOR, actor=ACTOR):
    return ServiceCallContext(
        WireIdentity(WireRole.APPLICATION_CLIENT, principal, actor),
        WireScope("domain-a", account_ref="account-a"), "registration", principal,
    )


def operator_caller():
    return CallerIdentity(OPERATOR, ACTOR, "operator", frozenset(), None,
                          frozenset(RISK_ADMIN_OPERATIONS))


def admin_rows(txn):
    return [
        {
            "seq": row["seq"],
            "kind": row["kind"],
            "code": row["code"],
            "detail": json.loads(row["detail_json"]),
        }
        for row in txn._all(
            "SELECT seq, kind, code, detail_json FROM risk_pretrade_audit "
            "WHERE kind='admin_refused' ORDER BY seq",
            (),
        )
    ]


def test_engine_operator_refusal_is_appended_once_with_bounded_detail():
    with setup() as (engine, _, txn, _):
        with pytest.raises(RiskAdminError) as first:
            engine.release_latch(None, "latch-1", 1, "reason")
        assert first.value.code == "risk_unauthorized"
        assert first.value.audited is True
        with pytest.raises(RiskAdminError):
            engine.release_latch(None, "latch-1", 1, "reason")
        rows = admin_rows(txn)
        assert [row["code"] for row in rows] == ["risk_unauthorized", "risk_unauthorized"]
        # Monotonic append-only sequence.
        assert rows[0]["seq"] < rows[1]["seq"]
        assert rows[0]["detail"] == {
            "operation": "risk.latch.release", "scope_kind": "account", "scope_value": "",
        }
        # No payload, credential or price detail.
        assert set(rows[0]["detail"]) <= {
            "operation", "principal_ref", "actor_ref", "scope_kind", "scope_value",
        }


def test_direct_non_operator_identity_is_referenced_without_payload():
    with setup() as (engine, _, txn, _):
        caller = CallerIdentity("principal-a", "actor-a", "strategy", frozenset(), None, frozenset())
        with pytest.raises(RiskAdminError):
            engine.release_latch(caller, "latch-1", 1, "reason")
        (row,) = admin_rows(txn)
        assert row["detail"] == {
            "operation": "risk.latch.release",
            "principal_ref": "principal-a",
            "actor_ref": "actor-a",
            "scope_kind": "account",
            "scope_value": "",
        }


def test_oversized_operator_fields_are_dropped_not_written():
    with setup() as (engine, _, txn, _):
        bogus = SimpleNamespace(principal_ref="p" * 10_000, actor_ref="a" * 10_000)
        with pytest.raises(RiskAdminError):
            engine.release_latch(bogus, "latch-1", 1, "reason")
        (row,) = admin_rows(txn)
        assert row["detail"] == {
            "operation": "risk.latch.release", "scope_kind": "account", "scope_value": "",
        }


def test_sdk_admin_validation_refusal_is_appended_and_changes_nothing():
    with setup() as (_, service, txn, connection):
        reply = service.handle("risk.policy.publish", context=context(), arguments={
            "expected_version": True, "document": {}, "reason": "", "exchange_rules_text": None,
        })
        assert reply == {"status": "REQUEST_REJECTED", "code": "risk_request_invalid"}
        (row,) = admin_rows(txn)
        assert row["detail"] == {
            "operation": "risk.policy.publish",
            "principal_ref": OPERATOR,
            "actor_ref": ACTOR,
            "scope_kind": "account",
            "scope_value": "",
        }
        assert row["code"] == "risk_request_invalid"
        # A refusal writes only the audit row: no policy, permit or intent state.
        for table in ("risk_pretrade_policy_versions", "risk_pretrade_permits",
                      "risk_pretrade_cancel_intents", "risk_pretrade_latches"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_unauthorized_admin_attempt_is_appended_with_attempted_identity():
    with setup() as (_, service, txn, _):
        reply = service.handle("risk.admin.status", context=context("intruder", "intruder-actor"),
                               arguments={})
        assert reply == {"status": "REQUEST_REJECTED", "code": "risk_unauthorized"}
        (row,) = admin_rows(txn)
        assert row["code"] == "risk_unauthorized"
        assert row["detail"]["principal_ref"] == "intruder"
        assert row["detail"]["actor_ref"] == "intruder-actor"
        assert row["detail"]["operation"] == "risk.admin.status"


def test_operator_refusal_counter_is_exposed_on_admin_status():
    with setup() as (engine, service, _, _):
        engine.status_summary = lambda: {"available": False}
        assert service.handle("risk.policy.publish", context=context(), arguments={
            "expected_version": True, "document": {}, "reason": "", "exchange_rules_text": None,
        })["code"] == "risk_request_invalid"
        assert service.handle("risk.admin.status", context=context("intruder", "intruder-actor"),
                              arguments={})["code"] == "risk_unauthorized"
        status = service.handle("risk.admin.status", context=context(), arguments={})
        assert status["status"] == "OBSERVED"
        assert status["summary"]["operator_refusal_counts"] == {
            "risk_request_invalid": 1, "risk_unauthorized": 1,
        }
        # Reporting the counter is not itself a refusal.
        assert status["summary"]["operator_refusal_counts"] == {
            "risk_request_invalid": 1, "risk_unauthorized": 1,
        }


def test_cancel_refusal_records_the_requested_scope_not_the_account_default():
    with setup() as (_, service, txn, _):
        reply = service.handle("risk.cancel.request", context=context(), arguments={
            "operation_id": "operation-a", "scope_kind": "strategy", "scope_value": "",
        })
        assert reply == {"status": "REQUEST_REJECTED", "code": "risk_request_invalid"}
        (row,) = admin_rows(txn)
        assert row["detail"]["operation"] == "risk.cancel.request"
        assert row["detail"]["scope_kind"] == "strategy"
        assert row["detail"]["scope_value"] == ""


def test_engine_audited_refusal_is_not_duplicated_by_the_sdk_boundary():
    with setup() as (engine, _, txn, _):
        narrowed = CallerIdentity(OPERATOR, ACTOR, "operator", frozenset(), None,
                                  frozenset({"risk.admin.status"}))
        with pytest.raises(RiskAdminError) as refused:
            engine.publish_policy(narrowed, {}, 0, "")
        assert refused.value.audited is True
        (row,) = admin_rows(txn)
        assert row["code"] == "risk_unauthorized"
        assert row["detail"]["principal_ref"] == OPERATOR
