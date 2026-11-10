"""Owner ports and baseline of the CTP Gateway strategy ledger (I63-E111).

Synthetic session, provider and return source over a real Gateway journal; no
CTP, account or network.
"""
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import (
    DirectExecutionEventKind,
    DirectOrderStatus,
    NativeOrderIdentity,
)
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_ctp.close_offset_policy import split_close_required
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    GatewayDirectCommandJournal,
)
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.ctp_gateway_strategy_ledger import (
    CtpGatewayStrategyLedger,
    CtpGatewayStrategyLedgerError,
)

ROUTE = "CTP___FUTURE"
RB = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2701", "rb2701")
SA = MarketInstrumentKey("ctp", "CZCE", "simnow", "future", "SA701", "SA701")
IF = MarketInstrumentKey("ctp", "CFFEX", "simnow", "future", "IF2701", "IF2701")
D0, D1 = "20261110", "20261111"


class Buffer:
    def __init__(self):
        self.last_sequence, self.gap, self.record_count = 7, False, 0

    def status(self):
        return SimpleNamespace(last_sequence=self.last_sequence, gap=self.gap, gap_reason=None,
                               record_count=self.record_count)


class Session:
    def __init__(self):
        self.return_buffer = Buffer()
        self.positions, self.open_orders, self.reads = (), (), 0
        self.on_read = None

    def read_account(self, *, generation, timeout):
        assert generation == "service-generation" and timeout > 0
        self.reads += 1
        if self.on_read is not None:
            self.on_read()
        return SimpleNamespace(observation_id=f"ctp-{self.reads}", positions=self.positions,
                               open_orders=self.open_orders, positions_complete=True,
                               open_orders_complete=True)


class Provider:
    def __init__(self):
        self.day, self.generation = D1, 3

    def trading_day(self):
        return self.day

    def session_generation(self):
        return self.generation


class Returns:
    def __init__(self):
        self.continuous, self.open = True, True

    def ledger_continuous(self):
        return self.continuous

    @contextmanager
    def reservation_fence(self, _context, _order):
        assert self.open
        yield


def row(instrument, side, today, yesterday):
    return SimpleNamespace(instrument=instrument, side=side, today_quantity=today,
                           yesterday_quantity=yesterday)


def order(side="buy", offset="open", symbol="rb2701", venue="SHFE"):
    return {"symbol": symbol, "side": side, "offset": offset, "quantity": "1",
            "quantity_unit": "contracts", "exchange_id": venue}


@pytest.fixture
def env(tmp_path):
    directory = tmp_path / "guard"
    directory.mkdir()
    guard = LocalAccountSessionGuard(directory, "acct", "owner")
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal.create(tmp_path / "db", "acct", generation=generation)
    journal.enable_controls(guard)
    values = SimpleNamespace(journal=journal, guard=guard, session=Session(), provider=Provider(),
                             returns=Returns())
    yield values
    guard.release()


def build(env, **options):
    return CtpGatewayStrategyLedger(
        journal=env.journal, guard=env.guard, session=env.session, provider=env.provider,
        returns=env.returns, instruments=(RB, SA, IF), account_scope="acct", route=ROUTE,
        service_generation="service-generation", split_close_required=split_close_required, **options)


def trade_before_ledger(env, *, strategy="strategy-b", settle=True, day=D0):
    """An open that filled while the journal had no strategy ledger yet."""
    journal = env.journal
    record = journal.reserve(strategy, "open", "p", order())
    native = {"exchange_name": ROUTE, "client_order_id": "open", "session_generation": 1,
              "fields_json": json.dumps({"trading_day": day})}
    journal.begin_dispatch(strategy, "open", record.reservation_id, record.digest, "p", 1, native, ("anchor",))
    journal.finish(strategy, "open", record.reservation_id, record.digest, "p", 2, native, ("anchor",), "SENT")
    ledger = GatewayDirectExecutionLedger(journal, env.guard)
    common = dict(identity=NativeOrderIdentity(ROUTE, "ref", {"trading_day": day}, 1),
                  identity_keys=("anchor",), binding_keys=("anchor",), venue_key="anchor",
                  side="buy", offset="open")
    assert ledger.ingest(GatewayReturnFact("acct", ROUTE, RB, day, DirectExecutionEventKind.TRADE, "source",
        trade_key=("SHFE", "trade"), trade_quantity="1", trade_price="10", **common)).applied
    if settle:
        ledger.ingest(GatewayReturnFact("acct", ROUTE, RB, day, DirectExecutionEventKind.ORDER, "source",
            status=DirectOrderStatus.FILLED, order_quantity="1", filled_quantity="1",
            remaining_quantity="0", **common))


