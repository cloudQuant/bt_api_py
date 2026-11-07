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


def test_closing_the_coordinator_closes_its_providers_and_survives_a_failure():
    closed = []

    class ClosingProvider(FakeProvider):
        def __init__(self, calls, name, fail=False):
            super().__init__(calls)
            self.name, self.fail = name, fail

        def close(self):
            closed.append(self.name)
            if self.fail:
                raise RuntimeError("synthetic close failure")

    providers = {
        "A": ClosingProvider([], "A", fail=True),
        "B": ClosingProvider([], "B"),
    }
    coordinator = DirectWriteCoordinator(WriteAdmission({"A": True, "B": True}), providers.get)
    coordinator.provider("A")
    coordinator.provider("B")
    coordinator.close()
    assert sorted(closed) == ["A", "B"]


def test_facade_close_closes_the_direct_write_providers():
    from bt_api_py.bt_api import BtApi

    api = BtApi({})
    closed = []

    class Provider:
        def close(self):
            closed.append(True)

    coordinator = DirectWriteCoordinator(WriteAdmission(None), lambda _name: Provider())
    coordinator.provider(ROUTE)
    api._direct_write_coordinator = coordinator
    api.close()
    assert closed == [True]


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


# ------------------------------------------- weak (trading day, OrderRef) keys


def _session_order_event(status, sys_id, front=1, session=7, sequence=1):
    """A CTP order return carrying its own FrontID/SessionID and OrderSysID."""

    ident = _identity(front=front, session=session, order_sys_id=sys_id)
    return DirectExecutionEvent(
        kind=DirectExecutionEventKind.ORDER,
        exchange_name=ROUTE,
        symbol="rb2701",
        identity=ident,
        exchange_order_id=sys_id,
        status=status,
        filled_qty=0.0,
        raw_status=status.value,
        sequence=sequence,
    )


def _day_trade_event(trade_id, sys_id, ref="000000000101"):
    """A CTP trade return as projected: OrderRef and TradingDay, no FrontID/SessionID."""

    ident = NativeOrderIdentity(
        exchange_name=ROUTE,
        client_order_id=ref,
        fields={
            "order_ref": ref,
            "exchange_id": "SHFE",
            "trading_day": "20261106",
            "order_sys_id": sys_id,
        },
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


def _venue_only_trade_event(trade_id, sys_id):
    """A trade naming only ExchangeID + OrderSysID (probes the learned venue keys)."""

    ident = NativeOrderIdentity(
        exchange_name=ROUTE,
        client_order_id=None,
        fields={"exchange_id": "SHFE", "order_sys_id": sys_id},
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


def _assert_not_attributed(item, evidence):
    assert item.matched is False
    assert item.evidence == evidence
    assert item.marker_id is None and item.host_scope is None


def test_foreign_session_order_return_with_our_day_and_order_ref_is_not_attributed():
    """OrderRef is unique only within one FrontID/SessionID (INV63-14)."""

    coordinator, provider, calls = _coordinator()
    coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="uuid-1")
    # Another terminal of the same account: same trading day and OrderRef.
    provider.events = [_session_order_event(DirectOrderStatus.ACCEPTED, "F1", front=2, session=9)]
    (foreign,) = coordinator.poll_events(ROUTE)
    _assert_not_attributed(foreign, "weak_key_candidate")
    # Its OrderSysID was not learned as ours: neither a full trade return nor
    # a venue-only probe on F1 is attributed.
    provider.events = [_day_trade_event("FT1", "F1"), _venue_only_trade_event("FT2", "F1")]
    full, venue_only = coordinator.poll_events(ROUTE)
    _assert_not_attributed(full, "weak_key_candidate")
    _assert_not_attributed(venue_only, "unmatched")


def test_own_trade_before_its_order_return_is_unmatched_then_matched_after_it():
    coordinator, provider, calls = _coordinator()
    coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="uuid-1")
    # The fill overtakes its order return: only the weak day key names it.
    provider.events = [_day_trade_event("T1", "S1")]
    (early,) = coordinator.poll_events(ROUTE)
    _assert_not_attributed(early, "weak_key_candidate")
    # The order return of our own session matches strongly and teaches S1.
    provider.events = [
        _session_order_event(DirectOrderStatus.ACCEPTED, "S1"),
        _day_trade_event("T2", "S1"),
        _venue_only_trade_event("T3", "S1"),
    ]
    order, trade, venue_only = coordinator.poll_events(ROUTE)
    for item in (order, trade, venue_only):
        assert item.matched is True and item.evidence == "direct_identity_match"
        assert item.marker_id == "marker-1" and item.host_scope == "uuid-1"


