"""Iteration 60 (R2-D): service strategy books from one strategy-ledger transaction.

The host's account-book source offers ``read_strategy_ledger``; the real
``ExecutionAccountBookReadAdapter`` then answers a strategy ``OWNER_BOOK`` with
one typed ``strategy_account_book`` part read by
``SqliteStrategyLedger.reconciled_snapshot``: positions, cash, costs, terms and
the reconciliation verdict of the same transaction, one declared cut id, Base
contract version 2. Evidence: UNIT/FAKE (in-process adapter, temp SQLite,
subscription state fed by the test; no provider, no network).
"""

from __future__ import annotations

import time
from decimal import Decimal

import msgpack
import pytest

pytest.importorskip("bt_api_execution")

from bt_api_base.account_book_protocol import StrategyAccountBookSnapshot  # noqa: E402

from bt_api_py.account_position_stream import StreamedAccountPositions  # noqa: E402
from bt_api_py.service_account_book_client import (  # noqa: E402
    ServiceStrategyAccountBookClient,
    ServiceStrategyAccountBookClientError,
)
from bt_api_py.service_execution_account_books import (  # noqa: E402
    STRATEGY_ACCOUNT_BOOK_REPLY_SCHEMA,
    STRATEGY_ACCOUNT_BOOK_REPLY_VERSION,
    ExecutionAccountBookReadAdapter,
    StrategyLedgerBookSourceV1,
)
from bt_api_py.strategy_ledger import (  # noqa: E402
    InstrumentTerms,
    LedgerIntent,
    SqliteStrategyLedger,
    StrategyBudget,
    StrategyCapital,
)
from tests.unit import test_service_execution_account_books_integration as books  # noqa: E402

INSTRUMENT = "X"


class _LedgerSource(books._TrustedAccountBooksSource):
    """Trusted host resource that also binds the account's strategy ledger."""

    def __init__(self, store, targets, ledger, positions) -> None:
        super().__init__(store, targets)
        self.ledger = ledger
        self.positions = positions
        self.ledger_reads = 0

    def read_strategy_ledger(self, *, account_key):
        assert account_key == books._ACCOUNT_KEY
        self.ledger_reads += 1
        return StrategyLedgerBookSourceV1(self.ledger, self.positions)

    def read_inputs(self, **_kwargs):  # pragma: no cover - must not be used
        raise AssertionError("strategy ledger views never read execution inputs")


@pytest.fixture
def host(tmp_path):
    from bt_api_execution import SqliteExecutionStore

    store = SqliteExecutionStore(str(tmp_path / "execution.sqlite3"))
    ledger = SqliteStrategyLedger(
        tmp_path / "ledger.sqlite3",
        account_ref=books._ACCOUNT,
        instruments={INSTRUMENT: InstrumentTerms(Decimal("1"), "USDT", "spot")},
        budgets={books._STRATEGY_A: StrategyBudget(Decimal("1000000"), "USDT")},
        capital={books._STRATEGY_A: StrategyCapital(Decimal("20000"), "USDT")},
    )
    ledger.reserve(
        LedgerIntent("a-x", books._STRATEGY_A, INSTRUMENT, "BUY", Decimal("2"), Decimal("100"))
    )
    ledger.apply_execution("a-x", cumulative_quantity=Decimal("2"), average_price=Decimal("100"))
    stream = StreamedAccountPositions(books._ACCOUNT, "gateway:account-stream")
    stream.apply_snapshot({(INSTRUMENT, "net"): Decimal("2")})
    targets = books._targets()
    source = _LedgerSource(store, targets, ledger, stream.report)
    adapter = ExecutionAccountBookReadAdapter(store, targets, source)
    try:
        yield adapter, source, ledger, stream
    finally:
        ledger.close()
        store.close()


def _query(adapter):
    reply = adapter.handle(
        books._BOOKS_OPERATION,
        principal_ref=books._STRATEGY_A_PRINCIPAL,
        identity=books._IDENTITY_A,
        scope=books._WIRE_A,
        arguments={},
    )
    # Cross the process boundary as MessagePack does (tuples become lists).
    return msgpack.unpackb(msgpack.packb(reply, use_bin_type=True), raw=False)


def _client():
    client = object.__new__(ServiceStrategyAccountBookClient)
    object.__setattr__(client, "strategy_ref", books._STRATEGY_A)
    object.__setattr__(client, "account_ref", books._ACCOUNT)
    return client


def _code(call):
    with pytest.raises(ServiceStrategyAccountBookClientError) as raised:
        call()
    return raised.value.code