def baseline(env):
    return env.journal.read_strategy_ledger_state(guard=env.guard)["baseline"]


def test_ports_resolve_only_configured_instruments_with_an_established_close_rule(env):
    env.journal.enable_strategy_ledger(env.guard)
    ledger = build(env)
    rb = ledger.resolve_binding(order())
    assert (rb.instrument, rb.route, rb.quantity_unit, rb.split_close_required) == (RB, ROUTE, "contracts", True)
    assert ledger.resolve_binding({"symbol": "rb2701"}) is rb
    assert ledger.resolve_binding(order(symbol="SA701", venue="CZCE")).split_close_required is False
    for bad in (order(symbol="rb2702"), order(venue="DCE"), order(symbol="IF2701", venue="CFFEX"),
                {"symbol": "IF2701"}, "rb2701", {"symbol": 1}):
        with pytest.raises(CtpGatewayStrategyLedgerError, match="INSTRUMENT_UNRESOLVED"):
            ledger.resolve_binding(bad)
    assert ledger.current_trading_day() == D1
    assert ledger.verify_day_transition(D0, D1) is True
    for old, new in ((D1, D1), (D1, D0), (D0, "20261112"), ("2026111", D1), (None, D1)):
        assert ledger.verify_day_transition(old, new) is False
    env.provider.day = None
    assert ledger.verify_day_transition(D0, D1) is False
    with pytest.raises(CtpGatewayStrategyLedgerError, match="TRADING_DAY_UNAVAILABLE"):
        ledger.current_trading_day()


def test_fresh_journal_and_flat_account_take_the_empty_baseline(env):
    env.journal.enable_strategy_ledger(env.guard)
    ledger = build(env)
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_LEDGER_UNAVAILABLE"):
        with ledger.reservation_fence(None, None):
            pass
    ledger.maintain()
    assert (ledger.state, ledger.ready, env.session.reads) == ("EMPTY_INITIALIZED", True, 1)
    stored = baseline(env)
    assert stored["schema_version"] == 1 and stored["positions_empty"] is True
    assert stored["trading_day"] == D1 and stored["session_generation"] == 3
    assert stored["cut_ref"] == "ctp-return-buffer:3:7"
    with ledger.reservation_fence(None, None):
        pass
    close = order("sell", "close_today")
    assert env.journal.read_strategy_position("strategy-a", close, expected_instrument=RB).net == 0
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_CLOSE_EXCEEDS"):
        env.journal.reserve("strategy-a", "close", "p", close)
    env.journal.reserve("strategy-a", "open", "p", order())
    ledger.maintain()
    assert env.session.reads == 1  # nothing is read again once the baseline exists
    # A restarted owner finds the baseline without asking the provider.
    env.journal._strategy_ledger_ports = None  # as a reopened journal would be
    restarted = build(env)
    restarted.maintain()
    assert (restarted.state, restarted.ready, env.session.reads) == ("PRESENT", True, 1)


def test_holdings_without_history_wait_for_an_external_baseline(env):
    env.journal.enable_strategy_ledger(env.guard)
    env.session.positions = (row(RB, "long", "0", "1"),)
    ledger = build(env, retry_seconds=30.0)
    ledger.maintain()
    assert (ledger.state, ledger.ready) == ("EXTERNAL_BASELINE_REQUIRED", False)
    assert baseline(env) is None
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_LEDGER_UNAVAILABLE"):
        with ledger.reservation_fence(None, None):
            pass
    ledger.maintain()
    assert env.session.reads == 1  # retried only after the retry interval


