"""Iteration 60 acceptance report 2026-11-03 F3/F4/F5 and G1: SDK strategy ledger.

F3  a strategy without a budget is always refused (``BUDGET_UNCONFIGURED``);
    the former ``require_budget=False`` opt-out is removed (G4, plan 60.07);
    closing never needs a budget; budgets never mix currencies.
F4  fees are cumulative per (intent, currency): a late fee for an unchanged
    quantity is applied, a report without a fee never resets the total.
F5  only a complete, fresh ``AccountPositionReport`` for this account can be
    ``BALANCED``; a plain mapping is not evidence.
G1  a strategy with ``capital`` has its own spot cash, moved by its own fills
    and fees, admitted atomically and published only after a trusted reconcile.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from bt_api_py.strategy_ledger import (
    AccountPositionReport,
    InstrumentTerms,
    LedgerIntent,
    SqliteStrategyLedger,
    StrategyBudget,
    StrategyCapital,
    StrategyLedgerError,
)

ACCOUNT = "acct-1"
SPOT = "SIM:SPOT:ABC"
FUT = "SIM:FUT:SA609"
USD = "SIM:SPOT:USDX"
NOW = 1_800_000_000_000_000_000
TERMS = {
    SPOT: InstrumentTerms(Decimal("1"), "CNY", "spot"),
    FUT: InstrumentTerms(Decimal("20"), "CNY", "futures"),
    USD: InstrumentTerms(Decimal("1"), "USD", "spot"),
}
# A generous budget for tests that exercise other rules than the budget limit.
WIDE_BUDGETS = {name: StrategyBudget(Decimal("1000000000"), "CNY") for name in ("A", "B", "s")}


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> int:
        return self.now


def _code(call):
    with pytest.raises(StrategyLedgerError) as raised:
        call()
    return raised.value.code


def _intent(intent_id, strategy, side, quantity, price="100", instrument=SPOT):
    return LedgerIntent(
        intent_id,
        strategy,
        instrument,
        side,
        Decimal(quantity),
        None if price is None else Decimal(price),
    )


def _report(positions, *, observed_ns=NOW, complete=True, account=ACCOUNT):
    return AccountPositionReport(account, "provider:sim", observed_ns, complete, positions)


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def ledger(tmp_path, clock):
    item = SqliteStrategyLedger(
        tmp_path / "ledger.sqlite3",
        account_ref=ACCOUNT,
        instruments=TERMS,
        budgets={
            "A": StrategyBudget(Decimal("100000"), "CNY"),
            "B": StrategyBudget(Decimal("100000"), "CNY"),
        },
        capital={
            "A": StrategyCapital(Decimal("20000"), "CNY"),
            "B": StrategyCapital(Decimal("30000"), "CNY"),
        },
        clock_ns=clock,
    )
    try:
        yield item
    finally:
        item.close()


def _fill(ledger, intent, quantity=None, price="100", **fee):
    ledger.reserve(intent)
    ledger.mark_submitted(intent.intent_id)
    ledger.apply_execution(
        intent.intent_id,
        cumulative_quantity=intent.quantity if quantity is None else Decimal(quantity),
        average_price=None if price is None else Decimal(price),
        **fee,
    )


# -- F3: budget is required by default ---------------------------------------------


def test_unbudgeted_open_is_refused_by_default(tmp_path):
    ledger = SqliteStrategyLedger(tmp_path / "f3.sqlite3", account_ref=ACCOUNT, instruments=TERMS)
    try:
        assert _code(lambda: ledger.reserve(_intent("i", "s", "BUY", "1"))) == "BUDGET_UNCONFIGURED"
    finally:
        ledger.close()


def test_report_reproduction_f3_f4_f5(tmp_path):
    """The exact report script: OPEN, 0, {}, BALANCED must no longer happen."""

    ledger = SqliteStrategyLedger(tmp_path / "repro.sqlite3", account_ref="a")
    try:
        assert (
            _code(lambda: ledger.reserve(LedgerIntent("i", "s", "X", "BUY", Decimal(1), Decimal(100))))
            == "BUDGET_UNCONFIGURED"
        )
        assert _code(lambda: ledger.reconcile({"X": 1})) == "ACCOUNT_POSITIONS_INVALID"
    finally:
        ledger.close()


def test_budget_opt_out_no_longer_exists(tmp_path):
    """G4: "没配预算一律拒绝" - there is no constructor switch to skip the budget."""

    with pytest.raises(TypeError):
        SqliteStrategyLedger(
            tmp_path / "optout.sqlite3",
            account_ref=ACCOUNT,
            instruments=TERMS,
            require_budget=False,
        )
    ledger = SqliteStrategyLedger(
        tmp_path / "optout.sqlite3",
        account_ref=ACCOUNT,
        instruments=TERMS,
        budgets={"other": StrategyBudget(Decimal("1000"), "CNY")},
    )
    try:
        assert _code(lambda: ledger.reserve(_intent("i", "s", "BUY", "1"))) == (
            "BUDGET_UNCONFIGURED"
        )
    finally:
        ledger.close()


def test_budget_never_mixes_currencies(tmp_path):
    ledger = SqliteStrategyLedger(
        tmp_path / "ccy.sqlite3",
        account_ref=ACCOUNT,
        instruments=TERMS,
        budgets={"A": StrategyBudget(Decimal("100000"), "CNY")},
    )
    try:
        code = _code(lambda: ledger.reserve(_intent("u", "A", "BUY", "1", instrument=USD)))
        assert code == "BUDGET_CURRENCY_MISMATCH"
    finally:
        ledger.close()


def test_closing_needs_no_budget(tmp_path):
    ledger = SqliteStrategyLedger(
        tmp_path / "close.sqlite3", account_ref=ACCOUNT, instruments=TERMS, budgets=WIDE_BUDGETS
    )
    try:
        _fill(ledger, _intent("o", "A", "BUY", "2"))
    finally:
        ledger.close()
    strict = SqliteStrategyLedger(
        tmp_path / "close.sqlite3", account_ref=ACCOUNT, instruments=TERMS
    )
    try:
        # Closing is admitted without any budget; opening is refused.
        assert strict.reserve(_intent("c", "A", "SELL", "2")) == "CLOSE"
        assert _code(lambda: strict.reserve(_intent("o2", "A", "BUY", "1"))) == (
            "BUDGET_UNCONFIGURED"
        )
        # Closing capacity is still enforced without a budget.
        assert _code(lambda: strict.reserve(_intent("c2", "A", "SELL", "1"))) == (
            "CLOSE_CAPACITY_EXCEEDED"
        )
    finally:
        strict.close()


# -- F4: fee identity ---------------------------------------------------------------


def test_late_fee_for_an_unchanged_quantity_is_applied(ledger):
    _fill(ledger, _intent("i", "A", "BUY", "1"))
    assert (
        ledger.apply_execution(
            "i", cumulative_quantity=1, average_price=100, fee="2", fee_currency="CNY"
        )
        == 0
    )
    assert dict(ledger.snapshot("A").fees_since_baseline) == {"CNY": Decimal("2")}
    # Redelivery of the same cumulative fee is idempotent.
    ledger.apply_execution("i", cumulative_quantity=1, average_price=100, fee="2", fee_currency="CNY")
    assert dict(ledger.snapshot("A").fees_since_baseline) == {"CNY": Decimal("2")}


def test_a_report_without_a_fee_never_resets_the_fee_total(ledger):
    """Before the fix: fee 2, then no fee, then fee 5 was booked as 2 + 5 = 7."""

    ledger.reserve(_intent("i", "A", "BUY", "3"))
    ledger.apply_execution("i", cumulative_quantity=1, average_price=100, fee="2", fee_currency="CNY")
    ledger.apply_execution("i", cumulative_quantity=2, average_price=100)
    ledger.apply_execution("i", cumulative_quantity=3, average_price=100, fee="5", fee_currency="CNY")
    assert dict(ledger.snapshot("A").fees_since_baseline) == {"CNY": Decimal("5")}


def test_fee_totals_are_per_currency_and_cannot_regress(ledger):
    ledger.reserve(_intent("i", "A", "BUY", "2"))
    ledger.apply_execution("i", cumulative_quantity=1, average_price=100, fee="1", fee_currency="CNY")
    ledger.apply_execution("i", cumulative_quantity=1, fee="0.1", fee_currency="BNB")
    assert dict(ledger.snapshot("A").fees_since_baseline) == {
        "CNY": Decimal("1"),
        "BNB": Decimal("0.1"),
    }
    code = _code(
        lambda: ledger.apply_execution(
            "i", cumulative_quantity=2, average_price=100, fee="0.5", fee_currency="CNY"
        )
    )
    assert code == "EXECUTION_FEE_REGRESSED"


def test_stale_reordered_report_is_ignored_including_its_fee(ledger):
    ledger.reserve(_intent("i", "A", "BUY", "2"))
    ledger.apply_execution("i", cumulative_quantity=2, average_price=100, fee="3", fee_currency="CNY")
    assert (
        ledger.apply_execution(
            "i", cumulative_quantity=1, average_price=100, fee="1", fee_currency="CNY"
        )
        == 0
    )
    assert dict(ledger.snapshot("A").fees_since_baseline) == {"CNY": Decimal("3")}


def test_fees_survive_restart(tmp_path, clock):
    path = tmp_path / "restart.sqlite3"
    kwargs = dict(account_ref=ACCOUNT, instruments=TERMS, budgets=WIDE_BUDGETS, clock_ns=clock)
    first = SqliteStrategyLedger(path, **kwargs)
    _fill(first, _intent("i", "A", "BUY", "1"), fee="2", fee_currency="CNY")
    first.close()
    second = SqliteStrategyLedger(path, **kwargs)
    try:
        second.apply_execution(
            "i", cumulative_quantity=1, average_price=100, fee="2.5", fee_currency="CNY"
        )
        assert dict(second.snapshot("A").fees_since_baseline) == {"CNY": Decimal("2.5")}
    finally:
        second.close()


# -- F5: trusted reconciliation -------------------------------------------------------


def test_plain_mapping_is_not_reconciliation_evidence(ledger):
    assert _code(lambda: ledger.reconcile({SPOT: 0})) == "ACCOUNT_POSITIONS_INVALID"


def test_foreign_account_report_is_refused(ledger):
    code = _code(lambda: ledger.reconcile(_report({}, account="other")))
    assert code == "ACCOUNT_POSITIONS_ACCOUNT_MISMATCH"


def test_incomplete_stale_or_future_reports_are_unverified(ledger, clock):
    _fill(ledger, _intent("i", "A", "BUY", "1"))
    assert ledger.reconcile(_report({SPOT: 1})).state == "BALANCED"
    incomplete = ledger.reconcile(_report({SPOT: 1}, complete=False))
    assert (incomplete.state, incomplete.reason) == ("UNVERIFIED", "REPORT_INCOMPLETE")
    stale = ledger.reconcile(_report({SPOT: 1}, observed_ns=NOW - 31_000_000_000))
    assert (stale.state, stale.reason) == ("UNVERIFIED", "REPORT_STALE")
    future = ledger.reconcile(_report({SPOT: 1}, observed_ns=NOW + 1))
    assert (future.state, future.reason) == ("UNVERIFIED", "REPORT_FROM_FUTURE")


def test_difference_with_pending_orders_is_in_flight_not_residual(ledger):
    _fill(ledger, _intent("i", "A", "BUY", "1"))
    ledger.reserve(_intent("j", "A", "BUY", "1"))
    ledger.mark_submitted("j")
    # The provider already holds the second unit; the ledger has not seen it.
    assert ledger.reconcile(_report({SPOT: 2})).state == "IN_FLIGHT"
    ledger.finish("j", "CANCELLED")
    assert ledger.reconcile(_report({SPOT: 2})).state == "RESIDUAL"


def test_reader_refuses_unverified_and_residual(ledger):
    _fill(ledger, _intent("i", "A", "BUY", "1"))
    reader = ledger.reader("A", account_positions=lambda: _report({SPOT: 1}, complete=False))
    assert _code(reader.snapshot) == "STRATEGY_BOOK_RECONCILIATION_UNVERIFIED"
    residual = ledger.reader("A", account_positions=lambda: _report({SPOT: 7}))
    assert _code(residual.snapshot) == "STRATEGY_BOOK_RECONCILIATION_RESIDUAL"


def test_fill_after_the_report_is_in_flight_positions_only(ledger, clock):
    observed = clock.now
    clock.now += 1_000_000  # the fill lands 1 ms after the provider snapshot
    _fill(ledger, _intent("i", "A", "BUY", "1"))
    report = _report({}, observed_ns=observed)  # provider does not show it yet
    assert ledger.reconcile(report).state == "IN_FLIGHT"
    snapshot = ledger.reader("A", account_positions=lambda: report).snapshot()
    assert snapshot.positions == {SPOT: Decimal("1")}
    assert snapshot.financial_available is False
    assert snapshot.financial_error_code == "STRATEGY_BOOK_RECONCILIATION_IN_FLIGHT"
    # A fill the report already covers is not excused.
    assert ledger.reconcile(_report({}, observed_ns=clock.now)).state == "RESIDUAL"


# -- G1: strategy cash ---------------------------------------------------------------


def test_cash_is_published_only_after_a_trusted_reconcile(ledger):
    _fill(ledger, _intent("i", "A", "BUY", "10"), fee="5", fee_currency="CNY")
    unreconciled = ledger.snapshot("A")
    assert unreconciled.financial_available is False
    assert unreconciled.financial_error_code == "STRATEGY_BOOK_UNRECONCILED"
    reader = ledger.reader("A", account_positions=lambda: _report({SPOT: 10}))
    snapshot = reader.snapshot()
    assert snapshot.financial_available is True
    # 20000 - 10 * 100 - 5 fee
    assert dict(snapshot.cash_by_currency) == {"CNY": Decimal("18995")}
    cost = snapshot.position_costs[SPOT]
    assert (cost.quantity, cost.average_cost, cost.cost_currency, cost.product_model) == (
        Decimal("10"),
        Decimal("100"),
        "CNY",
        "spot",
    )
    assert cost.metadata_version == TERMS[SPOT].version
    kinds = {mark.source_kind: mark for mark in snapshot.financial_ledger_cut.watermarks}
    assert kinds["account_facts"].source_ref == "provider:sim"
    assert kinds["execution_outbox"].cursor == snapshot.current_execution_cursor


def test_each_strategy_has_its_own_cash(ledger):
    _fill(ledger, _intent("a", "A", "BUY", "10"))
    _fill(ledger, _intent("b", "B", "BUY", "20"))
    report = lambda: _report({SPOT: 30})  # noqa: E731
    assert dict(ledger.reader("A", account_positions=report).snapshot().cash_by_currency) == {
        "CNY": Decimal("19000")
    }
    assert dict(ledger.reader("B", account_positions=report).snapshot().cash_by_currency) == {
        "CNY": Decimal("28000")
    }


def test_sell_proceeds_return_to_the_strategy(ledger):
    _fill(ledger, _intent("a", "A", "BUY", "10"))
    _fill(ledger, _intent("s", "A", "SELL", "4"), price="110")
    snapshot = ledger.reader("A", account_positions=lambda: _report({SPOT: 6})).snapshot()
    assert dict(snapshot.cash_by_currency) == {"CNY": Decimal("19440")}
    assert snapshot.position_costs[SPOT].average_cost == Decimal("100")


def test_buys_must_fit_cash_minus_pending_buys(ledger):
    ledger.reserve(_intent("a1", "A", "BUY", "150"))  # 15000 pending
    assert (
        _code(lambda: ledger.reserve(_intent("a2", "A", "BUY", "60")))
        == "STRATEGY_CASH_INSUFFICIENT"
    )
    assert ledger.reserve(_intent("a3", "A", "BUY", "50")) == "OPEN"  # exactly 20000
    ledger.finish("a1", "CANCELLED")
    assert ledger.reserve(_intent("a4", "A", "BUY", "100")) == "OPEN"


def test_concurrent_buys_never_overspend_cash(tmp_path, clock):
    path = tmp_path / "race.sqlite3"
    kwargs = dict(
        account_ref=ACCOUNT,
        instruments=TERMS,
        budgets=WIDE_BUDGETS,
        capital={"A": StrategyCapital(Decimal("1000"), "CNY")},
        clock_ns=clock,
        exclusive_owner=False,
    )
    instances = [SqliteStrategyLedger(path, **kwargs) for _ in range(6)]
    barrier = threading.Barrier(len(instances))
    results = []

    def attempt(index, item):
        barrier.wait()
        try:
            results.append(item.reserve(_intent(f"b{index}", "A", "BUY", "6")))
        except StrategyLedgerError as error:
            results.append(error.code)

    threads = [threading.Thread(target=attempt, args=pair) for pair in enumerate(instances)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for item in instances:
        item.close()
    assert results.count("OPEN") == 1
    assert results.count("STRATEGY_CASH_INSUFFICIENT") == 5


def test_unsupported_models_are_refused_before_dispatch(ledger):
    assert (
        _code(lambda: ledger.reserve(_intent("f", "A", "BUY", "1", instrument=FUT)))
        == "STRATEGY_CAPITAL_MODEL_UNSUPPORTED"
    )
    assert (
        _code(lambda: ledger.reserve(_intent("s", "A", "SELL", "1")))
        == "STRATEGY_CAPITAL_MODEL_UNSUPPORTED"
    )
    assert (
        _code(lambda: ledger.reserve(_intent("u", "A", "BUY", "1", instrument=USD)))
        == "BUDGET_CURRENCY_MISMATCH"
    )


def test_fill_without_price_makes_cash_unknown_and_blocks_cash_buys(ledger):
    _fill(ledger, _intent("a", "A", "BUY", "1"), price=None)
    snapshot = ledger.reader("A", account_positions=lambda: _report({SPOT: 1})).snapshot()
    assert snapshot.financial_available is False
    assert snapshot.financial_error_code == "STRATEGY_CASH_UNKNOWN"
    assert _code(lambda: ledger.reserve(_intent("b", "A", "BUY", "1"))) in {
        "COST_UNKNOWN",
        "STRATEGY_CASH_UNKNOWN",
    }
    # Reducing risk is still possible.
    assert ledger.reserve(_intent("c", "A", "SELL", "1")) == "CLOSE"


def test_fee_in_another_currency_is_kept_apart(ledger):
    _fill(ledger, _intent("a", "A", "BUY", "1"), fee="0.01", fee_currency="BNB")
    snapshot = ledger.reader("A", account_positions=lambda: _report({SPOT: 1})).snapshot()
    assert dict(snapshot.cash_by_currency) == {"CNY": Decimal("19900"), "BNB": Decimal("-0.01")}


def test_capital_is_persisted_and_cannot_change_or_arrive_late(tmp_path, clock):
    path = tmp_path / "capital.sqlite3"
    base = dict(account_ref=ACCOUNT, instruments=TERMS, budgets=WIDE_BUDGETS, clock_ns=clock)
    first = SqliteStrategyLedger(path, capital={"A": StrategyCapital(Decimal("100"), "CNY")}, **base)
    _fill(first, _intent("b", "B", "BUY", "1"))
    first.close()
    assert (
        _code(
            lambda: SqliteStrategyLedger(
                path, capital={"A": StrategyCapital(Decimal("200"), "CNY")}, **base
            )
        )
        == "LEDGER_CAPITAL_MISMATCH"
    )
    assert (
        _code(
            lambda: SqliteStrategyLedger(
                path, capital={"B": StrategyCapital(Decimal("200"), "CNY")}, **base
            )
        )
        == "LEDGER_CAPITAL_LATE"
    )


def test_strategy_without_capital_has_no_cash(ledger, tmp_path, clock):
    other = SqliteStrategyLedger(
        tmp_path / "nocap.sqlite3",
        account_ref=ACCOUNT,
        instruments=TERMS,
        budgets=WIDE_BUDGETS,
        clock_ns=clock,
    )
    try:
        _fill(other, _intent("a", "A", "BUY", "1"))
        snapshot = other.reader("A", account_positions=lambda: _report({SPOT: 1})).snapshot()
        assert snapshot.financial_available is False
        assert snapshot.financial_error_code == "STRATEGY_CAPITAL_UNCONFIGURED"
    finally:
        other.close()
