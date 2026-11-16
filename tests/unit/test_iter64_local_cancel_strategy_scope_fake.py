"""The local operator cancel path is strategy-only and records dispositions.

UNIT/FAKE: a real in-memory Risk ledger and engine; no Gateway, account or
network. A local UNKNOWN is never reported as a successful cancel.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from bt_api_risk.core.admission import AccountScope
from bt_api_risk.pretrade import CallerIdentity, RiskAdminError, ledger
from bt_api_risk.pretrade.engine import PretradeEngine

SCOPE = AccountScope("synthetic", "account-a", "simulation")
OPERATOR = "operator-a"
ACTOR = "operator-actor"
GRANTED = "strategy-a"


class MemoryLedger:
    def __init__(self, txn):
        self.txn = txn

    @contextmanager
    def transaction(self, **_kwargs):
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
        yield engine, txn, connection
    finally:
        connection.rollback()
        connection.close()


def caller(*, strategies=frozenset({GRANTED}), account=False, instruments=frozenset()):
    return CallerIdentity(OPERATOR, ACTOR, "operator", frozenset(), None,
                          frozenset({"risk.cancel.request"}), account,
                          frozenset(strategies), frozenset(instruments))


def insert_permit(connection, permit_id, request_key, *, strategy=GRANTED,
                  state="RESERVED", instrument="SHFE:rb", created_ns=1):
    connection.execute(
        "INSERT INTO risk_pretrade_permits VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            SCOPE.key, permit_id, strategy, request_key, None, "principal", "execution",
            "{}", "0" * 64, "{}", "0" * 64, instrument, "{}", "BUY", "OPEN", "INCREASE",
            "NET", "LONG", "1", None, None, None, "20300101", 1, state, 1, None, None,
            None, None, None, None, None, None, None, "0", "0", created_ns, None, None,
            created_ns,
        ),
    )


def local_cancel(engine, scope_kind="strategy", scope_value=GRANTED, *, who=None):
    return engine._accept_operator_cancel_local(
        who if who is not None else caller(), "operation-a", scope_kind, scope_value,
        _before_commit=lambda: None,
    )


def entries(txn, kind):
    return [
        json.loads(row["detail_json"])
        for row in txn._all(
            "SELECT detail_json FROM risk_pretrade_audit WHERE kind=? ORDER BY seq", (kind,)
        )
    ]


def test_only_a_granted_strategy_scope_may_use_the_local_path():
    with setup() as (engine, txn, connection):
        for kind, value in (("account", ""), ("instrument", "SHFE:rb")):
            with pytest.raises(RiskAdminError) as refused:
                local_cancel(engine, kind, value)
            assert refused.value.code == "risk_cancel_local_scope_unsupported"
            assert refused.value.audited is False
        # Refusals changed no cancel state and appended no disposition.
        assert connection.execute(
            "SELECT count(*) FROM risk_pretrade_cancel_intents").fetchone()[0] == 0
        assert entries(txn, "cancel_local_disposition") == []


def test_strategy_scope_without_an_explicit_grant_is_unauthorized():
    with setup() as (engine, txn, _):
        with pytest.raises(RiskAdminError) as refused:
            local_cancel(engine, who=caller(strategies=frozenset()))
        assert refused.value.code == "risk_unauthorized"
        assert entries(txn, "cancel_local_disposition") == []


def test_strategy_local_cancel_stays_unknown_and_records_dispositions():
    with setup() as (engine, txn, connection):
        insert_permit(connection, "p1", "k1", state="RESERVED")
        insert_permit(connection, "p2", "k2", state="UNKNOWN", created_ns=2)
        insert_permit(connection, "p3", "k3", state="TERMINAL", instrument="SHFE:cu", created_ns=3)
        insert_permit(connection, "p4", "k4", strategy="strategy-b", created_ns=4)
        reply = local_cancel(engine)
        # A local UNKNOWN is not a cancel success.
        assert reply == {"operation_id": "operation-a", "state": "UNKNOWN"}
        (detail,) = entries(txn, "cancel_local_disposition")
        assert detail["state"] == "UNKNOWN"
        assert detail["sent"] == 0
        assert detail["scope_kind"] == "strategy"
        assert detail["scope_value"] == GRANTED
        assert detail["truncated"] is False
        assert detail["disposition_counts"] == {
            "not_sent": 1, "accepted": 0, "terminal": 1, "unknown": 1,
        }
        targets = {target["request_key"]: target["disposition"] for target in detail["targets"]}
        assert targets == {"k1": "NOT_SENT", "k2": "UNKNOWN", "k3": "TERMINAL"}
        assert "k4" not in targets
        # The intent itself remains UNKNOWN with a finite local code.
        row = connection.execute(
            "SELECT state, last_code FROM risk_pretrade_cancel_intents").fetchone()
        assert tuple(row) == ("UNKNOWN", "risk_admin_cancel_local_only")


def test_disposition_record_is_bounded_and_marks_truncation():
    with setup() as (engine, txn, connection):
        for index in range(40):
            insert_permit(connection, f"p{index}", f"k{index}", created_ns=index)
        local_cancel(engine)
        (detail,) = entries(txn, "cancel_local_disposition")
        assert detail["truncated"] is True
        assert len(detail["targets"]) == 32
        assert detail["disposition_counts"]["not_sent"] == 32
        # The whole audit detail stays a small bounded JSON object.
        assert len(json.dumps(detail)) < 8192


def test_duplicate_retry_does_not_append_a_second_disposition():
    with setup() as (engine, txn, _):
        assert local_cancel(engine) == {"operation_id": "operation-a", "state": "UNKNOWN"}
        assert local_cancel(engine) == {"operation_id": "operation-a", "state": "UNKNOWN"}
        assert len(entries(txn, "cancel_local_disposition")) == 1