def test_history_that_matches_the_account_is_adopted_and_serves_the_close(env):
    trade_before_ledger(env)
    env.journal.enable_strategy_ledger(env.guard)
    env.session.positions = (row(RB, "long", "0", "1"), row(SA, "short", "0", "0"))
    ledger = build(env)
    ledger.maintain()
    assert (ledger.state, ledger.ready) == ("REPLAY_INITIALIZED", True)
    stored = baseline(env)
    assert stored["schema_version"] == 2 and stored["history_start_trading_day"] == D0
    assert [item["today_quantity"] + "/" + item["yesterday_quantity"] for item in stored["account_positions"]] == ["0/1"]
    close = order("sell", "close_yesterday")
    assert env.journal.read_strategy_close_capacity("strategy-b", close, expected_instrument=RB).available == 1
    env.journal.reserve("strategy-b", "close", "p", close)
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_CLOSE_EXCEEDS"):
        env.journal.reserve("strategy-a", "close", "p", close)


@pytest.mark.parametrize("positions,state", [
    ((), "REFUSED:STRATEGY_BASELINE_MISMATCH"),
    ((row(RB, "long", "0", "2"),), "REFUSED:STRATEGY_BASELINE_MISMATCH"),
    ((row(RB, "long", "0", "1"), row(SA, "long", "1", "0")), "REFUSED:STRATEGY_BASELINE_MISMATCH"),
    ((row(RB, "net", "0", "1"),), "POSITION_NOT_ATTRIBUTABLE"),
    ((row(RB, "long", None, None),), "POSITION_NOT_ATTRIBUTABLE"),
    ((row(RB, "long", "0", "1"), row(IF, "long", "0", "1")), "POSITION_NOT_ATTRIBUTABLE"),
])
def test_history_that_does_not_explain_the_account_is_refused(env, positions, state):
    trade_before_ledger(env)
    env.journal.enable_strategy_ledger(env.guard)
    env.session.positions = positions
    ledger = build(env)
    ledger.maintain()
    assert (ledger.state, ledger.ready) == (state, False)
    assert baseline(env) is None


def test_unsettled_history_is_refused(env):
    trade_before_ledger(env, settle=False)
    env.journal.enable_strategy_ledger(env.guard)
    env.session.positions = (row(RB, "long", "0", "1"),)
    ledger = build(env)
    ledger.maintain()
    assert ledger.state == "REFUSED:STRATEGY_BASELINE_HISTORY_UNRESOLVED" and baseline(env) is None


def test_baseline_needs_complete_returns_and_a_comparable_cut(env):
    env.journal.enable_strategy_ledger(env.guard)
    ledger = build(env, retry_seconds=0.001)
    env.returns.continuous = False
    ledger.maintain()
    assert (ledger.state, env.session.reads) == ("WAITING_RETURNS", 0)
    env.returns.continuous = True
    env.session.return_buffer.record_count = 1
    ledger.maintain()
    assert (ledger.state, env.session.reads) == ("WAITING_RETURNS", 0)
    env.session.return_buffer.record_count = 0

    def account_activity():
        env.session.return_buffer.last_sequence += 1

    env.session.on_read = account_activity
    ledger.maintain()
    assert ledger.state == "CUT_NOT_COMPARABLE" and baseline(env) is None
    import time
    for change, state in (
        (lambda: setattr(env.provider, "generation", env.provider.generation + 1), "CUT_NOT_COMPARABLE"),
        (lambda: setattr(env.session.return_buffer, "gap", True), "CUT_NOT_COMPARABLE"),
    ):
        env.session.on_read = change
        time.sleep(0.005)
        ledger.maintain()
        assert ledger.state == state and baseline(env) is None
        env.session.return_buffer.gap = False
    env.session.on_read = None
    env.session.open_orders = (object(),)
    time.sleep(0.005)
    ledger.maintain()
    assert ledger.state == "OPEN_ORDERS_PRESENT" and baseline(env) is None
    env.session.open_orders = ()
    time.sleep(0.005)
    ledger.maintain()
    assert ledger.state == "EMPTY_INITIALIZED"