def test_own_return_with_unreadable_session_fields_keeps_the_day_key_weak():
    """A strongly matched (venue) order return whose FrontID/SessionID are
    unreadable is still session-scoped: learning from it never turns the
    (trading day, OrderRef) key into an attribution key."""

    coordinator, provider, calls = _coordinator()
    coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="uuid-1")
    provider.events = [
        _session_order_event(DirectOrderStatus.ACCEPTED, "S1"),
        _session_order_event(DirectOrderStatus.PARTIALLY_FILLED, "S1", front=None, session=None),
    ]
    own, unreadable = coordinator.poll_events(ROUTE)
    assert own.matched and unreadable.matched and unreadable.marker_id == "marker-1"
    # Another session's order with our day and OrderRef is still nobody's.
    provider.events = [_session_order_event(DirectOrderStatus.ACCEPTED, "F2", front=2, session=9)]
    (foreign,) = coordinator.poll_events(ROUTE)
    _assert_not_attributed(foreign, "weak_key_candidate")


def test_order_error_without_session_fields_is_only_a_weak_candidate():
    """``OnErrRtnOrderInsert`` (private flow) has no FrontID/SessionID."""

    coordinator, provider, calls = _coordinator()
    coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="uuid-1")
    ident = NativeOrderIdentity(
        exchange_name=ROUTE,
        client_order_id="000000000101",
        fields={"order_ref": "000000000101", "exchange_id": "SHFE", "trading_day": "20261106"},
    )
    provider.events = [
        DirectExecutionEvent(
            kind=DirectExecutionEventKind.ORDER_ERROR,
            exchange_name=ROUTE,
            symbol="rb2701",
            identity=ident,
            error_code="22",
        )
    ]
    (event,) = coordinator.poll_events(ROUTE)
    _assert_not_attributed(event, "weak_key_candidate")


def test_restored_identity_matches_only_returns_of_its_original_session():
    coordinator, provider, calls = _coordinator()
    coordinator.register_restored_identity(ROUTE, _identity(), "marker-old", "uuid-1")
    provider.events = [
        _session_order_event(DirectOrderStatus.ACCEPTED, "F9", front=4, session=11),
        _day_trade_event("T0", "S1"),
    ]
    foreign, early = coordinator.poll_events(ROUTE)
    _assert_not_attributed(foreign, "weak_key_candidate")
    _assert_not_attributed(early, "weak_key_candidate")
    provider.events = [
        _session_order_event(DirectOrderStatus.PARTIALLY_FILLED, "S1"),
        _day_trade_event("T1", "S1"),
    ]
    order, trade = coordinator.poll_events(ROUTE)
    assert order.matched and order.marker_id == "marker-old" and order.host_scope == "uuid-1"
    assert trade.matched and trade.marker_id == "marker-old"


OKX_ROUTE = "OKX___SWAP"


def _okx_identity(client="bt1a2b3c4d5e6fs1", day="20261106", **extra):
    fields = {"inst_id": "BTC-USDT-SWAP", "trading_day": day}
    fields.update(extra)
    return NativeOrderIdentity(
        exchange_name=OKX_ROUTE, client_order_id=client, fields=fields, session_generation=1
    )


