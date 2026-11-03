"""Iteration 60 G2: ``GatewayAccountPositionStream(reconciliation_cut=True)``.

Only the Gateway's full-account ``account.reconciliation_cut`` evidence may make
the stream's report complete, and only after a healthy at-head journal view
poll. A trimmed strategy view alone stays diagnostic. UNIT/FAKE: the consumer
and runtime are local doubles; the wire path is covered by
``test_iter60_gateway_reconciliation_cut.py`` (INTEGRATION_SIM).
"""

from __future__ import annotations

import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bt_api_py.account_position_stream import (
    AccountPositionStreamError,
    GatewayAccountPositionStream,
)
from bt_api_py.strategy_ledger import (
    InstrumentTerms,
    LedgerIntent,
    SqliteStrategyLedger,
    StrategyBudget,
)

ACCOUNT = "acct-cut"
X = "SIM:X"


def _row(instrument, quantity):
    return {
        "instrument": {"instrument_id": instrument, "venue": "sim"},
        "side": "net",
        "quantity": quantity,
        "observed_at_utc": "2026-11-03T00:00:00Z",
    }


class _Consumer:
    def __init__(self, healthy=True):
        self.healthy = healthy
        self.last_state_status = None
        self.last_page_at_head = False
        self.closed = False

    def poll(self):
        self.last_state_status = {"ready": self.healthy, "received_ns": time.time_ns()}
        self.last_page_at_head = True
        return SimpleNamespace(projection={"view_scope": "v", "positions": {"k": _row(X, "9")}})

    def close(self):
        self.closed = True


class _Runtime:
    def __init__(self, quantity="2"):
        self.quantity = quantity
        self.refuse = False
        self.calls = []

    def next_maintenance_delay(self):
        return None

    def call(self, mount, operation, arguments, *, account_ref):
        self.calls.append((mount, operation, account_ref))
        if self.refuse:
            raise RuntimeError("GRANT_UNAVAILABLE")
        now = time.time_ns()
        return {
            "report": {
                "account_ref": account_ref,
                "positions": [_row(X, self.quantity)],
                "observed_ns": now,
                "valid_until_ns": now + 60_000_000_000,
            }
        }

    def close(self):
        pass


def _stream(consumer, runtime, **kwargs):
    return GatewayAccountPositionStream(
        lambda: (consumer, runtime),
        account_ref=ACCOUNT,
        reconciliation_cut=True,
        poll_interval_seconds=0.02,
        **kwargs,
    )


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def ledger(tmp_path):
    item = SqliteStrategyLedger(
        tmp_path / "ledger.sqlite3",
        account_ref=ACCOUNT,
        instruments={X: InstrumentTerms(Decimal("1"), "USD", "spot")},
        budgets={"A": StrategyBudget(Decimal("1000000"), "USD")},
    )
    item.reserve(LedgerIntent("a", "A", X, "BUY", Decimal("2"), Decimal("10")))
    item.apply_execution("a", cumulative_quantity=2, average_price=10)
    try:
        yield item
    finally:
        item.close()


def test_full_account_cut_makes_the_stream_complete_and_view_stays_diagnostic(ledger):
    runtime = _Runtime("2")
    stream = _stream(_Consumer(), runtime).start(wait_seconds=5)
    try:
        assert _wait(lambda: stream.report().complete)
        report = stream.report()
        assert report.positions == {X: Decimal("2")}
        assert report.source.endswith(":reconciliation_cut")
        assert ledger.reconcile(report).state == "BALANCED"
        # The trimmed view (X=9) is never evidence.
        assert stream.diagnostic_report().complete is False
        assert stream.diagnostic_report().positions == {X: Decimal("9")}
        assert runtime.calls[0] == ("store.account", "account.reconciliation_cut", ACCOUNT)
        # Revocation on the server: the next cut poll is refused.
        runtime.refuse = True
        assert _wait(lambda: not stream.report().complete)
        assert stream.cut_positions.interruption == "GATEWAY_RECONCILIATION_REFUSED"
        outcome = ledger.reconcile(stream.report())
        assert (outcome.state, outcome.reason) == ("UNVERIFIED", "REPORT_INCOMPLETE")
    finally:
        stream.close()
    assert stream.report().complete is False


def test_unhealthy_journal_view_withholds_the_cut(ledger):
    runtime = _Runtime("2")
    stream = _stream(_Consumer(healthy=False), runtime).start(wait_seconds=5)
    try:
        assert _wait(lambda: stream.cut_positions.interruption is not None)
        time.sleep(0.1)
        assert stream.report().complete is False
        assert stream.cut_positions.interruption == "GATEWAY_JOURNAL_STREAM_INTERRUPTED"
        assert runtime.calls == []
    finally:
        stream.close()


def test_cut_needs_a_runtime_and_excludes_a_local_verified_cut():
    stream = GatewayAccountPositionStream(
        lambda: _Consumer(), account_ref=ACCOUNT, reconciliation_cut=True,
        poll_interval_seconds=0.02,
    ).start(wait_seconds=5)
    try:
        assert _wait(lambda: stream.error == "RECONCILIATION_RUNTIME_REQUIRED")
        assert stream.report().complete is False
    finally:
        stream.close()
    with pytest.raises(AccountPositionStreamError):
        GatewayAccountPositionStream(
            lambda: None, account_ref=ACCOUNT, reconciliation_cut="yes"
        )


def test_without_the_cut_the_stream_is_never_complete():
    stream = GatewayAccountPositionStream(
        lambda: (_Consumer(), _Runtime()), account_ref=ACCOUNT, poll_interval_seconds=0.02
    ).start(wait_seconds=5)
    try:
        time.sleep(0.15)
        assert stream.report().complete is False
        assert stream.cut_positions is None
    finally:
        stream.close()


def test_evidence_for_another_account_is_refused():
    class _Other(_Runtime):
        def call(self, mount, operation, arguments, *, account_ref):
            reply = super().call(mount, operation, arguments, account_ref=account_ref)
            reply["report"]["account_ref"] = "someone-else"
            return reply

    stream = _stream(_Consumer(), _Other()).start(wait_seconds=5)
    try:
        assert _wait(lambda: stream.cut_positions.interruption == "GATEWAY_ACCOUNT_SCOPE_MISMATCH")
        assert stream.report().complete is False
    finally:
        stream.close()
