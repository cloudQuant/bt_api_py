"""In-memory Risk ledger and fake observation transport; no source or account proof."""

from contextlib import contextmanager
from decimal import Decimal
import sqlite3
from types import SimpleNamespace

import pytest

from bt_api_base.direct_write_protocol import DirectExecutionEventKind, DirectOrderStatus
from bt_api_base.gateway_execution_stream_protocol import GatewayExecutionObservation, GatewayExecutionStreamEvent
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_risk.core.admission import AccountScope
from bt_api_risk.pretrade import ledger
from bt_api_risk.pretrade.engine import PretradeEngine
from bt_api_py.risk_service_gateway import ObservationPump, RiskFactsCache
from bt_api_py.gateway_account_observer_client import GatewayAccountObservedEvent


EPOCH = "e" * 64
DAY = "20261114"
LATER_DAY = "20261115"
INSTRUMENT = MarketInstrumentKey("synthetic-provider", "SHFE", "synthetic-env",
                                 "FUTURE", "synthetic-contract", "synthetic-contract")


class TransactionContext:
    """Same connection only: no writer fence, transaction management or locking."""

    def __init__(self, txn):
        self.txn = txn

    @contextmanager
    def transaction(self):
        yield self.txn


@contextmanager
def memory_engine():
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA temp_store=MEMORY")
        assert connection.execute("PRAGMA temp_store").fetchone()[0] == 2
        assert connection.execute("PRAGMA journal_mode=MEMORY").fetchone()[0] == "memory"
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
        assert next(row[2] for row in connection.execute("PRAGMA database_list")
                    if row[1] == "main") == ""
        connection.row_factory = sqlite3.Row
        for statement in ledger._split_statements(ledger._SCHEMA):
            connection.execute(statement)
        scope = AccountScope("synthetic-provider", "synthetic-account", "synthetic-env")
        fence = SimpleNamespace(scope=scope, owner_id="synthetic-owner", fencing_token=1)
        txn = ledger.LedgerTxn(connection, scope.key, fence, None, 3)
        engine = PretradeEngine.__new__(PretradeEngine)
        engine._active = None
        engine._run_post([])
        engine._ledger = TransactionContext(txn)
        engine._clock = lambda: 60
        engine._facts = SimpleNamespace(trading_day=lambda: DAY)
        engine._origin = "risk:synthetic-owner"
        yield txn, engine
    finally:
        connection.rollback()
        connection.close()


class ReceiptCapture:
    def __init__(self, engine, *, reject_applied_cursor=False):
        self.engine = engine
        self.receipts = []
        self.reject_applied_cursor = reject_applied_cursor
        self.cursor_calls = 0

    def __getattr__(self, name):
        return getattr(self.engine, name)

    def apply_observations_bound(self, events):
        receipt = self.engine.apply_observations_bound(events)
        self.receipts.append(receipt)
        return receipt

    def observation_cursor_matches(self, epoch, sequence, revision):
        self.cursor_calls += 1
        if self.reject_applied_cursor and sequence >= 0:
            return False
        return self.engine.observation_cursor_matches(epoch, sequence, revision)


def event(sequence, kind, *, price="100", day=DAY):
    observation = GatewayExecutionObservation(
        account_scope="synthetic-account", route="synthetic-route",
        trading_day=day, instrument=INSTRUMENT,
        kind=kind, side="buy", offset="open", order_quantity="10", filled_quantity="3",
        limit_price="100", status=(None if kind == DirectExecutionEventKind.TRADE
                                   else DirectOrderStatus.PARTIALLY_FILLED),
        trade_quantity="3" if kind == DirectExecutionEventKind.TRADE else None,
        trade_price=price if kind == DirectExecutionEventKind.TRADE else None,
        trade_key_digest="c" * 64 if kind == DirectExecutionEventKind.TRADE else None,
        venue_key_digest=None,
    )
    stream_event = GatewayExecutionStreamEvent(
        sequence=sequence, request_key="synthetic-order", reservation_id="synthetic-reservation",
        command_principal="external-principal", command_digest="a" * 64,
        fact_digest="b" * 64, observation=observation)
    return GatewayAccountObservedEvent(strategy_scope="synthetic-strategy", event=stream_event)


