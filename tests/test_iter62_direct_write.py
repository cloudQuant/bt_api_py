"""Iteration 62: SDK write admission and the direct write coordinator."""

from __future__ import annotations

import time

import pytest

from bt_api_base.direct_write_protocol import (
    DirectExecutionEvent,
    DirectExecutionEventKind,
    DirectOrderReservation,
    DirectOrderStatus,
    DirectSubmitResult,
    DirectWriteError,
    NativeOrderIdentity,
    PreDispatchRecord,
    SettlementStatus,
    WriteAdmissionDenied,
    WriteGrant,
    WriteOperation,
)
from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.write_admission import WriteAdmission

ROUTE = "CTP___FUTURE"


def _identity(ref="000000000101", front=1, session=7, generation=3, **extra):
    fields = {
        "order_ref": ref,
        "front_id": front,
        "session_id": session,
        "trading_day": "20261106",
        "exchange_id": "SHFE",
    }
    fields.update(extra)
    return NativeOrderIdentity(
        exchange_name=ROUTE, client_order_id=ref, fields=fields, session_generation=generation
    )


class FakeProvider:
    def __init__(self, calls):
        self.calls = calls
        self.generation = 3
        self.submit_result = None
        self.submit_raises = None
        self.events = []
        self.settlement = SettlementStatus.CONFIRMED
        self.released = []

    def session_generation(self):
        return self.generation

    def trading_day(self):
        return "20261106"

    def reserve_order(self, request):
        self.calls.append("reserve")
        return DirectOrderReservation(identity=_identity(), symbol="rb2701", reservation_id="r1")

    def release_reservation(self, reservation):
        self.calls.append("release")
        self.released.append(reservation.reservation_id)

    def submit_order(self, reservation, request, grant, verifier):
        self.calls.append("submit")
        verifier.consume(
            grant,
            exchange_name=ROUTE,
            operation=WriteOperation.PLACE,
            session_generation=self.generation,
        )
        if self.submit_raises is not None:
            raise self.submit_raises
        return self.submit_result or DirectSubmitResult(sent=True, identity=reservation.identity)

    def cancel_order(self, target, symbol, grant, verifier):
        self.calls.append("cancel")
        verifier.consume(
            grant,
            exchange_name=ROUTE,
            operation=WriteOperation.CANCEL,
            session_generation=self.generation,
        )
        return DirectSubmitResult(sent=True, identity=target)

    def settlement_status(self):
        return self.settlement

    def confirm_settlement(self, grant, verifier):
        self.calls.append("confirm_settlement")
        verifier.consume(
            grant,
            exchange_name=ROUTE,
            operation=WriteOperation.SETTLEMENT_CONFIRM,
            session_generation=self.generation,
        )
        self.settlement = SettlementStatus.CONFIRMED
        return SettlementStatus.CONFIRMED

    def drain_execution_events(self, max_events):
        out, self.events = self.events[:max_events], self.events[max_events:]
        return tuple(out)


class FakePort:
    def __init__(self, calls, result="marker-1", raises=None):
        self.calls = calls
        self.result = result
        self.raises = raises
        self.records = []

    def commit(self, record):
        self.calls.append("commit")
        assert isinstance(record, PreDispatchRecord)
        self.records.append(record)
        if self.raises is not None:
            raise self.raises
        return self.result


def _coordinator(enabled=True):
    calls = []
    provider = FakeProvider(calls)
    admission = WriteAdmission({ROUTE: True} if enabled else None)
    coordinator = DirectWriteCoordinator(admission, lambda name: provider)
    return coordinator, provider, calls


# --------------------------------------------------------------- admission


def test_admission_is_disabled_by_default_and_never_issues():
    admission = WriteAdmission()
    assert admission.is_enabled(ROUTE) is False
    with pytest.raises(WriteAdmissionDenied) as info:
        admission.issue(ROUTE, WriteOperation.PLACE, 1)
    assert info.value.code == "write_admission_disabled"


