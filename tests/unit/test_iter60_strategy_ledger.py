"""Iteration 60 (acceptance report P0-3/P1-1/P1-5): SDK strategy ledger.

The ledger has no bt_api_execution dependency. Shared account: strategy A
owns 2, strategy B owns 3 of instrument X.
"""

from __future__ import annotations

import multiprocessing
import subprocess
import sys
import textwrap
import threading
from decimal import Decimal

import pytest

from bt_api_py.strategy_ledger import (
    AccountPositionReport,
    InstrumentTerms,
    LedgerIntent,
    SqliteStrategyLedger,
    StrategyBudget,
    StrategyLedgerError,
)

X = "PAPER:SIM:X"
ACCOUNT = "account-shared"
TERMS = {X: InstrumentTerms(Decimal("10"), "CNY", "futures")}


def _report(positions, *, account=ACCOUNT, complete=True, observed_ns=None):
    import time

    return AccountPositionReport(
        account, "provider:test", observed_ns or time.time_ns(), complete, positions
    )


def _intent(intent_id, strategy, side, quantity, price="100"):
    return LedgerIntent(
        intent_id, strategy, X, side, Decimal(quantity), None if price is None else Decimal(price)
    )


def _fill(ledger, intent, quantity=None, price="100"):
    ledger.reserve(intent)
    ledger.mark_submitted(intent.intent_id)
    ledger.apply_execution(
        intent.intent_id,
        cumulative_quantity=intent.quantity if quantity is None else Decimal(quantity),
        average_price=None if price is None else Decimal(price),
    )


@pytest.fixture
def ledger(tmp_path):
    item = SqliteStrategyLedger(
        tmp_path / "ledger.sqlite3", account_ref=ACCOUNT, instruments=TERMS, require_budget=False
    )
    _fill(item, _intent("a-open", "A", "BUY", "2"))
    _fill(item, _intent("b-open", "B", "BUY", "3"))
    try:
        yield item
    finally:
        item.close()


def _code(call):
    with pytest.raises(StrategyLedgerError) as raised:
        call()
    return raised.value.code


def test_positions_are_owned_per_strategy(ledger):
    assert ledger.positions("A") == {X: Decimal("2")}
    assert ledger.positions("B") == {X: Decimal("3")}
    snapshot = ledger.snapshot("A")
    assert snapshot.positions == {X: Decimal("2")} and snapshot.account_ref == ACCOUNT
    assert snapshot.financial_available is False


def test_a_strategy_cannot_close_more_than_it_owns(ledger):
    assert (
        _code(lambda: ledger.reserve(_intent("a-5", "A", "SELL", "5"))) == "POSITION_FLIP_REFUSED"
    )
    assert ledger.reserve(_intent("a-2", "A", "SELL", "2")) == "CLOSE"
    # The first close is still pending: nothing is left for A to close.
    assert (
        _code(lambda: ledger.reserve(_intent("a-1", "A", "SELL", "1"))) == "CLOSE_CAPACITY_EXCEEDED"
    )
    # B's share is untouched.
    assert ledger.reserve(_intent("b-3", "B", "SELL", "3")) == "CLOSE"


