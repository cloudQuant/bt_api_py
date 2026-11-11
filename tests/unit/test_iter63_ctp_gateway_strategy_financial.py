"""CTP producer of strategy cash/value snapshots on a real journal (D63-53, D63-54, D63-55)."""
import logging
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import DirectExecutionEventKind, DirectOrderStatus
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    GatewayDirectCommandJournal,
)
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from test_iter63_direct_execution_journal import fact

import bt_api_py.ctp_gateway_strategy_financial as module
from bt_api_py.ctp_gateway_strategy_financial import (
    CtpGatewayStrategyFinancial,
    CtpGatewayStrategyFinancialError,
    CtpStrategyFinancialSettings,
    OwnerLastPrices,
)
from bt_api_py.gateway_strategy_financial_source import GatewayStrategyFinancialSourceError
from bt_api_py.strategy_rates import parse_strategy_rates

D = Decimal
RATES = parse_strategy_rates(b"""
schema_version: 1
currency: CNY
products:
  rb:
    multiplier: 10
    margin_ratio: {long: "0.10", short: "0.12"}
    commission:
      open: {per_lot: "0", ratio: "0.0001"}
      close: {per_lot: "0", ratio: "0.0001"}
      close_today: {per_lot: "0", ratio: "0.0001"}
""")
UPGRADES = ("enable_controls", "enable_strategy_ledger", "enable_account_extensions",
            "enable_parent_authority", "enable_account_facts_financial")
FLAGS = {"controls_enabled": True, "strategy_ledger_enabled": True, "account_extensions_enabled": True,
         "parent_authority_enabled": True, "account_facts_financial_enabled": True}
UNAVAILABLE = "STRATEGY_FINANCIAL_UNAVAILABLE"


def _tick(symbol, price, **changes):
    """One projected tick as the owner-side market adapter hands it on."""
    data = {"schema": "sdk.market.tick", "instrument": {"provider": "ctp", "native_symbol": symbol},
            "last_price": price}
    data.update(changes)
    return SimpleNamespace(data=data)


@contextmanager
def _admitted():
    yield lambda: None


