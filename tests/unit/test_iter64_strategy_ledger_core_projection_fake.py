"""The SDK strategy port uses Risk-owned journal reads without a real database."""
from types import SimpleNamespace

import pytest

from bt_api_base.service_protocol import WireScope
from bt_api_risk import AccountScope
from bt_api_risk.risk_order_journal import RiskOrderJournal as CoreJournal
from bt_api_py.service_risk_order_journal import RiskOrderJournal as SDKJournal
from bt_api_py.service_strategy_ledger_host import _RiskJournalPort, ServiceStrategyLedgerError


class FakeConnection:
    def __init__(self, state):
        self.state = state
        self.closed = False
        state["opened"].append(self)

    def execute(self, sql, args):
        if sql.startswith("SELECT record_sha256"):
            row = {"record_sha256": self.state["first"]} if self.state["first"] is not None else None
            return SimpleNamespace(fetchone=lambda: row)
        if sql.startswith("SELECT 1 FROM risk_order_commands"):
            row = 1 if args == (self.state["scope"].key, "present") else None
            return SimpleNamespace(fetchone=lambda: row)
        if sql.startswith("SELECT * FROM risk_order_events"):
            return SimpleNamespace(fetchall=lambda: [self.state["event"]])
        raise AssertionError(sql)

    def close(self):
        self.closed = True


def test_sdk_inherits_core_reads_and_port_detects_replaced_identity():
    methods = ("strategy_ledger_journal_identity", "strategy_ledger_events_after", "has_intent_id")
    for name in methods:
        assert name not in SDKJournal.__dict__
        assert getattr(SDKJournal, name) is getattr(CoreJournal, name)

    scope = AccountScope("fixture", "account", "sandbox")
    state = {"scope": scope, "first": "a" * 64, "opened": [], "event": {
        "principal_ref": "p", "intent_id": "present", "revision": 1,
        "event_type": "PREPARED", "payload_json": "{}",
        "previous_account_sha256": "b" * 64, "previous_command_sha256": "c" * 64,
        "record_sha256": "a" * 64, "writer_owner_id": "owner",
        "writer_fencing_token": 1, "writer_fence_sha256": "d" * 64,
    }}
    journal = object.__new__(SDKJournal)
    journal._connection = lambda: FakeConnection(state)
    wire = WireScope("domain", account_ref="account", strategy_ref="strategy")
    target = SimpleNamespace(account_scope=scope, strategy_id="strategy")
    port = _RiskJournalPort(journal, {wire: target})

    assert port.source() == "risk-journal:" + "a" * 64
    assert port.has("present") is True
    assert port.has("absent") is False
    assert port.read(0, 10) == [(1, None)]
    state["first"] = "f" * 64
    with pytest.raises(ServiceStrategyLedgerError, match="LEDGER_JOURNAL_SOURCE_CHANGED"):
        port.read(0, 10)
    assert state["opened"] and all(conn.closed for conn in state["opened"])
