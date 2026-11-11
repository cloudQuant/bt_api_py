"""I63-E130: full-day return queries run as request and response on the owner.

Real CTP session and SQLite journal with only the native boundary substituted;
the venue's answer arrives when the test delivers it.
"""

import time
from dataclasses import replace

import pytest

from bt_api_py.ctp_gateway_trading import CtpGatewayReturnSettings, CtpGatewayTradingError
from tests.integration.test_iter63_ctp_gateway_return_ingestion import (
    _command,
    _fact_count,
    _order,
    _trade,
)
from tests.integration.test_iter63_ctp_gateway_return_maintenance import (
    _pair,
    _relogin_same_spi,
)
from tests.integration.test_iter63_ctp_gateway_return_maintenance import (
    real_owner as real_owner,
)


@pytest.fixture
def owner(real_owner):
    return real_owner


def _hold(native):
    """Answer queries only on ``deliver`` (a slow venue)."""
    held = []

    def accept(kind, request_id):
        native.calls.append(kind)
        held.append((kind, request_id))
        return 0

    def deliver():
        kind, request_id = held.pop(0)
        for row in native.rows[kind]:
            native.client._handle_query_callback(kind, row, None, request_id, False)
        native.client._handle_query_callback(kind, None, None, request_id, True)

    native.answer = accept
    return held, deliver


def _timed(call):
    started = time.monotonic()
    value = call()
    return value, time.monotonic() - started


def _rows(native, day):
    native.rows = {"orders": [_order(TradingDay=day)], "trades": [_trade(TradingDay=day)]}


def test_outstanding_query_never_waits_and_is_ingested_on_a_later_tick(owner):
    m, _session, native, journal, instrument, helper = owner
    _command(journal, day=helper.TODAY)
    _rows(native, helper.TODAY)
    held, deliver = _hold(native)
    m.poll()
    _, elapsed = _timed(m.start_query)
    assert elapsed < 0.2 and m.query_pending() and len(held) == 1
    assert m.due(time.monotonic()) is False  # never a second query meanwhile
    for _ in range(3):
        outstanding, elapsed = _timed(m.advance_query)
        assert outstanding is True and elapsed < 0.2
    assert _fact_count(journal) == 0 and m._done == set()
    deliver()
    assert m.advance_query() is False and not m.query_pending()
    assert m._done == {"orders"} and _fact_count(journal) == 1
    m.start_query()
    deliver()
    assert m.advance_query() is False
    assert m.dispatch_ready() and m.ledger_continuous()
    assert m._ledger.position("strategy-a", instrument) == "1"
    # A periodic re-query: while the venue answers, the source keeps the
    # health it has between queries (it is closed only while ingesting).
    m._next_query = 0.0
    m.start_query()
    assert m.query_pending()
    m.poll()
    assert m.dispatch_ready() and m.ledger_continuous()
    with m.reservation_fence(None, None):
        pass
    deliver()
    assert m.advance_query() is False and m.dispatch_ready()


def test_unanswered_query_fails_after_its_timeout_and_is_not_ingested_late(owner):
    m, _session, native, journal, _instrument, helper = owner
    m._settings = replace(m._settings, query_timeout_seconds=0.3, query_interval_seconds=30)
    _pair(m)
    assert m.dispatch_ready()
    _command(journal, day=helper.TODAY)
    _rows(native, helper.TODAY)
    held, deliver = _hold(native)
    m._next_query = 0.0
    m.start_query()
    started = time.monotonic()
    while True:
        outstanding, elapsed = _timed(m.advance_query)
        assert elapsed < 0.2
        if not outstanding:
            break
        assert time.monotonic() - started < 3
        time.sleep(0.02)
    assert time.monotonic() - started >= 0.3
    assert m.status()["status"] == "QUERY_FAILED" and m._done == set()
    assert m._last_query_failure == "CtpGatewayReturnQueryError(QUERY_TIMEOUT)"
    assert m._next_query > time.monotonic() + 25  # retried after the query interval
    # A failed periodic query loses nothing: the callback stream is intact.
    assert m.ledger_continuous() and not m.dispatch_ready()
    deliver()  # the venue answers late
    m.poll()
    assert _fact_count(journal) == 0


@pytest.mark.parametrize("change", ["relogin", "relogin_seen_by_advance", "pause"])
def test_session_change_while_outstanding_drops_the_query_without_backoff(owner, change):
    m, session, native, journal, _instrument, helper = owner
    _pair(m)
    _command(journal, day=helper.TODAY)
    _rows(native, helper.TODAY)
    held, deliver = _hold(native)
    m._next_query = 0.0
    m.start_query()
    assert m.query_pending() and len(held) == 1
    old_reader = m._reader
    if change == "relogin":
        _relogin_same_spi(native, helper.TODAY, 20)
        session.refresh_session()
        m.poll()
        assert m._reader is not old_reader
    elif change == "relogin_seen_by_advance":
        _relogin_same_spi(native, helper.TODAY, 20)
        session.refresh_session()
        assert m.advance_query() is False
        assert m._reader is not old_reader
    else:
        m.pause_for_reconnect()
    assert not m.query_pending()
    assert m._last_query_failure is None and m.status()["status"] != "QUERY_FAILED"
    assert m.due(time.monotonic()) and m._next_kind == "orders" and m._done == set()
    deliver()  # the old request's answer arrives afterwards
    m.poll()
    assert _fact_count(journal) == 0 and not m.dispatch_ready()
    for _ in range(2):
        m.start_query()
        deliver()
        assert m.advance_query() is False
    assert _fact_count(journal) == 2 and m.dispatch_ready() and m.ledger_continuous()


def test_closing_owner_drops_an_outstanding_query(owner):
    m, _session, native, journal, _instrument, helper = owner
    _rows(native, helper.TODAY)
    held, deliver = _hold(native)
    m.start_query()
    assert m.query_pending()
    m.begin_close()
    assert m.advance_query() is False and not m.query_pending()
    deliver()
    assert _fact_count(journal) == 0


def test_blocking_query_runs_the_same_path_to_its_end(owner):
    m, _session, native, journal, _instrument, helper = owner
    _command(journal, day=helper.TODAY)
    _rows(native, helper.TODAY)
    m.poll()
    m.query()
    m.query()
    assert not m.query_pending() and m._done == {"orders", "trades"}
    assert _fact_count(journal) == 2 and m.dispatch_ready()


@pytest.mark.parametrize("timeout", [5.5, 30, 60])
def test_return_query_timeout_may_exceed_five_seconds(owner, timeout):
    """SimNow 2026-11-11: ~110 orders took 3-5 s; the old 5 s cap left no margin."""
    instrument = owner[4]
    assert CtpGatewayReturnSettings((instrument,), query_timeout_seconds=timeout).query_timeout_seconds == timeout


@pytest.mark.parametrize("timeout", [60.5, 0.01, float("inf"), True])
def test_return_query_timeout_is_still_bounded(owner, timeout):
    instrument = owner[4]
    with pytest.raises(CtpGatewayTradingError, match="INVALID_RETURN_SETTINGS"):
        CtpGatewayReturnSettings((instrument,), query_timeout_seconds=timeout)