class World:
    def __init__(self, tmp_path):
        directory = tmp_path / "guards"
        directory.mkdir()
        self.guard = LocalAccountSessionGuard(directory, "acct", "owner")
        self.path = tmp_path / "db"
        self.journal_clock = [100.0]
        self.journal = GatewayDirectCommandJournal.create(self.path, "acct", generation=self.guard.acquire(),
                                                          clock=lambda: self.journal_clock[0])
        self.clock, self.continuous = [1000.0], [True]
        self.why = ["CALLBACK_FAILED"]          # what the return stream names while it is not continuous
        self.prices = OwnerLastPrices(clock=lambda: self.clock[0])
        self.ledger = self.source = None
        self.rows, self.quantities, self.accepted, self.refs = {}, {}, set(), 0

    # -- commands --------------------------------------------------------

    def _native(self, key):
        return {"exchange_name": "CTP", "client_order_id": key, "session_generation": 1, "fields_json": "{}"}

    def reserve(self, strategy, key, side, offset, quantity="1", price="3000"):
        payload = {"symbol": "rb2601", "side": side, "offset": offset, "quantity": quantity}
        if price is not None:
            payload["price"] = price
        self.rows[key] = (strategy, self.journal.reserve(strategy, key, "principal", payload))
        self.quantities[key] = quantity

    def dispatch(self, key, status="SENT"):
        strategy, row = self.rows[key]
        self.journal.begin_dispatch(strategy, key, row.reservation_id, row.digest, "principal", 1,
                                    self._native(key), (key + "-anchor",))
        if status != "DISPATCHING":
            self.journal.finish(strategy, key, row.reservation_id, row.digest, "principal", 2,
                                self._native(key), (key + "-anchor",), status)

    def order(self, strategy, key, side, offset, quantity="1", price="3000", status="SENT"):
        self.reserve(strategy, key, side, offset, quantity, price)
        if status != "RESERVED":
            self.dispatch(key, status)

    # -- lifecycle ---------------------------------------------------------

    def upgrade(self):
        for step in UPGRADES:
            getattr(self.journal, step)(self.guard)
        self.ledger = GatewayDirectExecutionLedger(self.journal, self.guard)

    def restart(self, *, new_generation=False):
        """A Gateway restart: the producer and the journal object are gone, the file stays."""
        self.source.close()
        journal_id, generation = self.journal.journal_id, self.journal.generation
        if new_generation:
            self.guard.release()
            generation = self.guard.acquire()
        self.journal = GatewayDirectCommandJournal(
            self.path, "acct", generation=generation, expected_journal_id=journal_id, minimum_revision=0,
            clock=lambda: self.journal_clock[0], **FLAGS)
        self.ledger = GatewayDirectExecutionLedger(self.journal, self.guard)

    # -- returns -----------------------------------------------------------

    def _fact(self, key, **kwargs):
        self.refs += 1
        return fact(keys=(key + "-anchor", "venue-" + key), binding=(key + "-anchor",), venue="venue-" + key,
                    ref=f"source-{self.refs}", **kwargs)

    def order_state(self, key, status, filled="0"):
        result = self.ledger.ingest(replace(
            self._fact(key, kind=DirectExecutionEventKind.ORDER, status=status),
            order_quantity=self.quantities[key], filled_quantity=filled))
        assert result.disposition == "ATTRIBUTED", result
        self.accepted.add(key)

    def fill(self, key, side, offset, quantity, price, trade_id):
        """An accepted order (the venue key is learned from it), its trade and its final state."""
        if key not in self.accepted:
            self.order_state(key, DirectOrderStatus.ACCEPTED)
        result = self.ledger.ingest(self._fact(key, kind=DirectExecutionEventKind.TRADE, side=side, offset=offset,
                                               quantity=quantity, price=price, trade_key=("SHFE", trade_id)))
        assert result.disposition == "ATTRIBUTED" and result.applied, result
        if quantity == self.quantities[key]:
            self.order_state(key, DirectOrderStatus.FILLED, filled=quantity)

    # -- producer ----------------------------------------------------------

    def producer(self, budgets=None, adjustments=(), refresh_seconds=30, price_max_age_seconds=60):
        settings = CtpStrategyFinancialSettings(
            "CNY", RATES, budgets or {"s1": D("100000.00")}, tuple(adjustments),
            refresh_seconds=refresh_seconds, price_max_age_seconds=price_max_age_seconds)
        value = CtpGatewayStrategyFinancial(
            settings, journal=self.journal, guard=self.guard, returns_continuous=lambda: self.continuous[0],
            returns_discontinuity=lambda: None if self.continuous[0] else self.why[0],
            last_price=self.prices.last, clock=lambda: self.clock[0])
        self.source = value.source()
        self.source.start()
        return value

    def tick(self, price, symbol="rb2601"):
        self.prices.observe([_tick(symbol, price)])

    def step(self, seconds=1.0):
        self.clock[0] += seconds
        return self.source.maintain()

    def read(self, strategy="s1"):
        return self.journal.read_strategy_financial(strategy, "CNY", admission_fence=_admitted)

    def unavailable(self, strategy="s1"):
        with pytest.raises((DirectCommandJournalError, GatewayStrategyFinancialSourceError)) as raised:
            self.read(strategy)
        return str(raised.value)


@pytest.fixture
def world(tmp_path):
    value = World(tmp_path)
    try:
        yield value
    finally:
        value.guard.release()


@pytest.fixture
def reasons(caplog):
    """The reasons the producer logged for an unavailable strategy, in order."""
    caplog.set_level(logging.INFO, logger=module.__name__)

    def read(level=None):
        return [record.getMessage().split(": ", 1)[1] for record in caplog.records
                if ("is unavailable" in record.getMessage() or "not available yet" in record.getMessage())
                and level in (None, record.levelno)]

    return read


