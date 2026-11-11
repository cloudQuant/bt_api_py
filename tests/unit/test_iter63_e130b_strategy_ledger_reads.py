"""I63-E130: the strategy ledger's account reads are request and response.

The baseline read and the comparison cut read start on one owner tick and end
on a later one; the return-buffer cut is taken before the start and after the
completion. Synthetic session, provider and return source over a real Gateway
journal; no CTP, account or network.
"""

import pytest
from bt_api_ctp.account_provider import CtpAccountSessionProviderError

from tests.unit.test_iter63_ctp_gateway_strategy_ledger import (  # noqa: F401  (fixture)
    UNAVAILABLE,
    Buffer,
    account_row,
    baseline,
    build,
    compare,
    env,
    order,
    trade_before_ledger,
)


class HeldSession:
    """Answers an account read only when the test says so; has no blocking read."""

    def __init__(self):
        self.return_buffer = Buffer()
        self.positions, self.open_orders = (), ()
        self.begins, self.polls, self.cancels, self.reads = 0, 0, 0, 0
        self.request = None
        self.answer_now, self.failure = False, None

    def begin_read_account(self, *, generation, timeout):
        assert generation == "service-generation" and timeout > 0
        assert self.request is None, "a second read began while one was outstanding"
        self.begins += 1
        self.request = object()
        return self.request

    def poll_read_account(self, request):
        assert request is self.request
        self.polls += 1
        if self.failure is not None:
            self.request = None
            raise self.failure
        if not self.answer_now:
            return None
        self.request, self.reads = None, self.reads + 1
        return _observation(self)

    def cancel_read_account(self, request):
        if request is self.request:
            self.request, self.cancels = None, self.cancels + 1


def _observation(session):
    from types import SimpleNamespace

    return SimpleNamespace(observation_id=f"ctp-{session.reads}", positions=session.positions,
                           open_orders=session.open_orders, positions_complete=True,
                           open_orders_complete=True)


@pytest.fixture
def held(request):
    values = request.getfixturevalue("env")
    values.session = HeldSession()
    values.journal.enable_strategy_ledger(values.guard)
    return values


def test_baseline_read_starts_on_one_tick_and_ends_on_a_later_one(held):
    ledger = build(held)
    ledger.maintain()
    assert (held.session.begins, ledger.read_pending, ledger.state, ledger.ready) == (
        1, True, "PENDING", False)
    for _ in range(3):
        ledger.maintain()  # each tick only polls the outstanding read
    assert (held.session.begins, held.session.polls, ledger.state) == (1, 4, "PENDING")
    held.session.answer_now = True
    ledger.maintain()
    assert (ledger.state, ledger.ready, ledger.read_pending) == ("EMPTY_INITIALIZED", True, False)
    assert baseline(held)["cut_ref"] == "ctp-return-buffer:3:7"


@pytest.mark.parametrize("change", ["sequence", "generation", "day", "gap", "returns", "journal"])
def test_anything_that_moved_while_the_read_was_outstanding_breaks_the_baseline_cut(held, change):
    ledger = build(held)
    ledger.maintain()
    assert ledger.read_pending
    if change == "sequence":
        # A return arrived and the owner already ingested it (nothing waits in the buffer).
        held.session.return_buffer.last_sequence += 1
    elif change == "generation":
        held.provider.generation += 1
    elif change == "day":
        held.provider.day = "20261112"
    elif change == "gap":
        held.session.return_buffer.gap = True
    elif change == "returns":
        held.returns.continuous = False
    else:
        held.journal.reserve("strategy-a", "open", "p", order())  # the journal gained history
    held.session.answer_now = True
    ledger.maintain()
    assert (ledger.state, ledger.ready, baseline(held)) == ("CUT_NOT_COMPARABLE", False, None)


@pytest.mark.parametrize("code", ["QUERY_BUDGET_EXCEEDED", "QUERY_SCOPE_CHANGED"])
def test_a_read_that_timed_out_or_lost_its_session_ends_the_attempt(held, code):
    ledger = build(held, retry_seconds=30.0)
    ledger.maintain()
    held.session.failure = CtpAccountSessionProviderError(code)
    ledger.maintain()
    assert (ledger.state, ledger.ready, ledger.read_pending) == ("FAILED:" + code, False, False)
    held.session.failure = None
    ledger.maintain()
    assert held.session.begins == 1  # retried only after the retry interval
    ledger._next_attempt = 0.0
    held.session.answer_now = True
    ledger.maintain()
    assert (held.session.begins, ledger.state) == (2, "EMPTY_INITIALIZED")


def _comparing(values):
    values.journal.enable_account_extensions(values.guard)
    ledger = build(values, comparison_refresh_seconds=1.0)
    values.session.answer_now = True
    ledger.maintain()  # baseline
    assert ledger.ready, ledger.state
    values.session.answer_now = False
    return ledger


def test_comparison_cut_read_is_request_and_response(held):
    trade_before_ledger(held)
    held.session.positions = (account_row(held_rb(), "long", "1"),)
    ledger = _comparing(held)
    ledger.maintain()  # the comparison read starts
    assert ledger.read_pending and held.session.begins == 2
    assert compare(held) == UNAVAILABLE and ledger.comparison_state == {}
    ledger.maintain()
    assert held.session.begins == 2  # an outstanding cut read is only polled
    held.session.answer_now = True
    ledger.maintain()
    assert not ledger.read_pending
    assert compare(held) == ("MATCH", 1, 0, 1, 0)
    assert ledger.comparison_state["rb2701"] == "MATCH"  # evaluated when the cut was kept


@pytest.mark.parametrize("change", ["sequence", "generation", "pending"])
def test_a_cut_read_during_which_the_account_moved_is_not_kept(held, change):
    trade_before_ledger(held)
    held.session.positions = (account_row(held_rb(), "long", "1"),)
    ledger = _comparing(held)
    ledger.maintain()
    if change == "sequence":
        held.session.return_buffer.last_sequence += 1
    elif change == "generation":
        held.provider.generation += 1
    else:
        held.session.return_buffer.record_count = 1
    held.session.answer_now = True
    ledger.maintain()
    assert ledger._account_cut is None and compare(held) == UNAVAILABLE
    held.session.return_buffer.record_count = 0
    if change == "generation":
        return
    ledger._comparison_next = 0.0
    ledger.maintain()
    assert compare(held) == ("MATCH", 1, 0, 1, 0)


def test_a_failed_cut_read_drops_the_cut_and_never_raises(held):
    trade_before_ledger(held)
    held.session.positions = (account_row(held_rb(), "long", "1"),)
    ledger = _comparing(held)
    ledger.maintain()
    held.session.failure = CtpAccountSessionProviderError("QUERY_BUDGET_EXCEEDED")
    ledger.maintain()
    assert ledger._account_cut is None and not ledger.read_pending
    assert compare(held) == UNAVAILABLE


def test_an_outstanding_cut_read_is_cancelled_when_its_tick_fails(held, monkeypatch):
    trade_before_ledger(held)
    held.session.positions = (account_row(held_rb(), "long", "1"),)
    ledger = _comparing(held)
    ledger.maintain()
    assert ledger.read_pending

    def broken():
        raise RuntimeError("synthetic")

    monkeypatch.setattr(ledger, "install_comparison", broken)
    ledger.maintain()
    assert (ledger.read_pending, held.session.cancels, held.session.request) == (False, 1, None)


def held_rb():
    from tests.unit.test_iter63_ctp_gateway_strategy_ledger import RB

    return RB