class Observer:
    def __init__(self, pages, *, offered, acknowledged=0):
        self.pages = list(pages)
        self.offered, self.acknowledged = offered, acknowledged
        self.reads, self.acks = [], []

    def progress(self, *, timeout_seconds):
        return SimpleNamespace(outcome="OBSERVED", code=None, progress=SimpleNamespace(
            epoch=EPOCH, offered_sequence=self.offered, acknowledged_sequence=self.acknowledged))

    def read_after(self, **kwargs):
        self.reads.append(kwargs)
        events = self.pages.pop(0)
        return SimpleNamespace(outcome="OBSERVED", code=None, page=SimpleNamespace(
            progress=SimpleNamespace(epoch=EPOCH, offered_sequence=self.offered,
                                     acknowledged_sequence=self.acknowledged),
            events=events, has_more=False))

    def acknowledge(self, sequence, *, expected_epoch, timeout_seconds):
        self.acks.append(sequence)
        self.acknowledged = sequence
        return SimpleNamespace(outcome="OBSERVED", progress=SimpleNamespace(
            epoch=expected_epoch, offered_sequence=self.offered, acknowledged_sequence=sequence))


def pump(engine, observer, facts):
    return ObservationPump(engine, observer, facts, risk_principal="synthetic-risk",
                           intent_payload=lambda key: None, timeout_seconds=1)


def test_trade_identity_conflict_blocks_ack_and_later_day():
    # No pytest host fixture, network, socket or provider client is involved.
    with memory_engine() as (txn, engine):
        capture = ReceiptCapture(engine)
        facts = RiskFactsCache({})
        prefix = [event(1, DirectExecutionEventKind.ORDER), event(2, DirectExecutionEventKind.TRADE)]
        conflict = event(3, DirectExecutionEventKind.TRADE, price="101", day=LATER_DAY)
        observer = Observer([prefix, [conflict]], offered=2)
        value = pump(capture, observer, facts)
        assert value.poll() == 2
        assert observer.acks == [2] and value._after == 2 and value.verified_epoch == EPOCH
        assert facts.trading_day() == DAY
        assert txn.counter(INSTRUMENT.instrument_id, DAY, "open_volume") == Decimal(3)
        observer.offered = 3
        assert value.poll() == 1  # count denotes Risk's actual application, not authorization to ACK
        receipt = capture.receipts[-1]
        assert (receipt.blocked, receipt.last_sequence, receipt.applied_count, receipt.covered_count) == (
            True, 2, 1, 1)
        assert txn.first_audit_detail("trade_identity_conflict") is not None
        assert txn.cursor()["continuity"] == "UNVERIFIED"
        assert value._after == 2 and observer.acks == [2] and value.verified_epoch is None
        assert facts.trading_day() is None

        # The same ledger remains blocked across a retry, observer replacement, and a new pump.
        observer.pages.append([conflict])
        reads_before_retry = len(observer.reads)
        assert value.poll() == 0 and observer.acks == [2] and value.verified_epoch is None
        assert len(observer.reads) == reads_before_retry
        replacement = Observer([prefix, [conflict]], offered=3, acknowledged=2)
        value.replace_observer(replacement)
        assert value.poll() == 0 and replacement.acks == [] and replacement.reads == []
        assert value.verified_epoch is None
        old_cursor_observer = Observer([prefix, [conflict]], offered=3, acknowledged=2)
        restarted = pump(capture, old_cursor_observer, facts)
        assert restarted.poll() == 0 and old_cursor_observer.acks == [] and old_cursor_observer.reads == []
        assert restarted._after == 0
        assert restarted.verified_epoch is None and facts.trading_day() is None
        assert txn.first_audit_detail("trade_identity_conflict") is not None
        assert txn.cursor()["continuity"] == "UNVERIFIED"