def test_flat_strategy_is_worth_its_budget_and_needs_no_price(world):
    world.upgrade()
    world.producer()
    snapshot = world.read()
    assert (snapshot.revision, snapshot.initial_budget, snapshot.adjustments) == (1, "100000.00", "0")
    assert (snapshot.value, snapshot.cash) == (D("100000.00"), D("100000.00"))
    assert snapshot.model == "futures_linear_v1" and snapshot.owner_generation == world.journal.generation
    # Time passing changes nothing for a flat strategy: no new snapshot, still served.
    for _ in range(3):
        world.step(40)
    assert world.read().revision == 1


def test_fills_prices_and_a_working_order_move_cash_and_value(world):
    world.order("s1", "o1", "buy", "open", quantity="2", price="3000")
    world.order("s1", "o2", "sell", "close", quantity="1", price="3100")
    world.order("s1", "o3", "buy", "open", quantity="1", price="2990")
    world.upgrade()
    world.tick(3050)
    world.producer()
    first = world.read()
    assert first.reservations == "8998.99"                      # 6000+6.00 and 2990+2.99; o2 closes
    assert (first.value, first.cash) == (D("100000.00"), D("91001.01"))
    world.fill("o1", "buy", "open", "2", "3000", "t1")
    assert world.unavailable() == UNAVAILABLE                   # computed before the fill
    world.step()
    opened = world.read()
    assert (opened.unrealized_pnl, opened.margin, opened.fees) == ("1000.00", "6100.00", "6.00")
    assert opened.reservations == "2992.99"                     # o1 is filled, o3 still works
    world.fill("o2", "sell", "close", "1", "3100", "t2")
    world.step()
    snapshot = world.read()
    assert (snapshot.realized_pnl, snapshot.unrealized_pnl, snapshot.fees) == ("1000.00", "500.00", "9.10")
    assert (snapshot.margin, snapshot.reservations) == ("3050.00", "2992.99")
    assert snapshot.value == D("101490.90")                     # 100000 + 1000 + 500 - 9.10
    assert snapshot.cash == D("95447.91")                       # value - 3050 - 2992.99
    # A cancelled working order frees its reservation.
    world.order_state("o3", DirectOrderStatus.CANCELLED)
    world.step()
    assert world.read().reservations == "0.00" and world.read().cash == D("98440.90")
    # A newer price is taken at the next refresh, not on every pass.
    revision = world.read().revision
    world.tick(3060)
    world.step(5)
    assert world.read().revision == revision
    world.step(30)
    refreshed = world.read()
    assert refreshed.revision == revision + 1 and refreshed.unrealized_pnl == "600.00"
    assert refreshed.previous_digest is not None
    # The same price again stores nothing new.
    world.tick(3060)
    world.step(30)
    assert world.read().revision == revision + 1


def test_a_partly_filled_working_order_reserves_only_what_is_left(world):
    world.order("s1", "o1", "buy", "open", quantity="3", price="3000")
    world.upgrade()
    world.tick(3000)
    world.producer()
    assert world.read().reservations == "9009.00"               # 3 x (3000 + 3.00)
    world.fill("o1", "buy", "open", "1", "3000", "t1")
    world.order_state("o1", DirectOrderStatus.PARTIALLY_FILLED, filled="1")
    world.step()
    snapshot = world.read()
    assert (snapshot.reservations, snapshot.margin) == ("6006.00", "3000.00")