@pytest.mark.parametrize("change", ["sequence", "generation", "day", "expired"])
def test_cut_is_rechecked_inside_the_journal_transaction(env, change, monkeypatch):
    env.journal.enable_strategy_ledger(env.guard)
    ledger = build(env, snapshot_validity_seconds=0.05 if change == "expired" else 5.0)
    original = env.journal.initialize_empty_strategy_ledger

    def late(evidence, **ports):
        # The account moved after the read and before the commit.
        if change == "sequence":
            env.session.return_buffer.last_sequence += 1
        elif change == "generation":
            env.provider.generation += 1
        elif change == "day":
            env.provider.day = "20261112"
        else:
            import time
            time.sleep(0.08)
        return original(evidence, **ports)

    monkeypatch.setattr(env.journal, "initialize_empty_strategy_ledger", late)
    ledger.maintain()
    assert ledger.state == "REFUSED:STRATEGY_LEDGER_UNAVAILABLE" and not ledger.ready
    assert baseline(env) is None


def test_failed_read_leaves_the_owner_running(env):
    env.journal.enable_strategy_ledger(env.guard)
    ledger = build(env, retry_seconds=0.001)

    def broken():
        raise RuntimeError("query failed")

    env.session.on_read = broken
    ledger.maintain()
    assert ledger.state == "FAILED:RuntimeError" and not ledger.ready
    env.session.on_read = None
    import time
    time.sleep(0.005)
    ledger.maintain()
    assert ledger.ready


def test_continuity_follows_the_session_day_the_returns_and_earlier_days(env):
    trade_before_ledger(env)
    env.journal.enable_strategy_ledger(env.guard)
    env.session.positions = (row(RB, "long", "0", "1"),)
    ledger = build(env)
    ledger.maintain()
    assert ledger.ready
    close = order("sell", "close_yesterday")

    def available():
        return env.journal.read_strategy_close_capacity("strategy-b", close, expected_instrument=RB).available

    assert available() == 1
    env.returns.continuous = False
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_LEDGER_UNAVAILABLE"):
        available()
    env.returns.continuous = True
    assert available() == 1
    # An order of an earlier day that may have reached the venue and was never
    # settled cannot be completed by today's queries.
    record = env.journal.reserve("strategy-b", "stale", "p", close)
    native = {"exchange_name": ROUTE, "client_order_id": "stale", "session_generation": 1,
              "fields_json": json.dumps({"trading_day": D1})}
    env.journal.begin_dispatch("strategy-b", "stale", record.reservation_id, record.digest, "p", 1,
                               native, ("stale-anchor",))
    assert available() == 0  # today's own unsettled close only occupies capacity
    env.provider.day = "20261112"
    fresh = build(env)   # the next trading day's owner
    env.journal._strategy_ledger_ports = None
    fresh.install()
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_LEDGER_UNAVAILABLE"):
        available()


# ---------------------------------------------------------------------------
# operator-declared external baseline (D63-06)
# ---------------------------------------------------------------------------
def declaration(*positions, operation="op-20261110-1", operator="operator-ops"):
    from bt_api_gateway.strategy_position_ledger import ExternalBaselinePosition

    from bt_api_py.ctp_gateway_strategy_ledger import ExternalBaselineDeclaration

    return ExternalBaselineDeclaration(operator, operation, tuple(
        ExternalBaselinePosition(instrument, direction, today, yesterday)
        for instrument, direction, today, yesterday in positions))


def test_declared_holdings_that_equal_the_account_become_external(env):
    env.journal.enable_strategy_ledger(env.guard)
    env.session.positions = (row(SA, "short", "2", "0"), row(RB, "long", "0", "1"))
    declared = declaration((RB, "long", "0", "1"), (SA, "short", "2", "0"))
    ledger = build(env, external_baseline=declared)
    ledger.maintain()
    assert (ledger.state, ledger.ready) == ("EXTERNAL_INITIALIZED", True)
    stored = baseline(env)
    assert (stored["operator_ref"], stored["operation_id"]) == ("operator-ops", "op-20261110-1")
    assert stored["positions_empty"] is False and len(stored["external_positions"]) == 2
    # The holdings belong to no strategy: nobody can close them through the Gateway.
    close = order("sell", "close_yesterday")
    assert env.journal.read_strategy_position("strategy-a", close, expected_instrument=RB).net == 0
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_CLOSE_EXCEEDS"):
        env.journal.reserve("strategy-a", "close", "p", close)
    env.journal.reserve("strategy-a", "open", "p", order())
    # A restart with the same declaration finds it; another declaration never replaces it.
    env.journal._strategy_ledger_ports = None
    same = build(env, external_baseline=declared)
    same.maintain()
    assert (same.state, same.ready, env.session.reads) == ("PRESENT", True, 1)
    for other in (declaration((RB, "long", "0", "1"), (SA, "short", "2", "0"), operation="op-2"),
                  declaration((RB, "long", "0", "1"), (SA, "short", "2", "0"), operator="someone-else"),
                  declaration((RB, "long", "0", "2"), (SA, "short", "2", "0"))):
        env.journal._strategy_ledger_ports = None
        changed = build(env, external_baseline=other)
        changed.maintain()
        assert (changed.state, changed.ready) == ("EXTERNAL_BASELINE_CONFLICT", False)
    # Without a declaration the existing baseline is simply served.
    env.journal._strategy_ledger_ports = None
    plain = build(env)
    plain.maintain()
    assert plain.ready


