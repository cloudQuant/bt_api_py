"""Returns that belong to no known order freeze their contract for every strategy (D63-59)."""
import logging
import threading

import pytest
from bt_api_base.direct_write_protocol import DirectExecutionEventKind, DirectOrderStatus
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    GatewayDirectCommandJournal,
)
from bt_api_gateway.direct_control import GatewayDirectControl
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from test_iter63_direct_execution_journal import fact

import bt_api_py.gateway_instrument_block as module
from bt_api_py.gateway_instrument_block import (
    GatewayInstrumentBlock,
    contract_has_undecided_orders,
    unresolved_return_origin,
)

HC = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "hc2601", "hc2601")
RB_SCOPE, HC_SCOPE = "SHFE:rb2601", "SHFE:hc2601"


def _order(symbol, offset="open"):
    return {"symbol": symbol, "exchange_id": "SHFE", "side": "buy" if offset == "open" else "sell",
            "offset": offset, "quantity": "1", "price": "3000"}


class World:
    def __init__(self, tmp_path):
        directory = tmp_path / "guards"
        directory.mkdir()
        self.guard = LocalAccountSessionGuard(directory, "acct", "owner")
        self.path = tmp_path / "db"
        self.journal = GatewayDirectCommandJournal.create(self.path, "acct", generation=self.guard.acquire(),
                                                          clock=lambda: 100.0)
        self.control = self.journal.enable_controls(self.guard)
        self.ledger = GatewayDirectExecutionLedger(self.journal, self.guard)
        self.clock, self.refs, self.keys = [10.0], 0, 0
        self.watcher = self.new_watcher()

    def new_watcher(self):
        return GatewayInstrumentBlock(journal=self.journal, control=self.control, clock=lambda: self.clock[0])

    def restart(self):
        self.journal = GatewayDirectCommandJournal(
            self.path, "acct", generation=self.journal.generation, expected_journal_id=self.journal.journal_id,
            minimum_revision=0, clock=lambda: 100.0, controls_enabled=True)
        self.control = GatewayDirectControl(self.journal, self.guard)
        self.ledger = GatewayDirectExecutionLedger(self.journal, self.guard)
        self.watcher = self.new_watcher()

    def stray(self, kind=DirectExecutionEventKind.TRADE, **kwargs):
        """A return whose identity matches no command of this journal."""
        self.refs += 1
        name = f"nobody-{self.refs}"
        result = self.ledger.ingest(fact(kind=kind, keys=(name, "venue-" + name), binding=(name,),
                                         venue="venue-" + name, trade_key=("SHFE", name), ref="source-" + name,
                                         status=DirectOrderStatus.ACCEPTED if kind is DirectExecutionEventKind.ORDER
                                         else None, **kwargs))
        assert result.disposition == "UNATTRIBUTED", result
        return result

    def step(self, seconds=1.0):
        self.clock[0] += seconds
        self.watcher.maintain()

    def reserve(self, strategy, symbol, offset="open"):
        self.keys += 1
        return self.journal.reserve(strategy, f"key-{self.keys}", "principal", _order(symbol, offset))

    def refused(self, strategy, symbol, offset="open"):
        with pytest.raises(DirectCommandJournalError) as raised:
            self.reserve(strategy, symbol, offset)
        return raised.value.code

    def causes(self, active=None):
        rows = self.control.list_causes(scope_kind="instrument")
        return [row for row in rows if active is None or bool(row["active"]) is active]


@pytest.fixture
def world(tmp_path):
    value = World(tmp_path)
    try:
        yield value
    finally:
        value.guard.release()


@pytest.fixture
def logged(caplog):
    caplog.set_level(logging.INFO, logger=module.__name__)
    return lambda level: [record.getMessage() for record in caplog.records if record.levelno == level]


