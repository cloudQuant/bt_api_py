"""Iteration 60 (post-acceptance): read-only ``SqliteStrategyLedger.execution_totals``.

A Backtrader process that books a late fee correction without the original
order (after a restart) compares the intent's recorded fee total with its own
durable record before calling ``apply_execution`` with the unchanged quantity.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bt_api_py.strategy_ledger import (
    InstrumentTerms,
    LedgerIntent,
    SqliteStrategyLedger,
    StrategyBudget,
    StrategyLedgerError,
)

X = "PAPER:SIM:X"


@pytest.fixture
def ledger(tmp_path):
    item = SqliteStrategyLedger(
        tmp_path / "ledger.sqlite3",
        account_ref="account-a",
        instruments={X: InstrumentTerms(Decimal("10"), "CNY", "futures")},
        budgets={"A": StrategyBudget(Decimal("1000000000"), "CNY")},
    )
    yield item
    item.close()


def test_totals_follow_cumulative_executions_and_fee_only_reports(ledger):
    ledger.reserve(LedgerIntent("i-1", "A", X, "BUY", Decimal("2"), Decimal("100")))
    assert ledger.execution_totals("i-1") == (Decimal("0"), {})
    ledger.apply_execution(
        "i-1", cumulative_quantity=1, average_price=100, fee="0.5", fee_currency="CNY"
    )
    ledger.apply_execution("i-1", cumulative_quantity=1, fee="0.1", fee_currency="USD")
    filled, fees = ledger.execution_totals("i-1")
    assert filled == Decimal("1")
    assert dict(fees) == {"CNY": Decimal("0.5"), "USD": Decimal("0.1")}
    # A fee-only correction at the recorded quantity moves only the fee total.
    assert ledger.apply_execution("i-1", cumulative_quantity=filled, fee="0.8",
                                  fee_currency="CNY") == Decimal("0")
    assert ledger.execution_totals("i-1")[1]["CNY"] == Decimal("0.8")
    assert ledger.positions("A")[X] == Decimal("1")
    with pytest.raises(TypeError):
        fees["CNY"] = Decimal("9")


def test_unknown_intent_is_refused(ledger):
    with pytest.raises(StrategyLedgerError, match="INTENT_NOT_FOUND"):
        ledger.execution_totals("missing")
