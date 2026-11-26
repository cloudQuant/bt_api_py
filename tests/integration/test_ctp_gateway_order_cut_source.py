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


def test_querying_timeout_aborts_cleanly_without_gap_and_retries_next_cut(real_owner, monkeypatch):
    """A late SimNow ReqQryOrder timeout during QUERYING must abort cleanly without permanently
    marking observed_order_source as GAP, retain its pin until terminal, and allow the next cut."""
    source, maintenance, session, native, _ = source_for(real_owner)
    sent = []
    original_req = native.ReqQryOrder

    def hold_first(field, rid):
        sent.append(rid)
        if len(sent) == 1:
            return 0  # simulate late SimNow query with no response before deadline
        return original_req(field, rid)

    monkeypatch.setattr(native, "ReqQryOrder", hold_first)
    assert source.begin() and source.advance()
    assert len(sent) == 1 and source.pending() is True

    # Expire the query deadline while still in QUERYING -> clean ABORTING (no GAP!).
    source._coordinator._deadline = 0.0
    assert source.advance() is True
    assert source._coordinator.status().state == "ABORTING"
    assert source.pending() is True
    assert session._order_query_cut is not None
    assert maintenance.observed_order_source_status().status != "GAP"
    assert not maintenance._observed_order_source.blocked

    # Late terminal callback arrives -> advance releases pin, resets coordinator cleanly, no GAP.
    native.client._spi.OnRspQryOrder(None, None, sent[0], True)
    assert source.advance() is False
    assert source.pending() is False
    assert session._order_query_cut is None
    assert maintenance.observed_order_source_status().status != "GAP"
    assert not maintenance._observed_order_source.blocked

    # Next cut starts and completes normally.
    assert source.due(1000000000.0) and source.begin()
    _finish(source._coordinator)
    assert source.current() is not None
    assert len(sent) == 2
    assert source.close()


def test_late_query_completion_refreshes_local_deadline_without_false_gap(real_owner, monkeypatch):
    """If QryOrder finishes near initial query_timeout_seconds, local projection/verification
    must get a refreshed deadline (still capped by trusted_expires_monotonic) instead of
    falsely poisoning observed_order_sources with status='GAP'."""
    import time
    from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

    source, maintenance, session, native, _ = source_for(real_owner)
    today = maintenance._observed_order_source._binding.trading_day
    native.rows["orders"] = [
        _order(TradingDay=today, OrderRef=str(idx + 1), OrderSysID=f"sys-late-{idx + 1}")
        for idx in range(5)
    ]
    assert source.begin()
    # Pretend QryOrder almost exhausted the initial 2s query budget right before returning.
    coordinator = source._coordinator
    initial_deadline = time.monotonic() + 0.05
    coordinator._deadline = initial_deadline
    assert source.advance() is True
    assert coordinator.status().state == "PROJECTING"
    # Deadline refreshed past initial_deadline and still bounded by trusted_expires_monotonic.
    assert coordinator._deadline > initial_deadline
    assert coordinator._deadline <= coordinator._result.query_source.trusted_expires_monotonic

    _finish(coordinator)
    assert coordinator.status().state == "CANDIDATE"
    assert source.current() is not None
    assert maintenance.observed_order_source_status().status != "GAP"
    assert source.close()


def test_proactive_staging_refresh_eliminates_blackout_window_and_stays_fail_closed(
    real_owner, monkeypatch
):
    """While an unexpired CANDIDATE is live (pin already released), due() starts a staging
    coordinator and promotes it atomically on completion with zero current() is None gap.
    Expiry of the live candidate before staging finishes still fails closed immediately."""
    source, maintenance, session, native, _ = source_for(real_owner)
    assert source.due(1000000000.0) and source.begin()
    _finish(source._coordinator)
    first_coord = source._coordinator
    first_cut = source.current()
    assert first_cut is not None
    assert first_coord.status().pin_retained is False
    assert session._order_query_cut is None

    # Before refresh_interval_seconds elapses, due() is False.
    assert source.due(0.0) is False

    # Once refresh_interval_seconds elapses while first_coord is still live & unexpired:
    assert source.due(1000000000.0) is True
    assert source.begin() is True
    assert source._coordinator is first_coord
    assert source._staging is not None
    assert source.pending() is True

    # Throughout staging steps, current() continues serving the still-live unexpired first cut!
    steps = 0
    while source.pending():
        assert source.current() is not None
        assert source.current_binding() is not None
        source.advance()
        steps += 1
        assert steps < 64

    # Staging promoted atomically; old coordinator closed; zero blackout; native queried twice.
    assert source._staging is None
    assert source._coordinator is not first_coord
    assert first_coord.status().state == "CLOSED"
    assert source.current() is not None
    assert native.calls.count("orders") == 2

    # Fail-closed check: if the live candidate expires while a staged query is still in flight,
    # current() MUST return None immediately (never serve an expired cut) and recover once staging completes.
    sent = []
    orig_req = native.ReqQryOrder

    def hold_third(field, rid):
        sent.append(rid)
        return 0

    monkeypatch.setattr(native, "ReqQryOrder", hold_third)
    assert source.due(1000000000.0) and source.begin()
    assert source.advance() is True and len(sent) == 1
    assert source.current() is not None
    # Expire live candidate mid-staging -> fail closed immediately!
    source._coordinator._deadline = 0.0
    assert source.current() is None
    assert source.pending() is True
    # Reconnect while staging holds native pin -> pin must remain fenced until terminal callback!
    assert source.pause_for_reconnect() is False
    assert session._order_query_cut is not None
    native.client._spi.OnRspQryOrder(None, None, sent[0], True)
    assert source.pause_for_reconnect() is True
    assert session._order_query_cut is None
    assert source.close()
    monkeypatch.setattr(native, "ReqQryOrder", orig_req)
