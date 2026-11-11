"""After an operator releases a blocked contract, its unattributed trades count as external (D63-59).

The strategies' own positions and close capacity on the contract are available again and the
account comparison explains the account as strategies plus external.
"""
import logging
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import (
    DirectExecutionEventKind,
    DirectOrderStatus,
    NativeOrderIdentity,
)
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_gateway.direct_command_journal import DirectCommandJournalError
from bt_api_gateway.direct_control import GatewayDirectControl
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger

import bt_api_py.gateway_instrument_block as block_module
from bt_api_py.gateway_instrument_block import (
    GatewayInstrumentBlock,
    operator_release_ref,
    released_by_operator,
)
from tests.unit.test_iter63_ctp_gateway_strategy_ledger import (  # noqa: F401  (env is a fixture)
    D1,
    RB,
    ROUTE,
    SA,
    account_row,
    compare,
    comparing,
    env,
    order,
    trade_before_ledger,
)

CLOSE = order("sell", "close_yesterday")


def manual_trade(site, name="manual-1", instrument=RB, side="buy", kind=DirectExecutionEventKind.TRADE):
    """A return of an order placed outside the Gateway: its identity matches no command."""
    common = {"identity": NativeOrderIdentity(ROUTE, name, {"trading_day": D1}, 3),
              "identity_keys": (name,), "binding_keys": (name,), "venue_key": name, "side": side,
              "offset": "open"}
    if kind is DirectExecutionEventKind.TRADE:
        fact = GatewayReturnFact("acct", ROUTE, instrument, D1, kind, "source-" + name,
                                 trade_key=("X", name), trade_quantity="1", trade_price="10", **common)
    else:
        fact = GatewayReturnFact("acct", ROUTE, instrument, D1, kind, "source-" + name,
                                 status=DirectOrderStatus.ACCEPTED, order_quantity="1", filled_quantity="0",
                                 remaining_quantity="1", **common)
    result = GatewayDirectExecutionLedger(site.journal, site.guard).ingest(fact)
    assert result.disposition == "UNATTRIBUTED", result
    return fact


@pytest.fixture
def world(env):  # noqa: F811  (the imported fixture)
    site = env
    """strategy-b holds one lot of rb from recorded trades; the account shows it; the ledger compares."""
    trade_before_ledger(site)
    site.session.positions = (account_row(RB, "long", "1"),)
    ledger = comparing(site)
    assert compare(site) == ("MATCH", 1, 0, 1, 0)
    control = GatewayDirectControl(site.journal, site.guard)
    clock = [10.0]
    watcher = GatewayInstrumentBlock(journal=site.journal, control=control, clock=lambda: clock[0],
                                     record_released=ledger.record_released)

    def step():
        clock[0] += 1.0
        watcher.maintain()

    def new_cut(*rows):
        """The account moved and the ledger takes a new comparison cut."""
        site.session.positions = rows
        site.session.return_buffer.last_sequence += 1
        ledger._comparison_next = 0.0
        ledger.maintain()

    return SimpleNamespace(env=site, ledger=ledger, control=control, watcher=watcher, step=step, new_cut=new_cut)


def _capacity(site):
    return site.journal.read_strategy_close_capacity("strategy-b", CLOSE, expected_instrument=RB).available


def _unavailable(site):
    with pytest.raises(DirectCommandJournalError) as raised:
        _capacity(site)
    return raised.value.code


def test_release_makes_the_strategies_ledger_whole_again_and_the_comparison_counts_the_trade(world, caplog):
    site = world.env
    caplog.set_level(logging.INFO, logger=block_module.__name__)
    assert _capacity(site) == 1
    manual_trade(site)
    # An unattributed trade on rb: nobody's position on rb is known to be complete.
    assert _unavailable(site) == "STRATEGY_LEDGER_UNAVAILABLE"
    world.step()
    cause, = world.control.list_causes(scope_kind="instrument")
    with pytest.raises(DirectCommandJournalError, match="^CONTROL_FREEZE$"):
        site.journal.reserve("strategy-a", "open-1", "p", order())
    world.new_cut(account_row(RB, "long", "2"))
    assert compare(site) == ("UNAVAILABLE", None, None, None, None)
    # The operator checked the trade (a manual order) and releases the contract.
    released = world.control.resume(cause["cause_id"], expected_version=1, operation_id="operator-release-1")
    assert site.journal.reserve("strategy-a", "open-1", "p", order()).status == "RESERVED"
    assert _unavailable(site) == "STRATEGY_LEDGER_UNAVAILABLE"            # not recorded as external yet
    world.step()
    assert _capacity(site) == 1                                           # strategy-b can close its own lot
    assert site.journal.reserve("strategy-b", "close-1", "p", CLOSE).status == "RESERVED"
    with pytest.raises(DirectCommandJournalError, match="^STRATEGY_CLOSE_EXCEEDS$"):
        site.journal.reserve("strategy-a", "close-2", "p", CLOSE)         # the manual lot is nobody's to close
    world.new_cut(account_row(RB, "long", "2"))
    assert compare(site) == ("MATCH", 1, 1, 2, 0)                         # one lot strategy-b, one external
    assert compare(site, SA) == ("MATCH", 0, 0, 0, 0)
    assert any("1 released trade(s) recorded as external" in record.getMessage() for record in caplog.records)
    with site.journal._transaction() as db:
        stored = db.execute("SELECT receipt_json FROM external_position_events").fetchall()
    assert len(stored) == 1 and operator_release_ref(released) in stored[0][0]
    # Passing again records nothing twice and changes nothing.
    world.step()
    world.step()
    with site.journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM external_position_events").fetchone()[0] == 1
    assert _capacity(site) == 0                                           # the close reserved above