class OkxShapedProvider(FakeProvider):
    """Client order ids unique per account: no FrontID/SessionID in identities."""

    def session_generation(self):
        return 1

    def reserve_order(self, request):
        self.calls.append("reserve")
        return DirectOrderReservation(
            identity=_okx_identity(), symbol="BTC-USDT-SWAP", reservation_id="r-okx"
        )

    def submit_order(self, reservation, request, grant, verifier):
        self.calls.append("submit")
        verifier.consume(
            grant, exchange_name=OKX_ROUTE, operation=WriteOperation.PLACE, session_generation=1
        )
        return DirectSubmitResult(sent=True, identity=reservation.identity)


def test_identities_without_session_fields_keep_their_day_key_strong():
    calls = []
    provider = OkxShapedProvider(calls)
    coordinator = DirectWriteCoordinator(WriteAdmission({OKX_ROUTE: True}), lambda _n: provider)
    outcome = coordinator.place(OKX_ROUTE, object(), commit_port=FakePort(calls), host_scope="u")
    assert outcome.sent is True
    order_identity = _okx_identity(ord_id="901")
    provider.events = [
        DirectExecutionEvent(
            kind=DirectExecutionEventKind.ORDER,
            exchange_name=OKX_ROUTE,
            symbol="BTC-USDT-SWAP",
            identity=order_identity,
            exchange_order_id="901",
            status=DirectOrderStatus.ACCEPTED,
            raw_status="live",
        ),
        DirectExecutionEvent(
            kind=DirectExecutionEventKind.TRADE,
            exchange_name=OKX_ROUTE,
            symbol="BTC-USDT-SWAP",
            identity=order_identity,
            exchange_order_id="901",
            side="buy",
            trade_id="t-1",
            trade_price=100.0,
            trade_qty=1.0,
        ),
    ]
    events = coordinator.poll_events(OKX_ROUTE)
    assert [e.event.kind.value for e in events] == ["order", "trade"]
    for item in events:
        assert item.matched is True and item.evidence == "direct_identity_match"
        assert item.marker_id == "marker-1" and item.host_scope == "u"
    # Another client order id of the same day is still nobody's.
    provider.events = [
        DirectExecutionEvent(
            kind=DirectExecutionEventKind.ORDER,
            exchange_name=OKX_ROUTE,
            symbol="BTC-USDT-SWAP",
            identity=_okx_identity(client="manual77"),
            status=DirectOrderStatus.ACCEPTED,
            raw_status="live",
        )
    ]
    (other,) = coordinator.poll_events(OKX_ROUTE)
    _assert_not_attributed(other, "unmatched")


# ------------------------------------------------------- provider event drops


def test_dropped_events_report_the_provider_counter():
    coordinator, provider, _calls = _coordinator()
    assert coordinator.dropped_events(ROUTE) is None  # provider not created yet
    coordinator.provider(ROUTE)
    assert coordinator.dropped_events(ROUTE) is None  # provider without the counter
    provider.dropped_events = 0
    assert coordinator.dropped_events(ROUTE) == 0
    provider.dropped_events = 7
    assert coordinator.dropped_events(ROUTE) == 7


@pytest.mark.parametrize("value", [True, -1, 2.0, "3", None])
def test_dropped_events_refuse_a_counter_that_is_not_a_count(value):
    coordinator, provider, _calls = _coordinator()
    coordinator.provider(ROUTE)
    provider.dropped_events = value
    assert coordinator.dropped_events(ROUTE) is None


def test_dropped_events_of_a_failing_counter_are_unknown():
    class Provider:
        @property
        def dropped_events(self):
            raise RuntimeError("counter unavailable")

    coordinator = DirectWriteCoordinator(WriteAdmission(None), lambda _name: Provider())
    coordinator.provider(ROUTE)
    assert coordinator.dropped_events(ROUTE) is None


def test_facade_reports_direct_execution_dropped_events():
    from bt_api_py.bt_api import BtApi

    class Provider:
        dropped_events = 5

    api = BtApi({})
    assert api.direct_execution_dropped_events(ROUTE) is None
    coordinator = DirectWriteCoordinator(WriteAdmission(None), lambda _name: Provider())
    coordinator.provider(ROUTE)
    api._direct_write_coordinator = coordinator
    assert api.direct_execution_dropped_events(ROUTE) == 5
    assert api.direct_execution_dropped_events("OKX___SWAP") is None