def test_balanced_book_is_one_transaction_with_one_declared_cut(host):
    adapter, source, ledger, _stream = host
    reply = _query(adapter)
    assert reply["ok"] is True and source.ledger_reads == 1
    result = reply["result"]
    assert result["source"] == "STRATEGY_LEDGER"
    assert result["reconciliation"]["state"] == "BALANCED"
    # Financial figures travel only inside the typed part.
    assert result["financial"]["available"] is False
    book = _client()._typed_snapshot(reply)
    assert type(book) is StrategyAccountBookSnapshot and book.contract_version == 2
    assert book.positions == {INSTRUMENT: Decimal("2")}
    assert book.cash_by_currency == {"USDT": Decimal("19800")}
    assert book.position_costs[INSTRUMENT].metadata_version == (
        InstrumentTerms(Decimal("1"), "USDT", "spot").version
    )
    assert book.financial_cut_relationship == "SAME_TRANSACTION"
    assert book.position_cut_id == book.financial_cut_id == result["reconciliation"]["cut_id"]
    assert book.reconciliation_state == "BALANCED"
    assert book.account_key == book.account_ref == books._ACCOUNT
    assert book.financial_ledger_cut.watermarks[0].cursor == book.current_execution_cursor
    # Each read is its own transaction with its own cut identity.
    again = _client()._typed_snapshot(_query(adapter))
    assert again.position_cut_id != book.position_cut_id


def test_in_flight_book_carries_positions_without_financials(host):
    adapter, _source, ledger, stream = host
    # A pending order on the instrument explains the provider difference.
    ledger.reserve(
        LedgerIntent("a-x2", books._STRATEGY_A, INSTRUMENT, "SELL", Decimal("1"), Decimal("100"))
    )
    stream.apply_snapshot({(INSTRUMENT, "net"): Decimal("1")})
    book = _client()._typed_snapshot(_query(adapter))
    assert book.reconciliation_state == "IN_FLIGHT"
    assert book.financial_available is False and book.financial_cut_id is None
    assert book.financial_error_code == "STRATEGY_BOOK_RECONCILIATION_IN_FLIGHT"


def test_unreconciled_books_are_refused_with_their_verdict(host, monkeypatch):
    adapter, source, ledger, stream = host
    stream.apply_snapshot({(INSTRUMENT, "net"): Decimal("2")}, observed_ns=time.time_ns() - 10**12)
    assert _code(lambda: _client()._typed_snapshot(_query(adapter))) == (
        "STRATEGY_BOOK_RECONCILIATION_UNVERIFIED"
    )
    stream.mark_interrupted("SEQUENCE_GAP")
    assert _code(lambda: _client()._typed_snapshot(_query(adapter))) == (
        "STRATEGY_BOOK_RECONCILIATION_UNVERIFIED"
    )
    # Residual: the provider shows a position no strategy explains (past the
    # settle window, so the recent fill no longer excuses it).
    monkeypatch.setattr(ledger, "_max_age_ns", 1)
    stream.apply_snapshot({(INSTRUMENT, "net"): Decimal("5")})
    stream.confirm_live(valid_until_ns=time.time_ns() + 10**10)
    time.sleep(0.001)
    assert _code(lambda: _client()._typed_snapshot(_query(adapter))) == (
        "STRATEGY_BOOK_RECONCILIATION_RESIDUAL"
    )

    def broken():
        raise RuntimeError("subscription closed")

    source.positions = broken
    assert _code(lambda: _client()._typed_snapshot(_query(adapter))) == (
        "ACCOUNT_POSITIONS_UNAVAILABLE"
    )


def test_old_reply_and_snapshot_versions_are_refused(host):
    adapter, _source, _ledger, _stream = host
    assert STRATEGY_ACCOUNT_BOOK_REPLY_VERSION == 3
    reply = _query(adapter)
    reply["result"]["strategy_account_book"]["data"]["contract_version"] = 1
    assert _code(lambda: _client()._typed_snapshot(reply)) == (
        "ACCOUNT_BOOK_TYPED_VERSION_UNSUPPORTED"
    )
    reply = _query(adapter)
    for name in ("contract_version", "position_cut_id", "financial_cut_id",
                 "reconciliation_state"):
        reply["result"]["strategy_account_book"]["data"].pop(name)
    assert _code(lambda: _client()._typed_snapshot(reply)) == (
        "ACCOUNT_BOOK_TYPED_VERSION_UNSUPPORTED"
    )
    reply = _query(adapter)
    reply["result"]["strategy_account_book"] = {
        "schema_id": STRATEGY_ACCOUNT_BOOK_REPLY_SCHEMA,
        "schema_version": 2,
        "error_code": "STRATEGY_BOOK_RECONCILIATION_RESIDUAL",
    }
    assert _code(lambda: _client()._typed_snapshot(reply)) == (
        "ACCOUNT_BOOK_TYPED_VERSION_UNSUPPORTED"
    )


def test_mismatched_cut_ids_are_refused(host):
    adapter, _source, _ledger, _stream = host
    for field, value in (("financial_cut_id", "other-transaction"), ("position_cut_id", None)):
        reply = _query(adapter)
        reply["result"]["strategy_account_book"]["data"][field] = value
        assert _code(lambda: _client()._typed_snapshot(reply)) == (
            "STRATEGY_FINANCIAL_CUT_MISMATCH"
        )


def test_a_ledger_of_another_account_is_not_served(host, tmp_path):
    adapter, source, _ledger, _stream = host
    other = SqliteStrategyLedger(tmp_path / "other.sqlite3", account_ref="another-account")
    try:
        source.ledger = other
        assert _query(adapter) == {"ok": False, "code": "UNAVAILABLE", "result": {}}
    finally:
        other.close()
    with pytest.raises(TypeError):
        StrategyLedgerBookSourceV1(object(), lambda: None)