def test_one_more_unattributed_trade_blocks_again_and_is_not_external(world, caplog):
    site = world.env
    caplog.set_level(logging.WARNING, logger=block_module.__name__)
    manual_trade(site)
    world.step()
    first, = world.control.list_causes(scope_kind="instrument")
    world.control.resume(first["cause_id"], expected_version=1, operation_id="operator-release-1")
    world.step()
    assert _capacity(site) == 1
    manual_trade(site, "manual-2")
    assert _unavailable(site) == "STRATEGY_LEDGER_UNAVAILABLE"            # the new trade is not vouched for
    world.step()
    active = [row for row in world.control.list_causes(scope_kind="instrument") if row["active"]]
    assert len(active) == 1 and active[0]["cause_id"] != first["cause_id"]
    with site.journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM external_position_events").fetchone()[0] == 1
    # Releasing the new cause covers both trades; only the new one still needs a record.
    world.control.resume(active[0]["cause_id"], expected_version=1, operation_id="operator-release-2")
    world.step()
    assert _capacity(site) == 1
    world.new_cut(account_row(RB, "long", "3"))
    assert compare(site) == ("MATCH", 1, 2, 3, 0)
    # The trade that was already recorded was left alone: nothing was refused.
    assert not [record for record in caplog.records if "could not be recorded" in record.getMessage()]


def test_an_external_record_that_no_release_stands_behind_is_not_counted(world):
    """Only an operator's release vouches; a record made any other way leaves the comparison open."""
    from contextlib import contextmanager

    from bt_api_gateway.external_position_ledger import ExternalFactEvidence

    site = world.env
    fact = manual_trade(site)
    evidence = ExternalFactEvidence("acct", site.journal.journal_id, site.journal.generation, 3, ROUTE, D1,
                                    fact.fact_digest(), fact.source_ref, "some-cut", "some-classifier")

    @contextmanager
    def fence():
        yield

    assert site.journal.record_verified_external_fact(
        fact.fact_digest(), evidence, guard=site.guard, source_fence=fence,
        verify_source_and_exclusion=lambda *_: True)
    assert _unavailable(site) == "EXTERNAL_LEDGER_UNAVAILABLE"           # the ledger does not vouch for it
    world.new_cut(account_row(RB, "long", "2"))
    assert compare(site) == ("UNAVAILABLE", None, None, None, None)


def test_an_unattributed_order_needs_no_record_and_a_release_is_enough(world):
    site = world.env
    manual_trade(site, kind=DirectExecutionEventKind.ORDER)
    assert _capacity(site) == 1                                           # an order moves no position
    world.step()
    cause, = world.control.list_causes(scope_kind="instrument")
    world.control.resume(cause["cause_id"], expected_version=1, operation_id="operator-release-1")
    world.step()
    with site.journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM external_position_events").fetchone()[0] == 0
    assert _capacity(site) == 1


def test_a_record_is_vouched_for_only_by_an_operator_release_of_that_contract(world):
    site = world.env
    fact = manual_trade(site)
    world.step()
    cause, = world.control.list_causes(scope_kind="instrument")

    def vouched(row, **changes):
        evidence = SimpleNamespace(exclusion_ref=operator_release_ref(row), account_scope="acct",
                                   fact_digest=fact.fact_digest())
        for name, value in changes.items():
            setattr(evidence, name, value)
        with site.journal._transaction() as db:
            return released_by_operator(db, "acct", fact, evidence)

    assert vouched(dict(cause, resume_id="operator-release-1")) is False   # still active: nobody released it
    released = world.control.resume(cause["cause_id"], expected_version=1, operation_id="operator-release-1")
    assert vouched(released) is True
    assert vouched(released, exclusion_ref="operator-release:" + cause["cause_id"]) is False
    assert vouched(released, exclusion_ref=operator_release_ref(dict(released, resume_id="other"))) is False
    assert vouched(released, exclusion_ref=operator_release_ref(dict(released, cause_id="absent"))) is False
    assert vouched(released, exclusion_ref=None) is False
    assert vouched(released, account_scope="other") is False
    assert vouched(released, fact_digest="0" * 64) is False
    other = manual_trade(site, "manual-sa", instrument=SA)                 # a trade on another contract
    with site.journal._transaction() as db:
        assert released_by_operator(db, "acct", other, SimpleNamespace(
            exclusion_ref=operator_release_ref(released), account_scope="acct",
            fact_digest=other.fact_digest())) is False
    # A cause the watcher lifted itself, or an operator's own account cause, vouches for nothing.
    world.control.apply("account-freeze", "FREEZE", scope_kind="account", origin="operator:x")
    mine = world.control.resume("account-freeze", expected_version=1, operation_id="operator-release-2")
    assert vouched(mine) is False
    foreign = world.control.apply("unresolved-return:SHFE:rb2701:feed", "FREEZE", scope_kind="instrument",
                                  scope_value="SHFE:rb2701", origin="operator:x")
    foreign = world.control.resume(foreign["cause_id"], expected_version=1, operation_id="operator-release-3")
    assert vouched(foreign) is False                                      # not a cause of the watcher
    lifted = world.control.apply("unresolved-return:SHFE:rb2701:beef", "FREEZE", scope_kind="instrument",
                                 scope_value="SHFE:rb2701", origin=world.watcher.origin)
    lifted = world.control.resume(lifted["cause_id"], expected_version=1, operation_id="resolved:" + lifted["cause_id"])
    assert vouched(lifted) is False