@pytest.mark.parametrize("kind", [DirectExecutionEventKind.TRADE, DirectExecutionEventKind.ORDER])
def test_an_unattributed_return_blocks_its_contract_for_every_strategy_and_nothing_else(world, logged, kind):
    world.step()
    assert world.causes() == [] and world.reserve("s1", "rb2601").status == "RESERVED"
    world.stray(kind)
    assert world.reserve("s1", "rb2601").status == "RESERVED"             # not seen before the next pass
    world.step()
    cause, = world.causes()
    assert (cause["scope_value"], cause["mode"], cause["active"], cause["origin"]) == (
        RB_SCOPE, "FREEZE", 1, unresolved_return_origin("acct"))
    assert cause["cause_id"].startswith("unresolved-return:" + RB_SCOPE + ":")
    for strategy in ("s1", "s2"):
        for offset in ("open", "close"):
            assert world.refused(strategy, "rb2601", offset) == "CONTROL_FREEZE"
    assert world.reserve("s1", "hc2601").status == "RESERVED"             # other contracts go on
    assert world.reserve("s2", "hc2601", "close").status == "RESERVED"
    warnings = logged(logging.WARNING)
    assert len(warnings) == 1 and RB_SCOPE in warnings[0] and cause["cause_id"] in warnings[0]
    assert "1 return(s)" in warnings[0]
    for _ in range(3):                                                    # nothing new: no new cause, no new line
        world.step()
    assert len(world.causes()) == 1 and len(logged(logging.WARNING)) == 1


def test_each_contract_has_its_own_block(world, logged):
    world.stray()
    world.stray(instrument=HC)
    world.step()
    assert sorted(row["scope_value"] for row in world.causes(active=True)) == [HC_SCOPE, RB_SCOPE]
    assert world.refused("s1", "rb2601") == world.refused("s1", "hc2601") == "CONTROL_FREEZE"
    assert len(logged(logging.WARNING)) == 2


def test_a_return_that_is_attributed_after_all_lifts_the_block_by_itself(world, logged):
    row = world.reserve("s1", "rb2601")
    native = {"exchange_name": "CTP", "client_order_id": "o1", "session_generation": 1, "fields_json": "{}"}
    world.journal.begin_dispatch("s1", "key-1", row.reservation_id, row.digest, "principal", 1, native, ("anchor",))
    world.journal.finish("s1", "key-1", row.reservation_id, row.digest, "principal", 2, native, ("anchor",), "SENT")
    # The trade arrives before the order return that tells which venue order it belongs to.
    early = fact(keys=("anchor", "venue-o1"), binding=("anchor",), venue="venue-o1", ref="trade-1",
                 trade_key=("SHFE", "t1"))
    assert world.ledger.ingest(early).disposition == "UNATTRIBUTED"
    world.step()
    assert world.refused("s2", "rb2601") == "CONTROL_FREEZE"
    cause, = world.causes(active=True)
    world.ledger.ingest(fact(kind=DirectExecutionEventKind.ORDER, keys=("anchor", "venue-o1"), binding=("anchor",),
                             venue="venue-o1", ref="order-1", status=DirectOrderStatus.ACCEPTED))
    # The Gateway's return maintenance looks at unattributed returns again.
    assert [item.disposition for item in world.ledger.reconcile_pending()] == ["ATTRIBUTED"]
    world.step()
    assert world.causes(active=True) == [] and world.reserve("s2", "rb2601").status == "RESERVED"
    released, = world.causes(active=False)
    assert released["resume_id"] == "resolved:" + cause["cause_id"]
    assert any(RB_SCOPE in line and "no longer blocked" in line for line in logged(logging.INFO))


