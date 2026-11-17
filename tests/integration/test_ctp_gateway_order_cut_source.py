"""Synthetic native and SQLite evidence for owner-local cut scheduling."""

import threading

import pytest

from bt_api_py.ctp_gateway_order_cut_source import (
    CtpGatewayOrderCutSettings,
    CtpGatewayOrderCutSource,
)
from tests.integration.test_iter64_ctp_gateway_order_cut import _finish
from tests.integration.test_iter64_ctp_observed_order_source import selected

pytest_plugins = ("tests.integration.test_iter63_ctp_gateway_return_maintenance",)


def source_for(real_owner):
    maintenance, session, native, journal, _ = selected(real_owner)
    session._trader._query_interval = 0
    source = CtpGatewayOrderCutSource(
        session,
        maintenance,
        journal,
        maintenance._ledger._guard,
        CtpGatewayOrderCutSettings(2, 0.05, frozenset({"reader"})),
        execution_domain_ref="paper",
    )
    return source, maintenance, session, native, journal


def test_real_source_publishes_and_normally_retires_expired_cut(real_owner):
    source, maintenance, session, native, journal = source_for(real_owner)
    assert source.journal is journal and source.guard is maintenance._ledger._guard
    assert source.due(1000000000.0) and source.begin()
    _finish(source._coordinator)
    assert source.current() is not None
    first = source._coordinator
    first._deadline = 0.0
    assert source.current() is None
    assert first.status().state == "CLOSED"
    assert maintenance.observed_order_source_status().status != "GAP"
    assert session._order_query_cut is None
    assert source.due(1000000000.0) and source.begin()
    _finish(source._coordinator)
    assert source.current() is not None
    assert native.calls.count("orders") == 2
    assert source.close() and source.close()


def test_owner_thread_required_and_pending_close_retains_original_pin(real_owner, monkeypatch):
    source, maintenance, session, native, _ = source_for(real_owner)
    sent = []
    monkeypatch.setattr(native, "ReqQryOrder", lambda _field, rid: sent.append(rid) or 0)
    errors = []

    def wrong():
        try:
            source.current()
        except RuntimeError as error:
            errors.append(str(error))

    thread = threading.Thread(target=wrong)
    thread.start()
    thread.join()
    assert errors == ["ORDER_CUT_WRONG_OWNER"]
    assert source.begin() and source.advance() and sent
    original = source._coordinator
    assert not source.pause_for_reconnect()
    assert source._coordinator is original and session._order_query_cut is not None
    native.client._spi.OnRspQryOrder(None, None, sent[0], True)
    assert source.pause_for_reconnect()
    assert session._order_query_cut is None
    assert source.close()


def test_rebuild_cannot_replace_source_until_original_cut_is_closed(real_owner, monkeypatch):
    from tests.integration.test_iter63_ctp_gateway_return_maintenance import _relogin_same_spi

    source, maintenance, session, native, _ = source_for(real_owner)
    maintenance.bind_before_rebuild(source.pause_for_reconnect)
    sent = []
    monkeypatch.setattr(native, "ReqQryOrder", lambda _field, rid: sent.append(rid) or 0)
    assert source.begin() and source.advance() and sent
    tracker, coordinator = maintenance._observed_order_source, source._coordinator
    _relogin_same_spi(native, tracker._binding.trading_day, 20)
    session.refresh_session()
    maintenance.poll()
    assert maintenance._observed_order_source is tracker
    assert source._coordinator is coordinator and session._order_query_cut is not None
    native.client._spi.OnRspQryOrder(None, None, sent[0], True)
    assert source.close()


@pytest.mark.parametrize(
    "state,pin,expected",
    [
        ("QUERYING", True, True),
        ("GAP", True, True),
        ("UNKNOWN", None, True),
        ("GAP", False, False),
        ("CANDIDATE", False, False),
    ],
)
def test_pending_fences_original_pin_and_uncertain_custody(
    real_owner, monkeypatch, state, pin, expected
):
    from types import SimpleNamespace

    source, _, _, _, _ = source_for(real_owner)
    coordinator = source._coordinator
    original = coordinator.status
    monkeypatch.setattr(
        coordinator, "status", lambda: SimpleNamespace(state=state, pin_retained=pin)
    )
    assert source.pending() is expected
    monkeypatch.setattr(coordinator, "status", original)
    assert source.close()


def test_expired_candidate_busy_does_not_poison_or_replace_before_validation(
    real_owner, monkeypatch
):
    from bt_api_ctp.gateway_session import CtpGatewaySessionError

    source, maintenance, session, _, _ = source_for(real_owner)
    assert source.begin()
    _finish(source._coordinator)
    old = source._coordinator
    old._deadline = 0.0
    original = session.capture_calendar_session

    def busy():
        raise CtpGatewaySessionError("CALENDAR_SESSION_BUSY")

    monkeypatch.setattr(session, "capture_calendar_session", busy)
    assert source.current() is None
    assert source._coordinator is old and old.status().state == "CANDIDATE"
    assert maintenance.observed_order_source_status().status != "GAP"
    monkeypatch.setattr(session, "capture_calendar_session", original)
    assert source.current() is None
    assert old.status().state == "CLOSED" and source._coordinator is not old
    assert source.close()