def test_unknown_outcome_keeps_capacity_across_restart(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    first = SqliteStrategyLedger(path, account_ref=ACCOUNT, require_budget=False)
    _fill(first, _intent("a-open", "A", "BUY", "2"))
    first.reserve(_intent("a-close", "A", "SELL", "2"))
    first.mark_unknown("a-close")
    first.close()  # process restarts
    second = SqliteStrategyLedger(path, account_ref=ACCOUNT)
    try:
        assert second.intent_state("a-close") == "UNKNOWN"
        code = _code(lambda: second.reserve(_intent("a-again", "A", "SELL", "1")))
        assert code == "CLOSE_CAPACITY_EXCEEDED"
    finally:
        second.close()


def test_partial_fill_then_cancel_releases_only_the_rest(ledger):
    ledger.reserve(_intent("a-close", "A", "SELL", "2"))
    ledger.apply_execution(
        "a-close", cumulative_quantity=Decimal("1"), average_price=Decimal("101")
    )
    assert ledger.positions("A") == {X: Decimal("1")}
    assert (
        _code(lambda: ledger.reserve(_intent("a-x", "A", "SELL", "1"))) == "CLOSE_CAPACITY_EXCEEDED"
    )
    ledger.finish("a-close", "CANCELLED")
    assert ledger.reserve(_intent("a-rest", "A", "SELL", "1")) == "CLOSE"


def test_late_fill_after_cancel_is_still_applied(ledger):
    ledger.reserve(_intent("a-close", "A", "SELL", "2"))
    ledger.finish("a-close", "CANCELLED")
    ledger.apply_execution("a-close", cumulative_quantity=Decimal("2"), average_price=Decimal("99"))
    assert ledger.positions("A") == {}
    assert ledger.intent_state("a-close") == "CANCELLED"


def test_executions_are_idempotent_and_cannot_overfill(ledger):
    ledger.reserve(_intent("a-close", "A", "SELL", "2"))
    assert ledger.apply_execution("a-close", cumulative_quantity=1, average_price=100) == 1
    assert ledger.apply_execution("a-close", cumulative_quantity=1, average_price=100) == 0
    assert ledger.positions("A") == {X: Decimal("1")}
    assert (
        _code(lambda: ledger.apply_execution("a-close", cumulative_quantity=3))
        == "EXECUTION_OVERFILL"
    )


def test_intent_ids_are_bound_to_their_content(ledger):
    intent = _intent("a-close", "A", "SELL", "1")
    assert ledger.reserve(intent) == ledger.reserve(intent) == "CLOSE"
    assert (
        _code(lambda: ledger.reserve(_intent("a-close", "A", "SELL", "2"))) == "INTENT_ID_CONFLICT"
    )


def test_fees_accumulate_per_strategy(ledger):
    ledger.reserve(_intent("a-close", "A", "SELL", "2"))
    ledger.apply_execution(
        "a-close", cumulative_quantity=1, average_price=100, fee="0.5", fee_currency="CNY"
    )
    ledger.apply_execution(
        "a-close", cumulative_quantity=2, average_price=100, fee="1.2", fee_currency="CNY"
    )
    assert ledger.snapshot("A").fees_since_baseline == {"CNY": Decimal("1.2")}
    assert ledger.snapshot("B").fees_since_baseline == {}


# -- budget (P1-1) -----------------------------------------------------------


def _budget_ledger(tmp_path, **kwargs):
    return SqliteStrategyLedger(
        tmp_path / "budget.sqlite3",
        account_ref=ACCOUNT,
        budgets={"A": StrategyBudget(Decimal("5000"), "CNY")},
        **kwargs,
    )


def test_budget_refuses_unknown_instrument_terms(tmp_path):
    ledger = _budget_ledger(tmp_path)
    try:
        code = _code(lambda: ledger.reserve(_intent("a1", "A", "BUY", "1")))
        assert code == "INSTRUMENT_TERMS_UNKNOWN"
    finally:
        ledger.close()


def test_budget_refuses_an_unknown_price(tmp_path):
    ledger = _budget_ledger(tmp_path, instruments=TERMS)
    try:
        code = _code(lambda: ledger.reserve(_intent("a1", "A", "BUY", "1", price=None)))
        assert code == "PRICE_UNKNOWN"
    finally:
        ledger.close()


def test_budget_refuses_more_exposure_after_a_fill_without_price(tmp_path):
    ledger = _budget_ledger(tmp_path, instruments=TERMS)
    try:
        _fill(ledger, _intent("a1", "A", "BUY", "1"), price=None)
        assert _code(lambda: ledger.reserve(_intent("a2", "A", "BUY", "1"))) == "COST_UNKNOWN"
        # Reducing risk stays possible.
        assert ledger.reserve(_intent("a3", "A", "SELL", "1")) == "CLOSE"
    finally:
        ledger.close()


def test_budget_counts_positions_at_cost_and_pending_opens(tmp_path):
    ledger = _budget_ledger(tmp_path, instruments=TERMS)
    try:
        _fill(ledger, _intent("a1", "A", "BUY", "2"))  # 2 * 100 * 10 = 2000 at cost
        ledger.reserve(_intent("a2", "A", "BUY", "2"))  # pending 2000
        assert _code(lambda: ledger.reserve(_intent("a3", "A", "BUY", "2"))) == "BUDGET_EXCEEDED"
        assert ledger.reserve(_intent("a4", "A", "BUY", "1")) == "OPEN"  # exactly 5000
        # A strategy without a budget is refused, not unlimited (60.07, F3).
        code = _code(lambda: ledger.reserve(_intent("b1", "B", "BUY", "1")))
        assert code == "BUDGET_UNCONFIGURED"
    finally:
        ledger.close()


# -- atomicity across instances and processes (P0-1/P1-1 style) ---------------


def test_one_owner_per_account(tmp_path):
    path = tmp_path / "owner.sqlite3"
    first = SqliteStrategyLedger(path, account_ref=ACCOUNT)
    try:
        assert (
            _code(lambda: SqliteStrategyLedger(path, account_ref=ACCOUNT)) == "OWNER_ALREADY_ACTIVE"
        )
    finally:
        first.close()
    SqliteStrategyLedger(path, account_ref=ACCOUNT).close()


def test_ledger_is_bound_to_one_account(tmp_path):
    path = tmp_path / "account.sqlite3"
    SqliteStrategyLedger(path, account_ref=ACCOUNT).close()
    assert (
        _code(lambda: SqliteStrategyLedger(path, account_ref="other")) == "LEDGER_ACCOUNT_MISMATCH"
    )


def test_concurrent_closes_across_instances_admit_exactly_one(tmp_path):
    path = tmp_path / "race.sqlite3"
    seed = SqliteStrategyLedger(
        path, account_ref=ACCOUNT, exclusive_owner=False, require_budget=False
    )
    _fill(seed, _intent("a-open", "A", "BUY", "2"))
    instances = [
        SqliteStrategyLedger(path, account_ref=ACCOUNT, exclusive_owner=False) for _ in range(8)
    ]
    barrier = threading.Barrier(len(instances))
    results = []

    def attempt(index, ledger):
        barrier.wait()
        try:
            results.append(ledger.reserve(_intent(f"close-{index}", "A", "SELL", "2")))
        except StrategyLedgerError as error:
            results.append(error.code)

    threads = [threading.Thread(target=attempt, args=item) for item in enumerate(instances)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for item in (seed, *instances):
        item.close()
    assert results.count("CLOSE") == 1
    assert results.count("CLOSE_CAPACITY_EXCEEDED") == len(instances) - 1


def _process_attempt(path, index, start, queue):
    ledger = SqliteStrategyLedger(path, account_ref=ACCOUNT, exclusive_owner=False)
    start.wait()
    try:
        queue.put(ledger.reserve(_intent(f"p-close-{index}", "A", "SELL", "2")))
    except StrategyLedgerError as error:
        queue.put(error.code)
    finally:
        ledger.close()


def test_concurrent_closes_across_processes_admit_exactly_one(tmp_path):
    path = tmp_path / "process-race.sqlite3"
    seed = SqliteStrategyLedger(
        path, account_ref=ACCOUNT, exclusive_owner=False, require_budget=False
    )
    _fill(seed, _intent("a-open", "A", "BUY", "2"))
    seed.close()
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    queue = context.Queue()
    processes = [
        context.Process(target=_process_attempt, args=(str(path), index, start, queue))
        for index in range(4)
    ]
    for process in processes:
        process.start()
    start.set()
    results = [queue.get(timeout=60) for _ in processes]
    for process in processes:
        process.join(timeout=60)
    assert results.count("CLOSE") == 1
    assert results.count("CLOSE_CAPACITY_EXCEEDED") == 3


# -- reconciliation and reader ---------------------------------------------------


def test_reader_requires_a_balanced_account(ledger):
    balanced = ledger.reader("A", account_positions=lambda: _report({X: Decimal("5")}))
    assert balanced.snapshot().positions == {X: Decimal("2")}
    residual = ledger.reader("A", account_positions=lambda: _report({X: Decimal("6")}))
    assert _code(residual.snapshot) == "STRATEGY_BOOK_RECONCILIATION_RESIDUAL"
    assert ledger.reconcile(_report({X: Decimal("6")})).residual == {X: Decimal("1")}

    def broken():
        raise RuntimeError("provider down")

    assert (
        _code(ledger.reader("A", account_positions=broken).snapshot)
        == "ACCOUNT_POSITIONS_UNAVAILABLE"
    )


def test_ledger_works_without_bt_api_execution(tmp_path):
    script = textwrap.dedent(
        f"""
        import sys
        from decimal import Decimal

        class _Block:
            def find_spec(self, name, path=None, target=None):
                if name == "bt_api_execution" or name.startswith("bt_api_execution."):
                    raise ImportError("blocked for the test")
                return None

        sys.meta_path.insert(0, _Block())
        from bt_api_py.strategy_ledger import LedgerIntent, SqliteStrategyLedger

        ledger = SqliteStrategyLedger(
            {str(tmp_path / "noexec.sqlite3")!r}, account_ref="acct", require_budget=False
        )
        ledger.reserve(LedgerIntent("i1", "A", "X", "BUY", Decimal("1"), Decimal("1")))
        ledger.apply_execution("i1", cumulative_quantity=Decimal("1"), average_price=Decimal("1"))
        assert ledger.positions("A") == {{"X": Decimal("1")}}
        assert "bt_api_execution" not in sys.modules
        ledger.close()
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