def test_recording_is_refused_while_an_own_order_on_the_contract_is_undecided(world, caplog):
    site = world.env
    caplog.set_level(logging.INFO, logger=block_module.__name__)
    manual_trade(site)
    world.step()
    cause, = world.control.list_causes(scope_kind="instrument")
    # Released at the journal level; then an own order on rb goes out and has no outcome yet.
    world.control.resume(cause["cause_id"], expected_version=1, operation_id="operator-release-1")
    row = site.journal.reserve("strategy-a", "going-out", "p", order())
    site.journal.begin_dispatch("strategy-a", "going-out", row.reservation_id, row.digest, "p", 1,
                               {"exchange_name": ROUTE, "client_order_id": "going-out", "session_generation": 1,
                                "fields_json": "{}"}, ("going-out-anchor",))
    world.step()
    world.step()
    with site.journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM external_position_events").fetchone()[0] == 0
    assert _unavailable(site) == "STRATEGY_LEDGER_UNAVAILABLE"
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING
                and "could not be recorded as external" in record.getMessage()]
    assert len(warnings) == 1 and "EXTERNAL_LEDGER_UNAVAILABLE" in warnings[0]   # once, with the reason


def test_recording_runs_on_the_owner_thread_only(world):
    import threading

    from bt_api_py.ctp_gateway_strategy_ledger import CtpGatewayStrategyLedgerError

    errors = []

    def other():
        try:
            world.ledger.record_released({"cause_id": "c", "resume_id": "r", "scope_value": "SHFE:rb2701"}, [])
        except CtpGatewayStrategyLedgerError as error:
            errors.append(error.code)

    thread = threading.Thread(target=other)
    thread.start()
    thread.join(5)
    assert errors == ["OWNER_REQUIRED"]


def test_strategy_cash_is_withheld_for_an_unattributed_trade_and_returns_after_the_release(world):
    """The cash producer treats a trade recorded as external as settled (D63-59 with D63-53)."""
    from contextlib import contextmanager
    from decimal import Decimal

    from bt_api_py.ctp_gateway_strategy_financial import (
        CtpGatewayStrategyFinancial,
        CtpStrategyFinancialSettings,
    )
    from bt_api_py.strategy_rates import parse_strategy_rates

    site = world.env
    for step in ("enable_parent_authority", "enable_account_facts_financial"):
        getattr(site.journal, step)(site.guard)
    rates = parse_strategy_rates(b"""schema_version: 1
currency: CNY
products:
  rb:
    multiplier: 10
    margin_ratio: {long: "0.10", short: "0.10"}
    commission:
      open: {per_lot: "0", ratio: "0"}
      close: {per_lot: "0", ratio: "0"}
      close_today: {per_lot: "0", ratio: "0"}
""")
    clock = [100.0]
    producer = CtpGatewayStrategyFinancial(
        CtpStrategyFinancialSettings("CNY", rates, {"strategy-b": Decimal("1000.00")}),
        journal=site.journal, guard=site.guard, returns_continuous=lambda: True,
        last_price=lambda symbol: (Decimal("12"), clock[0]), clock=lambda: clock[0])
    source = producer.source()
    source.start()

    @contextmanager
    def admitted():
        yield lambda: None

    def value():
        clock[0] += 6.0
        source.maintain()
        return site.journal.read_strategy_financial("strategy-b", "CNY", admission_fence=admitted).value

    assert value() == Decimal("1020.00")                                 # 1000 + (12 - 10) x 1 x 10
    manual_trade(site)
    with pytest.raises(DirectCommandJournalError, match="^STRATEGY_FINANCIAL_UNAVAILABLE$"):
        value()                                                          # the trade may be this strategy's
    world.step()
    cause, = world.control.list_causes(scope_kind="instrument")
    world.control.resume(cause["cause_id"], expected_version=1, operation_id="operator-release-1")
    with pytest.raises(DirectCommandJournalError, match="^STRATEGY_FINANCIAL_UNAVAILABLE$"):
        value()                                                          # released, not recorded yet
    world.step()
    assert value() == Decimal("1020.00")                                 # settled as external: the figure is back