def test_a_held_instrument_without_a_usable_price_is_unavailable(world, reasons):
    world.order("s1", "o1", "buy", "open")
    world.upgrade()
    world.fill("o1", "buy", "open", "1", "3000", "t1")
    world.producer()
    assert world.unavailable() == UNAVAILABLE                   # no price was ever seen
    world.tick(3010)
    world.step()
    assert world.unavailable() == UNAVAILABLE                   # a failure is retried every 5 s
    world.step(4)
    assert world.read().unrealized_pnl == "100.00"
    # Between two passes the age of the price counts, not the time of the computation.
    world.clock[0] += 55
    assert world.read().unrealized_pnl == "100.00"              # the price is 60 s old: still allowed
    world.clock[0] += 1
    assert world.unavailable() == UNAVAILABLE                   # 61 s: too old
    world.step(0)
    assert world.unavailable() == UNAVAILABLE                   # and no newer one exists
    world.tick(3020)
    world.step(5)
    assert world.read().unrealized_pnl == "200.00"
    assert reasons() == ["VALUATION_PRICE_UNAVAILABLE", "VALUATION_PRICE_UNAVAILABLE"]


def test_adjustments_are_summed_and_a_changed_initial_budget_is_refused(world, reasons):
    world.upgrade()
    world.producer(adjustments=[("adj-1", "s1", D("50000.00")), ("adj-2", "s1", D("-20000.00"))])
    snapshot = world.read()
    assert (snapshot.initial_budget, snapshot.adjustments, snapshot.value) == (
        "100000.00", "30000.00", D("130000.00"))
    # The chain started with 100000: another initial amount cannot replace it.
    world.restart()
    world.producer(budgets={"s1": D("150000.00")})
    assert world.unavailable() == UNAVAILABLE and reasons() == ["INITIAL_BUDGET_CHANGED"]
    # The same budget with another adjustment list is a new revision of the same chain.
    world.restart()
    world.producer(adjustments=[("adj-1", "s1", D("50000.00"))])
    latest = world.read()
    assert (latest.revision, latest.adjustments, latest.value) == (2, "50000.00", D("150000.00"))
    assert latest.previous_digest == snapshot.digest


def test_a_restart_continues_the_chain_under_the_new_owner_generation(world):
    world.order("s1", "o1", "buy", "open")
    world.upgrade()
    world.fill("o1", "buy", "open", "1", "3000", "t1")
    world.tick(3010)
    world.producer()
    before = world.read()
    world.restart(new_generation=True)
    assert world.unavailable() == UNAVAILABLE                   # nothing vouches for the old snapshot yet
    world.producer()
    after = world.read()
    assert after.owner_generation == world.journal.generation != before.owner_generation
    assert (after.revision, after.previous_digest) == (before.revision + 1, before.digest)
    assert (after.unrealized_pnl, after.fees, after.margin) == (before.unrealized_pnl, "3.00", "3010.00")


def test_only_strategies_with_a_budget_get_a_snapshot_and_each_has_its_own(world):
    world.order("s1", "o1", "buy", "open")
    world.order("s2", "p1", "sell", "open", price="3010")
    world.upgrade()
    world.fill("o1", "buy", "open", "1", "3000", "t1")
    world.fill("p1", "sell", "open", "1", "3010", "t2")
    world.tick(3005)
    world.producer(budgets={"s1": D("100000.00"), "s2": D("60000.00")},
                   adjustments=[("adj-1", "s2", D("1000.00"))])
    first, second = world.read("s1"), world.read("s2")
    assert (first.unrealized_pnl, first.margin, first.value) == ("50.00", "3005.00", D("100047.00"))
    assert (second.unrealized_pnl, second.margin) == ("50.00", "3606.00")   # short side, ratio 0.12
    assert (first.adjustments, second.adjustments) == ("0", "1000.00")      # an adjustment has one owner
    assert second.value == D("61046.99")                                    # 60000 + 1000 + 50 - 3.01
    assert world.unavailable("s3") == UNAVAILABLE                           # no budget, no snapshot
    # A new order of one strategy leaves the other one's snapshot as it is.
    world.order("s2", "p2", "sell", "open")
    assert world.read("s1") == first and world.unavailable("s2") == UNAVAILABLE