def test_an_operator_release_holds_for_those_returns_and_a_new_one_blocks_again(world, logged):
    world.stray()
    world.step()
    cause, = world.causes(active=True)
    world.control.resume(cause["cause_id"], expected_version=1, operation_id="operator-release-1")
    assert world.reserve("s1", "rb2601").status == "RESERVED"
    for _ in range(3):                                                    # the same returns: not blocked again
        world.step()
    assert world.causes(active=True) == [] and world.reserve("s1", "rb2601").status == "RESERVED"
    assert len(logged(logging.WARNING)) == 1
    # After a restart the released cause is still the answer for the same returns.
    world.restart()
    world.step()
    assert world.causes(active=True) == [] and world.reserve("s1", "rb2601").status == "RESERVED"
    # One more unattributed return on the contract is a new, unreleased situation.
    world.stray()
    world.step()
    second, = world.causes(active=True)
    assert second["cause_id"] != cause["cause_id"] and world.refused("s1", "rb2601") == "CONTROL_FREEZE"
    assert "2 return(s)" in logged(logging.WARNING)[-1]


def test_a_block_survives_a_restart_and_is_not_announced_twice(world, logged):
    world.stray()
    world.step()
    world.restart()
    assert world.refused("s1", "rb2601") == "CONTROL_FREEZE"              # stored in the journal
    world.step()
    assert len(world.causes(active=True)) == 1 and len(logged(logging.WARNING)) == 1


def test_passes_are_spaced_stay_on_the_owner_thread_and_never_raise(world, logged, monkeypatch):
    world.step()
    world.stray()
    world.watcher.maintain()                                              # within the interval: nothing read
    assert world.causes() == []
    thread = threading.Thread(target=lambda: (world.clock.__setitem__(0, world.clock[0] + 5),
                                              world.watcher.maintain()))
    thread.start()
    thread.join(5)
    assert world.causes() == []                                           # another thread decides nothing
    original = world.watcher._unresolved
    monkeypatch.setattr(world.watcher, "_unresolved", lambda: (_ for _ in ()).throw(RuntimeError("READ_FAILED")))
    world.step()
    world.step()
    assert world.causes() == []
    errors = logged(logging.ERROR)
    assert len(errors) == 1 and errors[0].endswith("READ_FAILED")         # once, not per pass
    monkeypatch.setattr(world.watcher, "_unresolved", original)
    world.step()
    assert len(world.causes(active=True)) == 1


def test_undecided_orders_on_a_contract_are_found(world):
    def undecided(scope):
        with world.journal._transaction() as db:
            return contract_has_undecided_orders(db, "acct", scope)

    row = world.reserve("s1", "rb2601")
    assert undecided(RB_SCOPE) is False                                   # reserved only: nothing went out
    native = {"exchange_name": "CTP", "client_order_id": "o1", "session_generation": 1, "fields_json": "{}"}
    world.journal.begin_dispatch("s1", "key-1", row.reservation_id, row.digest, "principal", 1, native, ("anchor",))
    assert undecided(RB_SCOPE) is True and undecided(HC_SCOPE) is False   # dispatching: outcome not known
    world.journal.finish("s1", "key-1", row.reservation_id, row.digest, "principal", 2, native, ("anchor",), "SENT")
    assert undecided(RB_SCOPE) is False                                   # sent: its returns are attributable


def test_the_watcher_needs_the_control_of_its_own_journal(world, tmp_path):
    for kwargs in ({"journal": object(), "control": world.control}, {"journal": world.journal, "control": object()},
                   {"journal": world.journal, "control": world.control, "interval_seconds": 0},
                   {"journal": world.journal, "control": world.control, "clock": 1}):
        with pytest.raises(ValueError, match="^INVALID_GATEWAY_INSTRUMENT_BLOCK$"):
            GatewayInstrumentBlock(**kwargs)
    assert world.watcher.origin == unresolved_return_origin("acct") != unresolved_return_origin("other")
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = World(other_root)
    try:
        # A control that belongs to another journal would block and release in the wrong place.
        with pytest.raises(ValueError, match="^INVALID_GATEWAY_INSTRUMENT_BLOCK$"):
            GatewayInstrumentBlock(journal=world.journal, control=other.control)
    finally:
        other.guard.release()