def test_grant_is_one_shot_and_scope_bound():
    admission = WriteAdmission({ROUTE: True})
    grant = admission.issue(ROUTE, WriteOperation.PLACE, 5)
    admission.consume(grant, exchange_name=ROUTE, operation=WriteOperation.PLACE, session_generation=5)
    with pytest.raises(WriteAdmissionDenied) as info:
        admission.consume(
            grant, exchange_name=ROUTE, operation=WriteOperation.PLACE, session_generation=5
        )
    assert info.value.code == "write_grant_invalid_or_consumed"
    other = admission.issue(ROUTE, WriteOperation.PLACE, 5)
    with pytest.raises(WriteAdmissionDenied) as info:
        admission.consume(
            other, exchange_name=ROUTE, operation=WriteOperation.CANCEL, session_generation=5
        )
    assert info.value.code == "write_grant_scope_mismatch"
    third = admission.issue(ROUTE, WriteOperation.PLACE, 5)
    with pytest.raises(WriteAdmissionDenied) as info:
        admission.consume(
            third, exchange_name=ROUTE, operation=WriteOperation.PLACE, session_generation=6
        )
    assert info.value.code == "write_grant_session_changed"


def test_forged_grant_is_rejected():
    admission = WriteAdmission({ROUTE: True})
    forged = WriteGrant(ROUTE, WriteOperation.PLACE, 1, "forged", time.monotonic_ns())
    with pytest.raises(WriteAdmissionDenied):
        admission.consume(
            forged, exchange_name=ROUTE, operation=WriteOperation.PLACE, session_generation=1
        )


def test_admission_is_environment_neutral():
    # The same rule applies to any route name; no profile or registry input exists.
    admission = WriteAdmission({"CTP___FUTURE": True, "OKX___SWAP": False})
    assert admission.enabled_routes() == ("CTP___FUTURE",)


# --------------------------------------------------------------- coordinator


def test_place_requires_admission_and_never_touches_provider_when_disabled():
    coordinator, provider, calls = _coordinator(enabled=False)
    with pytest.raises(WriteAdmissionDenied):
        coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="s")
    assert calls == []


def test_place_commits_before_submit_and_succeeds():
    coordinator, provider, calls = _coordinator()
    port = FakePort(calls)
    outcome = coordinator.place(ROUTE, object(), commit_port=port, host_scope="uuid-1")
    assert calls == ["reserve", "commit", "submit"]
    assert outcome.sent is True and outcome.marker_id == "marker-1"
    record = port.records[0]
    assert record.operation is WriteOperation.PLACE
    assert record.identity.client_order_id == "000000000101"
    assert record.host_scope == "uuid-1"


@pytest.mark.parametrize("result,raises", [(None, None), ("", None), ("x", OSError("disk"))])
def test_place_without_durable_commit_never_submits(result, raises):
    coordinator, provider, calls = _coordinator()
    outcome = coordinator.place(
        ROUTE, object(), commit_port=FakePort(calls, result=result, raises=raises), host_scope="s"
    )
    assert "submit" not in calls and "release" in calls
    assert outcome.sent is False and outcome.error_code == "pre_dispatch_commit_failed"


def test_place_session_change_after_commit_does_not_send():
    coordinator, provider, calls = _coordinator()

    class Port(FakePort):
        def commit(self, record):
            provider.generation = 4  # reconnect happened while persisting
            return super().commit(record)

    outcome = coordinator.place(ROUTE, object(), commit_port=Port(calls), host_scope="s")
    assert "submit" not in calls
    assert outcome.sent is False and outcome.error_code == "session_changed_before_dispatch"
    assert outcome.marker_id == "marker-1"


def test_place_outcome_unknown_is_reported_as_none():
    coordinator, provider, calls = _coordinator()
    provider.submit_raises = DirectWriteError("ctp_direct_submit_outcome_unknown")
    outcome = coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="s")
    assert outcome.sent is None
    provider.submit_raises = RuntimeError("native crash")
    outcome = coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="s")
    assert outcome.sent is None and outcome.error_code == "direct_submit_outcome_unknown"


def test_place_definite_reject_is_false():
    coordinator, provider, calls = _coordinator()
    provider.submit_raises = DirectWriteError("ctp_direct_gate_unavailable")
    outcome = coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="s")
    assert outcome.sent is False and outcome.error_code == "ctp_direct_gate_unavailable"


def test_cancel_commits_then_sends():
    coordinator, provider, calls = _coordinator()
    outcome = coordinator.cancel(
        ROUTE, _identity(), "rb2701", commit_port=FakePort(calls), host_scope="s"
    )
    assert calls == ["commit", "cancel"] and outcome.sent is True


def test_settlement_confirmed_needs_no_write():
    coordinator, provider, calls = _coordinator()
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="s")
        is SettlementStatus.CONFIRMED
    )
    assert calls == []