def test_returns_still_arriving_at_a_start_are_not_reported_as_an_interruption(world, reasons, caplog):
    world.upgrade()
    world.continuous[0], world.why[0] = False, "QUERY_PAIR_INCOMPLETE"
    world.producer()
    world.step()
    world.step()
    # Expected right after a start: one line per strategy at INFO, and no warning about the stream.
    assert reasons(logging.INFO) == ["RETURNS_NOT_CONTINUOUS"] and reasons(logging.WARNING) == []
    assert [record.getMessage() for record in caplog.records
            if record.getMessage().startswith("Return stream")] == []
    world.continuous[0] = True
    world.step()
    assert world.read().revision == 1


def test_incomplete_returns_or_an_unattributed_trade_make_the_strategy_unavailable(world, reasons, caplog):
    def lines(start):
        return [record.getMessage() for record in caplog.records if record.getMessage().startswith(start)]

    world.order("s1", "o1", "buy", "open")
    world.upgrade()
    world.fill("o1", "buy", "open", "1", "3000", "t1")
    world.tick(3000)
    world.producer()
    assert world.read().revision == 1
    assert lines("Strategy cash/value of s1 is available") == ["Strategy cash/value of s1 is available"]
    world.continuous[0] = False                                 # a gap in the return stream
    assert world.unavailable() == UNAVAILABLE
    world.step()
    assert world.unavailable() == UNAVAILABLE and reasons(logging.WARNING) == ["RETURNS_NOT_CONTINUOUS"]
    world.step()
    # The log names the condition behind it, once for the whole interruption (I63-E149).
    assert lines("Return stream") == [
        "Return stream is not continuous (CALLBACK_FAILED): no strategy cash/value until it is again"]
    world.continuous[0] = True
    world.step()
    assert world.read().revision == 1                           # nothing changed meanwhile
    # ... and says that the strategy is available again although nothing was recomputed for it;
    # the log used to leave it "unavailable" until its next change.
    assert len(lines("Strategy cash/value of s1 is available")) == 2
    world.continuous[0], world.why[0] = False, "SOURCE_GAP"     # a second interruption is named again
    world.step()
    assert lines("Return stream")[1:] == [
        "Return stream is not continuous (SOURCE_GAP): no strategy cash/value until it is again"]
    world.continuous[0] = True
    world.step()
    assert world.read().revision == 1 and len(lines("Strategy cash/value of s1 is available")) == 3
    # An order of somebody else on the instrument is not this strategy's money.
    other = world.ledger.ingest(fact(kind=DirectExecutionEventKind.ORDER, keys=("nobody", "venue-y"),
                                     binding=("nobody",), venue="venue-y", ref="source-other",
                                     status=DirectOrderStatus.ACCEPTED))
    assert other.disposition == "UNATTRIBUTED"
    world.step()
    assert world.read().revision == 1
    # A trade on the instrument that is attributed to nobody may be this strategy's.
    stray = world.ledger.ingest(fact(kind=DirectExecutionEventKind.TRADE, keys=("nobody", "venue-x"),
                                     binding=("nobody",), venue="venue-x", trade_key=("SHFE", "t9"),
                                     ref="source-stray"))
    assert stray.disposition == "UNATTRIBUTED"
    assert world.unavailable() == UNAVAILABLE                   # at once, before any new pass
    world.step()
    assert world.unavailable() == UNAVAILABLE
    assert reasons() == ["RETURNS_NOT_CONTINUOUS", "RETURNS_NOT_CONTINUOUS",      # the two interruptions above
                         "UNATTRIBUTED_TRADE_ON_INSTRUMENT"]