@pytest.mark.parametrize("positions", [
    (),
    (row(RB, "long", "0", "2"),),
    (row(RB, "long", "1", "0"),),
    (row(RB, "short", "0", "1"),),
    (row(RB, "long", "0", "1"), row(SA, "long", "1", "0")),
])
def test_declaration_that_differs_from_the_account_is_refused(env, positions):
    env.journal.enable_strategy_ledger(env.guard)
    env.session.positions = positions
    ledger = build(env, external_baseline=declaration((RB, "long", "0", "1")))
    ledger.maintain()
    assert (ledger.state, ledger.ready) == ("EXTERNAL_BASELINE_MISMATCH", False)
    assert baseline(env) is None


def test_external_declaration_needs_a_journal_without_history(env):
    trade_before_ledger(env)
    env.journal.enable_strategy_ledger(env.guard)
    env.session.positions = (row(RB, "long", "0", "1"),)
    ledger = build(env, external_baseline=declaration((RB, "long", "0", "1")))
    ledger.maintain()
    assert (ledger.state, ledger.ready, env.session.reads) == (
        "EXTERNAL_BASELINE_NEEDS_EMPTY_JOURNAL", False, 0)
    assert baseline(env) is None


def test_invalid_external_declarations_are_refused_before_any_read(env):
    from bt_api_gateway.strategy_position_ledger import ExternalBaselinePosition

    from bt_api_py.ctp_gateway_strategy_ledger import ExternalBaselineDeclaration

    env.journal.enable_strategy_ledger(env.guard)
    other = MarketInstrumentKey("ctp", "DCE", "simnow", "future", "m2701", "m2701")
    for bad in (declaration((other, "long", "0", "1")),   # not a configured instrument
                declaration((IF, "long", "0", "1")),      # venue without an established close rule
                {"operator": "x"}):
        with pytest.raises(CtpGatewayStrategyLedgerError, match="INVALID_EXTERNAL_BASELINE"):
            build(env, external_baseline=bad)
    lot = ExternalBaselinePosition(RB, "long", "0", "1")
    for arguments in (("", "op", (lot,)), ("operator", " op", (lot,)), ("operator", "op", ()),
                      ("operator", "op", (lot, lot)), ("operator", "op", [lot]), (1, "op", (lot,))):
        with pytest.raises(CtpGatewayStrategyLedgerError, match="INVALID_EXTERNAL_BASELINE"):
            ExternalBaselineDeclaration(*arguments)


# ---------------------------------------------------------------------------
# account comparison (D63-48): strategies + external == account at a kept cut
# ---------------------------------------------------------------------------
def account_row(instrument, side, quantity):
    today = "0"
    return SimpleNamespace(instrument=instrument, side=side, quantity=quantity, today_quantity=today,
                           yesterday_quantity=quantity)


def comparing(env, **options):
    env.journal.enable_strategy_ledger(env.guard)
    env.journal.enable_account_extensions(env.guard)
    ledger = build(env, comparison_refresh_seconds=1.0, **options)
    ledger.maintain()          # baseline
    assert ledger.ready, ledger.state
    ledger.maintain()          # comparison ports and the first account cut
    return ledger


def compare(env, instrument=RB):
    result = env.journal.read_account_net_comparison(instrument)
    return (result.status, None if result.strategy_net is None else int(result.strategy_net),
            None if result.external_net is None else int(result.external_net),
            None if result.account_net is None else int(result.account_net),
            None if result.difference is None else int(result.difference))