def test_unproven_applied_cursor_does_not_advance_event_day():
    with memory_engine() as (txn, engine):
        capture = ReceiptCapture(engine, reject_applied_cursor=True)
        facts = RiskFactsCache({})
        observer = Observer([[event(1, DirectExecutionEventKind.ORDER)]], offered=1)
        value = pump(capture, observer, facts)
        assert value.poll() == 1
        assert capture.receipts[-1].blocked is False
        assert observer.acks == [] and value._after == 0 and value.verified_epoch is None
        assert value.last_code == "observation_apply_unproven"
        assert facts.trading_day() is None


@pytest.mark.parametrize("failure", ["replace_observer", "reenter_poll", "cursor_changed"])
def test_fact_callback_invalidation_never_acks_old_observer(failure):
    class CallbackFacts(RiskFactsCache):
        callback = None
        calls = 0

        def note_event_day(self, trading_day):
            self.calls += 1
            super().note_event_day(trading_day)
            self.callback()

    with memory_engine() as (txn, engine):
        capture = ReceiptCapture(engine)
        facts = CallbackFacts({})
        observer = Observer([[event(1, DirectExecutionEventKind.ORDER),
                              event(2, DirectExecutionEventKind.TRADE)]], offered=2)
        replacement = Observer([], offered=2)
        value = pump(capture, observer, facts)
        callback_cursor_calls = []
        if failure == "replace_observer":
            def replace():
                value.replace_observer(replacement)
                callback_cursor_calls.append(capture.cursor_calls)
            facts.callback = replace
        elif failure == "reenter_poll":
            def reenter():
                value.poll()
                callback_cursor_calls.append(capture.cursor_calls)
            facts.callback = reenter
        else:
            def invalidate_cursor():
                capture.reject_applied_cursor = True
                callback_cursor_calls.append(capture.cursor_calls)
            facts.callback = invalidate_cursor
        assert value.poll() == 2
        assert observer.acks == [] and replacement.acks == []
        assert capture.cursor_calls == callback_cursor_calls[0] + (failure == "cursor_changed")
        assert value._after == 0 and value.verified_epoch is None
        assert facts.calls == 1
        assert facts.trading_day() is None


@pytest.mark.parametrize("days,seed", [
    (["20261114", "20261113", "20261117", "20261116", "20261115"], None),
    (["20261112", "20261114", "20261113"], None),
    (["20261114", "20261113", "20261112"], "20261120"),
])
@pytest.mark.parametrize("page_size", [2, 256])
def test_page_day_aggregation_matches_builtin_cache_and_has_constant_cursor_checks(days, seed, page_size):
    class CountingFacts(RiskFactsCache):
        def __init__(self):
            super().__init__({})
            self.projected = []

        def note_event_day(self, day):
            self.projected.append(day)
            super().note_event_day(day)

    with memory_engine() as (txn, engine):
        capture = ReceiptCapture(engine)
        facts, reference = CountingFacts(), RiskFactsCache({})
        if seed is not None:
            facts.note_event_day(seed)
            reference.note_event_day(seed)
            facts.projected.clear()
            # Pump restart revokes the previous observation day before replay.
            reference.forget_event_day()
        raw_days = [days[index % len(days)] for index in range(page_size)]
        for raw_day in raw_days:
            reference.note_event_day(raw_day)
        observer = Observer([[event(index + 1, DirectExecutionEventKind.ORDER, day=day)
                              for index, day in enumerate(raw_days)]], offered=page_size)
        value = pump(capture, observer, facts)
        assert value.poll() == page_size
        valid = [day for day in raw_days if type(day) is str and len(day) == 8 and day.isdigit()]
        assert facts.projected == [max(valid, default=None)]
        assert facts.trading_day() == reference.trading_day()
        assert capture.cursor_calls == 4  # reset, pre-projection, pre-ACK, post-ACK
        assert observer.acks == [page_size] and value._after == page_size and value.verified_epoch == EPOCH