def test_only_commands_that_can_be_at_the_venue_reserve_cash(world):
    world.order("s1", "r1", "buy", "open", status="RESERVED")
    world.order("s1", "x1", "buy", "open", status="RESERVED")
    world.order("s1", "d1", "buy", "open", status="DISPATCHING")
    world.order("s1", "n1", "buy", "open", status="NOT_SENT")
    world.order("s1", "c1", "sell", "close", status="DISPATCHING")
    world.upgrade()
    producer = world.producer()
    assert world.read().reservations == "3003.00"               # d1 only
    watched = producer._strategies["s1"].watched
    assert len(watched) == 3                                    # r1, x1, d1: n1 was never sent, c1 closes
    # A reserved command that is dispatched later starts to count.
    world.dispatch("r1")
    assert world.unavailable() == UNAVAILABLE
    world.step()
    assert world.read().reservations == "6006.00"
    # A reservation that expires is forgotten.
    world.journal_clock[0] += 60
    world.journal.expire("s1", "x1", "principal")
    world.step()
    assert world.read().reservations == "6006.00" and len(watched) == 2
    # A return for the command that was believed not sent: it is at the venue after all.
    world.order_state("n1", DirectOrderStatus.ACCEPTED)
    world.step()
    assert world.read().reservations == "9009.00" and len(watched) == 3
    world.order_state("n1", DirectOrderStatus.CANCELLED)
    world.order_state("d1", DirectOrderStatus.REJECTED)
    world.step()
    assert world.read().reservations == "3003.00" and len(watched) == 1


def test_a_command_reserved_after_the_start_is_seen(world):
    world.upgrade()
    world.producer()
    assert world.read().reservations == "0.00"
    world.order("s1", "late", "sell", "open", price="3100")
    assert world.unavailable() == UNAVAILABLE
    world.step()
    assert world.read().reservations == "3723.10"               # 3100 x 10 x 0.12 + 3.10


def test_a_working_opening_order_without_a_limit_price_is_unavailable(world, reasons):
    world.order("s1", "m1", "buy", "open", price=None)
    world.upgrade()
    world.producer()
    assert world.unavailable() == UNAVAILABLE and reasons() == ["WORKING_ORDER_PRICE_UNAVAILABLE"]


def test_history_is_loaded_in_pages_after_a_start(world, reasons, monkeypatch):
    for index in range(5):
        world.order("s1", f"o{index}", "buy", "open", price=str(3000 + index))
    world.upgrade()
    for index in range(3):
        world.fill(f"o{index}", "buy", "open", "1", str(3000 + index), f"t{index}")
    world.tick(3001)
    monkeypatch.setattr(module, "_PAGE", 2)
    world.producer()
    passes = 1
    while world.source is not None and passes < 20:
        try:
            snapshot = world.read()
            break
        except DirectCommandJournalError:
            world.step()
            passes += 1
    # 5 commands and 9 returns at 2 per pass and kind: 3 passes for the commands, the third already
    # takes returns, and 4 more for the rest of them.
    assert passes == 7 and reasons(logging.INFO) == ["HISTORY_LOADING"] and reasons(logging.WARNING) == []
    assert (snapshot.revision, snapshot.fees, snapshot.margin) == (1, "9.00", "9003.00")
    assert snapshot.unrealized_pnl == "0.00"                    # average 3001 at price 3001
    assert snapshot.reservations == "6013.01"                   # 3003 + 3004 and their fees 3.00 + 3.00


def test_a_snapshot_that_was_not_stored_is_produced_again(world, monkeypatch):
    world.upgrade()
    original, calls = GatewayDirectCommandJournal.append_strategy_financial, []

    def failing_once(self, snapshot, *, guard):
        calls.append(snapshot.revision)
        if len(calls) == 1:
            raise DirectCommandJournalError("STORAGE_FAILED")
        return original(self, snapshot, guard=guard)

    monkeypatch.setattr(GatewayDirectCommandJournal, "append_strategy_financial", failing_once)
    world.producer()
    assert world.unavailable() == "STRATEGY_FINANCIAL_SOURCE_UNAVAILABLE"
    assert world.step() is True and world.read().revision == 1 and calls == [1, 1]