UNAVAILABLE = ("UNAVAILABLE", None, None, None, None)


def test_strategy_positions_plus_external_equal_the_account_at_the_kept_cut(env):
    trade_before_ledger(env)
    env.session.positions = (account_row(RB, "long", "1"),)
    ledger = comparing(env)
    assert env.session.reads == 2  # one for the baseline, one for the comparison cut
    assert compare(env) == ("MATCH", 1, 0, 1, 0)
    assert compare(env, SA) == ("MATCH", 0, 0, 0, 0)
    assert compare(env, IF) == UNAVAILABLE  # no established close rule: not resolvable
    ledger.maintain()
    assert env.session.reads == 2  # a current cut is not read again
    # The account moved (any order or trade shows on the private stream): the
    # old snapshot answers nothing until a new one is taken.
    env.session.return_buffer.last_sequence += 1
    env.session.positions = (account_row(RB, "long", "2"),)
    assert compare(env) == UNAVAILABLE
    ledger.maintain()
    assert env.session.reads == 2 and compare(env) == UNAVAILABLE  # refresh is rate limited
    ledger._comparison_next = 0.0
    ledger.maintain()
    assert env.session.reads == 3
    assert compare(env) == ("MISMATCH", 1, 0, 2, 1)  # one lot nobody in the journal explains


def test_declared_external_holdings_count_as_external_not_as_a_difference(env):
    env.session.positions = (account_row(RB, "long", "1"), account_row(SA, "short", "2"))
    comparing(env, external_baseline=declaration((RB, "long", "0", "1"), (SA, "short", "0", "2")))
    assert compare(env) == ("MATCH", 0, 1, 1, 0)
    assert compare(env, SA) == ("MATCH", 0, -2, -2, 0)


@pytest.mark.parametrize("change", ["day", "session", "gap", "pending", "age", "returns"])
def test_comparison_is_unavailable_once_the_cut_is_not_the_latest(env, change, monkeypatch):
    trade_before_ledger(env)
    env.session.positions = (account_row(RB, "long", "1"),)
    ledger = comparing(env)
    assert compare(env)[0] == "MATCH"
    if change == "day":
        env.provider.day = "20261112"
    elif change == "session":
        env.provider.generation += 1
    elif change == "gap":
        env.session.return_buffer.gap = True
    elif change == "pending":
        env.session.return_buffer.record_count = 1
    elif change == "age":
        ledger._account_cut["taken_at"] -= 121.0
    else:
        env.returns.continuous = False
    assert compare(env) == UNAVAILABLE


def test_a_snapshot_taken_while_the_account_moved_is_not_kept(env):
    trade_before_ledger(env)
    env.session.positions = (account_row(RB, "long", "1"),)
    env.journal.enable_strategy_ledger(env.guard)
    env.journal.enable_account_extensions(env.guard)
    ledger = build(env, comparison_refresh_seconds=1.0)
    ledger.maintain()
    assert ledger.ready

    def account_activity():
        env.session.return_buffer.last_sequence += 1

    env.session.on_read = account_activity
    ledger.maintain()
    assert env.session.reads == 2 and compare(env) == UNAVAILABLE
    env.session.on_read = None
    ledger._comparison_next = 0.0
    ledger.maintain()
    assert compare(env) == ("MATCH", 1, 0, 1, 0)


def test_comparison_is_off_unless_configured(env):
    trade_before_ledger(env)
    env.session.positions = (account_row(RB, "long", "1"),)
    env.journal.enable_strategy_ledger(env.guard)
    env.journal.enable_account_extensions(env.guard)
    ledger = build(env)
    ledger.maintain()
    ledger.maintain()
    assert ledger.ready and env.session.reads == 1 and compare(env) == UNAVAILABLE
    ledger.install_comparison()  # asking directly changes nothing either
    assert env.journal._account_comparison_ports is None
    for bad in (0.5, 500, "30", True):
        with pytest.raises(CtpGatewayStrategyLedgerError, match="INVALID_STRATEGY_LEDGER_SETTINGS"):
            build(env, comparison_refresh_seconds=bad)