def test_settlement_unconfirmed_is_committed_then_confirmed():
    coordinator, provider, calls = _coordinator()
    provider.settlement = SettlementStatus.UNCONFIRMED
    port = FakePort(calls)
    status = coordinator.ensure_settlement(ROUTE, commit_port=port, host_scope="s")
    assert status is SettlementStatus.CONFIRMED
    assert calls == ["commit", "confirm_settlement"]
    assert port.records[0].operation is WriteOperation.SETTLEMENT_CONFIRM


def test_polling_returns_does_not_wait_for_a_settlement_confirmation():
    """A settlement confirmation holds the write lock for its native round trips
    (up to ~31 s on CTP); the strategy thread's return polling must not wait."""
    import threading

    coordinator, provider, calls = _coordinator()
    provider.settlement = SettlementStatus.UNCONFIRMED
    entered, release = threading.Event(), threading.Event()
    confirm = provider.confirm_settlement

    def slow_confirm(grant, verifier):
        entered.set()
        release.wait(10)
        return confirm(grant, verifier)

    provider.confirm_settlement = slow_confirm
    coordinator.provider(ROUTE)  # created before the write, as on a running facade
    settle = threading.Thread(
        target=coordinator.ensure_settlement,
        args=(ROUTE,),
        kwargs={"commit_port": FakePort(calls), "host_scope": "s"},
    )
    settle.start()
    timer = threading.Timer(2.0, release.set)  # bounds the old (blocking) behaviour
    try:
        assert entered.wait(5)
        timer.start()
        provider.events = [_trade_event()]
        began = time.monotonic()
        events = coordinator.poll_events(ROUTE)
        waited = time.monotonic() - began
    finally:
        release.set()
        timer.cancel()
        settle.join(10)
    assert waited < 0.5
    assert [e.event.kind.value for e in events] == ["trade"]
    assert provider.settlement is SettlementStatus.CONFIRMED


def test_settlement_unknown_is_not_written():
    coordinator, provider, calls = _coordinator()
    provider.settlement = SettlementStatus.UNKNOWN
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="s")
        is SettlementStatus.UNKNOWN
    )
    assert calls == []


# --------------------------------------------------------------- correlation


def _order_event(status, sys_id=None, filled=0.0, sequence=1):
    ident = _identity(**({"order_sys_id": sys_id} if sys_id else {}))
    return DirectExecutionEvent(
        kind=DirectExecutionEventKind.ORDER,
        exchange_name=ROUTE,
        symbol="rb2701",
        identity=ident,
        exchange_order_id=sys_id,
        status=status,
        filled_qty=filled,
        raw_status=status.value,
        sequence=sequence,
    )


def _trade_event(trade_id="T1", sys_id="S1"):
    ident = NativeOrderIdentity(
        exchange_name=ROUTE,
        client_order_id="000000000101",
        fields={"order_ref": "000000000101", "exchange_id": "SHFE", "order_sys_id": sys_id},
    )
    return DirectExecutionEvent(
        kind=DirectExecutionEventKind.TRADE,
        exchange_name=ROUTE,
        symbol="rb2701",
        identity=ident,
        exchange_order_id=sys_id,
        side="buy",
        trade_id=trade_id,
        trade_price=3000.0,
        trade_qty=1.0,
    )


def test_returns_correlate_by_session_identity_then_venue_identity_and_dedupe():
    coordinator, provider, calls = _coordinator()
    coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="uuid-1")
    provider.events = [
        _order_event(DirectOrderStatus.SUBMITTED),
        _order_event(DirectOrderStatus.ACCEPTED, sys_id="S1"),
        _order_event(DirectOrderStatus.ACCEPTED, sys_id="S1"),  # duplicate replay
        _trade_event(),
        _trade_event(),  # duplicate trade
    ]
    events = coordinator.poll_events(ROUTE)
    assert [e.event.kind.value for e in events] == ["order", "order", "trade"]
    assert all(e.matched and e.marker_id == "marker-1" for e in events)
    assert events[-1].evidence == "direct_identity_match"


def test_unmatched_returns_are_reported_unmatched():
    coordinator, provider, calls = _coordinator()
    provider.events = [_trade_event(trade_id="X", sys_id="OTHER")]
    (event,) = coordinator.poll_events(ROUTE)
    assert event.matched is False and event.evidence == "unmatched" and event.marker_id is None


def test_restored_identity_correlates_after_restart():
    coordinator, provider, calls = _coordinator()
    coordinator.register_restored_identity(ROUTE, _identity(), "marker-old", "uuid-1")
    provider.events = [_order_event(DirectOrderStatus.CANCELLED, sys_id="S1")]
    (event,) = coordinator.poll_events(ROUTE)
    assert event.matched and event.marker_id == "marker-old"