def test_a_history_that_does_not_add_up_stays_unavailable(world, reasons):
    world.order("s1", "c1", "sell", "close")
    world.upgrade()
    world.tick(3000)
    world.producer()
    assert world.read().value == D("100000.00")
    # The journal holds a close of a position that none of the strategy's own trades opened.
    world.fill("c1", "sell", "close", "1", "3000", "t1")
    for _ in range(3):
        world.step(6)
        assert world.unavailable() == UNAVAILABLE
    world.order("s1", "o2", "buy", "open")
    world.step(6)
    assert world.unavailable() == UNAVAILABLE
    assert reasons() == ["POSITION_UNDERFLOW"]                  # reported once, not on every retry


def test_a_trade_that_is_reported_again_with_more_detail_is_counted_once(world):
    world.order("s1", "o1", "buy", "open")
    world.upgrade()
    world.tick(3000)
    world.producer()
    world.fill("o1", "buy", "open", "1", "3000", "t1")
    world.step()
    assert (world.read().fees, world.read().margin) == ("3.00", "3000.00")
    richer = world.ledger.ingest(replace(
        world._fact("o1", kind=DirectExecutionEventKind.TRADE, side="buy", offset="open", quantity="1",
                    price="3000", trade_key=("SHFE", "t1")), schema_version=4, trade_date="20300101"))
    assert (richer.disposition, richer.reason, richer.applied) == ("ATTRIBUTED", "TRADE_DATE_ENRICHED", False)
    assert richer.event_sequence is not None                    # it is in the strategy's stream
    world.step()
    assert (world.read().fees, world.read().margin) == ("3.00", "3000.00")


def test_a_trade_of_a_command_that_was_recorded_as_not_sent_gives_no_figure(world, reasons):
    world.order("s1", "n1", "buy", "open", status="NOT_SENT")
    world.upgrade()
    world.tick(3000)
    world.producer()
    assert world.read().reservations == "0.00"
    world.order_state("n1", DirectOrderStatus.ACCEPTED)
    result = world.ledger.ingest(world._fact("n1", kind=DirectExecutionEventKind.TRADE, side="buy",
                                             offset="open", quantity="1", price="3000", trade_key=("SHFE", "t1")))
    assert (result.disposition, result.reason, result.applied) == ("ATTRIBUTED", "NOT_SENT_CONTRADICTION", True)
    world.step()
    assert world.unavailable() == UNAVAILABLE and reasons() == ["TRADE_ATTRIBUTION_NOT_MATCHED"]


def test_a_changed_snapshot_that_was_not_stored_does_not_let_the_old_one_through(world, monkeypatch):
    world.upgrade()
    world.producer(budgets={"s1": D("100000.00"), "s2": D("60000.00")})
    assert world.read("s2").revision == 1
    world.order("s1", "o1", "buy", "open")
    world.order("s2", "p1", "buy", "open")
    original, calls = GatewayDirectCommandJournal.append_strategy_financial, []

    def failing_once(self, snapshot, *, guard):
        calls.append(snapshot.strategy)
        if len(calls) == 1:
            raise DirectCommandJournalError("STORAGE_FAILED")
        return original(self, snapshot, guard=guard)

    monkeypatch.setattr(GatewayDirectCommandJournal, "append_strategy_financial", failing_once)
    assert world.step() is False and calls == ["s1"]            # s2's new snapshot was not stored either
    # A pass that follows at once reads nothing (at most one pass per second) and reports success;
    # the stored snapshot of s2 is the old one, without the reservation, and must not be served.
    assert world.source.maintain() is True and calls == ["s1"]
    assert world.unavailable("s2") == UNAVAILABLE and world.unavailable("s1") == UNAVAILABLE
    assert world.step() is True and calls == ["s1", "s1", "s2"]
    assert (world.read("s1").revision, world.read("s2").reservations) == (2, "3003.00")


def test_commands_that_are_not_only_appended_give_no_figure(world, reasons):
    world.upgrade()
    producer = world.producer()
    # As if the row ids of the command table had been renumbered: a new command is not seen as new.
    producer._strategies["s1"].command_rowid += 10
    world.order("s1", "late", "buy", "open")
    world.step()
    assert world.unavailable() == UNAVAILABLE and reasons() == ["COMMAND_HISTORY_INCONSISTENT"]


def test_last_prices_keep_only_usable_values():
    clock = [5.0]
    prices = OwnerLastPrices(clock=lambda: clock[0])
    prices.observe([_tick("rb2601", 3000.5),
                    _tick("rb2601", 0), _tick("rb2601", "nan"), _tick("rb2601", "abc"),
                    _tick("rb2601", 1.7976931348623157e308), _tick("rb2601", True), _tick("", 1),
                    _tick("rb2601", 9, schema="sdk.market.bar"),          # not a tick
                    _tick("rb2601", 9, instrument="rb2601"),              # not an instrument mapping
                    _tick("hc2601", None), SimpleNamespace(data=None), object()])
    assert prices.last("rb2601") == (D("3000.5"), 5.0) and prices.last("hc2601") is None
    clock[0] = 9.0
    prices.observe([_tick("rb2601", "3001")])
    assert prices.last("rb2601") == (D("3001"), 9.0)


@pytest.mark.parametrize("raw", [
    {"budgets": {"s1": "1.00"}},
    {"currency": "USD", "budgets": {"s1": "1.00"}},
    {"currency": "CNY", "budgets": {}},
    {"currency": "CNY", "budgets": {"s1": "-1.00"}},
    {"currency": "CNY", "budgets": {"s1": "1.001"}},
    {"currency": "CNY", "budgets": {"s1": 1.5}},
    {"currency": "CNY", "budgets": {"s1": "abc"}},
    {"currency": "CNY", "budgets": {"s1": "10000000000000000.00"}},
    {"currency": "CNY", "budgets": {"s1": "1.00"}, "adjustments": [{"id": "a", "strategy": "s2", "amount": "1.00"}]},
    {"currency": "CNY", "budgets": {"s1": "1.00"}, "adjustments": [{"id": "a", "strategy": "s1", "amount": "0"}]},
    {"currency": "CNY", "budgets": {"s1": "1.00"},
     "adjustments": [{"id": "a", "strategy": "s1", "amount": "1.00"}, {"id": "a", "strategy": "s1", "amount": "2.00"}]},
    {"currency": "CNY", "budgets": {"s1": "1.00"}, "adjustments": [{"id": "a", "strategy": "s1"}]},
    {"currency": "CNY", "budgets": {"s1": "1.00"}, "refresh_seconds": 0},
    {"currency": "CNY", "budgets": {"s1": "1.00"}, "refresh_seconds": 30, "price_max_age_seconds": 10},
    {"currency": "CNY", "budgets": {"s1": "1.00"}, "default_budget": "1.00"},
    ["s1"],
])
def test_bundle_settings_are_strict_and_have_no_default_budget(raw):
    with pytest.raises(CtpGatewayStrategyFinancialError, match="^INVALID_STRATEGY_FINANCIAL_SETTINGS$"):
        CtpStrategyFinancialSettings.from_bundle(raw, rates=RATES)


def test_bundle_settings_are_parsed():
    settings = CtpStrategyFinancialSettings.from_bundle(
        {"currency": "CNY", "budgets": {"s1": "600000.00", "s2": 500000},
         "adjustments": [{"id": "2026-11-12-a", "strategy": "s1", "amount": "-100000.00"}],
         "refresh_seconds": 10, "price_max_age_seconds": 20}, rates=RATES)
    assert dict(settings.budgets) == {"s1": D("600000.00"), "s2": D("500000")}
    assert settings.adjustments == (("2026-11-12-a", "s1", D("-100000.00")),)
    assert (settings.refresh_seconds, settings.price_max_age_seconds) == (10, 20)
    defaults = CtpStrategyFinancialSettings.from_bundle({"currency": "CNY", "budgets": {"s1": "1.00"}}, rates=RATES)
    assert (defaults.refresh_seconds, defaults.price_max_age_seconds, defaults.adjustments) == (60.0, 180.0, ())
