"""Iteration 62: SDK write admission and the direct write coordinator."""

from __future__ import annotations

import dataclasses
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


def _session_order_event(status, sys_id, front=1, session=7, sequence=1, ref="000000000101"):
    """A CTP order return carrying its own FrontID/SessionID and OrderSysID."""

    ident = _identity(ref=ref, front=front, session=session, order_sys_id=sys_id)
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


# ------------------------------------------- trading-day de-duplication scope
#
# Iteration 62 review §4 item 5: CTP reuses OrderSysID/TradeID (and OrderRef,
# FrontID/SessionID) every trading day, so a later day's return can carry
# exactly the de-duplication fields of an earlier day's return.  A provider
# declaring ``execution_ids_trading_day_scoped = True`` gets per-trading-day
# de-duplication; undeclared routes (OKX/Binance) keep undated keys so a GTC
# order's returns replayed on a later UTC day are still dropped.

DAY1, DAY2 = "20261106", "20261109"


class DayScopedProvider(FakeProvider):
    """CTP-like: venue execution ids are unique only within one trading day."""

    execution_ids_trading_day_scoped = True


def _day_scoped_coordinator():
    calls = []
    provider = DayScopedProvider(calls)
    coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda _n: provider)
    return coordinator, provider, calls


def _on_day(event, day, event_day=...):
    """``event`` as returned on ``day``: the identity field and, unless given
    separately, the event field (both set by the CTP projection)."""

    fields = dict(event.identity.fields)
    if day is None:
        fields.pop("trading_day", None)
    else:
        fields["trading_day"] = day
    identity = dataclasses.replace(event.identity, fields=fields)
    return dataclasses.replace(
        event, identity=identity, trading_day=day if event_day is ... else event_day
    )


def _ctp_returns(day):
    """An order return, a fill and an insert error whose venue ids recur daily."""

    error = DirectExecutionEvent(
        kind=DirectExecutionEventKind.ORDER_ERROR,
        exchange_name=ROUTE,
        symbol="rb2701",
        identity=_identity(ref="000000000102"),
        status=DirectOrderStatus.REJECTED,
        error_code="31",
    )
    return [
        _on_day(_session_order_event(DirectOrderStatus.ACCEPTED, "S1"), day),
        _on_day(_day_trade_event("T1", "S1"), day),
        _on_day(error, day),
    ]


def test_day_scoped_route_delivers_equal_returns_of_two_trading_days():
    coordinator, provider, _calls = _day_scoped_coordinator()
    day1, day2 = _ctp_returns(DAY1), _ctp_returns(DAY2)
    # The venue reused every id: the provider-level keys are identical.
    assert [e.dedup_key() for e in day1] == [e.dedup_key() for e in day2]
    provider.events = list(day1)
    assert len(coordinator.poll_events(ROUTE)) == 3
    provider.events = list(day2)
    delivered = coordinator.poll_events(ROUTE)
    assert [item.event.kind.value for item in delivered] == ["order", "trade", "order_error"]
    assert {item.event.trading_day for item in delivered} == {DAY2}


def test_day_scoped_route_still_drops_an_exact_repeat_of_the_same_day():
    coordinator, provider, _calls = _day_scoped_coordinator()
    day1 = _ctp_returns(DAY1)
    provider.events = day1 + day1  # e.g. a RESTART replay inside one drain
    assert len(coordinator.poll_events(ROUTE)) == 3
    provider.events = list(day1)  # a later replay of the same day
    assert coordinator.poll_events(ROUTE) == ()
    day2 = _ctp_returns(DAY2)
    provider.events = day2 + day2 + day1  # day 2 once; day 1 is still known
    delivered = coordinator.poll_events(ROUTE)
    assert len(delivered) == 3 and {item.event.trading_day for item in delivered} == {DAY2}


class OkxUndeclared(OkxShapedProvider):
    pass


class OkxDeclaredFalse(OkxShapedProvider):
    execution_ids_trading_day_scoped = False


@pytest.mark.parametrize("provider_class", [OkxUndeclared, OkxDeclaredFalse])
def test_undeclared_route_still_drops_a_cross_day_repeat(provider_class):
    """An OKX GTC order's returns replayed on a later UTC day stay replays."""

    provider = provider_class([])
    coordinator = DirectWriteCoordinator(WriteAdmission(None), lambda _n: provider)
    identity = _okx_identity(day="20261106", ord_id="901")
    order = DirectExecutionEvent(
        kind=DirectExecutionEventKind.ORDER,
        exchange_name=OKX_ROUTE,
        symbol="BTC-USDT-SWAP",
        identity=identity,
        exchange_order_id="901",
        status=DirectOrderStatus.PARTIALLY_FILLED,
        filled_qty=1.0,
        raw_status="partially_filled",
        trading_day="20261106",
    )
    trade = DirectExecutionEvent(
        kind=DirectExecutionEventKind.TRADE,
        exchange_name=OKX_ROUTE,
        symbol="BTC-USDT-SWAP",
        identity=identity,
        exchange_order_id="901",
        side="buy",
        trade_id="t-1",
        trade_price=100.0,
        trade_qty=1.0,
        trading_day="20261106",
    )
    provider.events = [order, trade]
    assert len(coordinator.poll_events(OKX_ROUTE)) == 2
    provider.events = [_on_day(order, "20261107"), _on_day(trade, "20261107")]
    assert coordinator.poll_events(OKX_ROUTE) == ()


@pytest.mark.parametrize(
    "fields_day,event_day",
    [
        (None, None),  # no trading day at all
        (DAY1, DAY2),  # the identity and the event disagree
        ("2026-11-06", ...),  # not YYYYMMDD
        ("", ...),
        (20261106, None),  # not text
    ],
)
def test_day_scoped_route_never_deduplicates_a_return_without_one_trading_day(
    fields_day, event_day
):
    """Such a return could belong to any day: it is delivered every time
    (never dropped against another day's equal return); the host keeps its
    own fill keys."""

    coordinator, provider, _calls = _day_scoped_coordinator()
    trade = _on_day(_day_trade_event("T1", "S1"), fields_day, event_day)
    provider.events = [_on_day(_day_trade_event("T1", "S1"), DAY1), trade, trade]
    assert len(coordinator.poll_events(ROUTE)) == 3
    provider.events = [trade]
    assert len(coordinator.poll_events(ROUTE)) == 1


def test_day_scoped_seen_set_stays_bounded_and_skips_undated_returns(monkeypatch):
    monkeypatch.setattr("bt_api_py.direct_write._DEDUP_CAPACITY", 2)
    coordinator, provider, _calls = _day_scoped_coordinator()
    first = _on_day(_day_trade_event("T1", "S1"), DAY1)
    undated = [_on_day(_day_trade_event(f"U{n}", "S1"), None) for n in range(5)]
    provider.events = [first] + undated
    assert len(coordinator.poll_events(ROUTE)) == 6
    # Undated returns took no slot: the first key is still remembered.
    provider.events = [first]
    assert coordinator.poll_events(ROUTE) == ()
    later = [_on_day(_day_trade_event(f"T{n}", "S1"), DAY1) for n in (2, 3)]
    provider.events = list(later)
    assert len(coordinator.poll_events(ROUTE)) == 2
    # Capacity 2: the oldest key was evicted, the newest are still dropped.
    provider.events = later + [first]
    assert [item.event.trade_id for item in coordinator.poll_events(ROUTE)] == ["T1"]


class _RaisingDeclaration(DayScopedProvider):
    @property
    def execution_ids_trading_day_scoped(self):
        raise RuntimeError("declaration unavailable")


@pytest.mark.parametrize("value", ["yes", 1, 0, None, (lambda: True), _RaisingDeclaration])
def test_invalid_day_scope_declaration_fails_closed(value):
    calls = []
    if value is _RaisingDeclaration:
        provider = _RaisingDeclaration(calls)
    else:
        provider = FakeProvider(calls)
        provider.execution_ids_trading_day_scoped = value
    provider.events = _ctp_returns(DAY1)
    coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda _n: provider)
    for attempt in (
        lambda: coordinator.poll_events(ROUTE),
        lambda: coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="u"),
    ):
        with pytest.raises(DirectWriteError) as info:
            attempt()
        assert info.value.code == "direct_write_provider_declaration_invalid"
    assert calls == [] and len(provider.events) == 3  # nothing reserved, nothing drained
    assert coordinator.dropped_events(ROUTE) is None  # the provider was never registered


def test_weak_key_correlation_is_unchanged_on_a_day_scoped_route():
    """71547da7: a (trading day, OrderRef) hit of a session-scoped identity is
    only a candidate; a strong own-session return attributes and teaches the
    venue id."""

    coordinator, provider, calls = _day_scoped_coordinator()
    coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="uuid-1")
    provider.events = [
        _session_order_event(DirectOrderStatus.ACCEPTED, "F1", front=2, session=9),
        _day_trade_event("T1", "S1"),
    ]
    foreign, early = coordinator.poll_events(ROUTE)
    _assert_not_attributed(foreign, "weak_key_candidate")
    _assert_not_attributed(early, "weak_key_candidate")
    provider.events = [
        _session_order_event(DirectOrderStatus.ACCEPTED, "S1"),
        _day_trade_event("T2", "S1"),
        _on_day(_venue_only_trade_event("T3", "S1"), DAY1),  # the learned venue id
        _venue_only_trade_event("T4", "S1"),  # names no day: never attributed here
        _day_trade_event("FT1", "F1"),
    ]
    order, trade, venue_only, undated, foreign_fill = coordinator.poll_events(ROUTE)
    for item in (order, trade, venue_only):
        assert item.matched is True and item.evidence == "direct_identity_match"
        assert item.marker_id == "marker-1" and item.host_scope == "uuid-1"
    _assert_not_attributed(undated, "unmatched")
    _assert_not_attributed(foreign_fill, "weak_key_candidate")


# ------------------------------------- trading-day scoped strong keys (INV63-14)
#
# CTP restarts OrderSysID and OrderRef every trading day and SessionIDs can
# repeat, so on a route declaring ``execution_ids_trading_day_scoped`` the
# strong keys are (trading day, FrontID, SessionID, OrderRef) and (trading day,
# ExchangeID, OrderSysID).  In a process running across a trading-day switch a
# day-2 return reusing day-1 ids must not be attributed to the day-1 order.


def _identity_on(day, **extra):
    """``_identity()`` reserved on trading day ``day`` (``None``: no day field)."""

    identity = _identity(**extra)
    fields = dict(identity.fields)
    if day is None:
        fields.pop("trading_day")
    else:
        fields["trading_day"] = day
    return dataclasses.replace(identity, fields=fields)


class _DayReservingProvider(DayScopedProvider):
    """Reserves FrontID/SessionID/OrderRef 1/7/000000000101 on ``self.day``."""

    day = DAY1

    def reserve_order(self, request):
        self.calls.append("reserve")
        return DirectOrderReservation(
            identity=_identity_on(self.day), symbol="rb2701", reservation_id="r1"
        )


class _UndeclaredDayReservingProvider(_DayReservingProvider):
    execution_ids_trading_day_scoped = False


def _sys_id_only_trade_event(trade_id, sys_id, day):
    """A fill whose identity names no OrderSysID: only ``exchange_order_id``
    carries it (probes the venue-key fallback lookup)."""

    return DirectExecutionEvent(
        kind=DirectExecutionEventKind.TRADE,
        exchange_name=ROUTE,
        symbol="rb2701",
        identity=NativeOrderIdentity(
            exchange_name=ROUTE,
            client_order_id=None,
            fields={"exchange_id": "SHFE", "trading_day": day},
        ),
        exchange_order_id=sys_id,
        side="buy",
        trade_id=trade_id,
        trade_price=3000.0,
        trade_qty=1.0,
        trading_day=day,
    )


def _assert_attributed(item, marker_id, host_scope="uuid-1"):
    assert item.matched is True and item.evidence == "direct_identity_match"
    assert item.marker_id == marker_id and item.host_scope == host_scope


def test_day_scoped_route_never_attributes_a_later_day_return_reusing_the_order_sys_id():
    coordinator, provider, calls = _day_scoped_coordinator()
    coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="uuid-1")
    provider.events = [_on_day(_session_order_event(DirectOrderStatus.ACCEPTED, "S1"), DAY1)]
    (own,) = coordinator.poll_events(ROUTE)
    _assert_attributed(own, "marker-1")  # teaches (DAY1, SHFE, S1)
    # Day 2: another order (another session and OrderRef) is given S1 again.
    provider.events = [
        _on_day(
            _session_order_event(
                DirectOrderStatus.ACCEPTED, "S1", front=2, session=9, ref="000000000555"
            ),
            DAY2,
        ),
        _on_day(_day_trade_event("T9", "S1", ref="000000000555"), DAY2),
        _on_day(_venue_only_trade_event("T8", "S1"), DAY2),
        _sys_id_only_trade_event("T7", "S1", DAY2),
    ]
    later = coordinator.poll_events(ROUTE)
    assert len(later) == 4
    for item in later:
        _assert_not_attributed(item, "unmatched")
    # Day-1 returns of the own order still match, through every strong key.
    provider.events = [
        _on_day(_day_trade_event("T1", "S1"), DAY1),
        _on_day(_venue_only_trade_event("T2", "S1"), DAY1),
        _sys_id_only_trade_event("T3", "S1", DAY1),
    ]
    same_day = coordinator.poll_events(ROUTE)
    assert len(same_day) == 3
    for item in same_day:
        _assert_attributed(item, "marker-1")


def test_day_scoped_route_never_attributes_a_later_day_return_reusing_the_session_triple():
    coordinator, provider, calls = _day_scoped_coordinator()
    coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="uuid-1")
    # Day 2: a new login was given FrontID/SessionID 1/7 again and OrderRef
    # restarted, so another order carries the day-1 session triple.
    provider.events = [
        _on_day(_session_order_event(DirectOrderStatus.ACCEPTED, "S7"), DAY2),
        _on_day(_day_trade_event("T7", "S7"), DAY2),
        _on_day(_venue_only_trade_event("T6", "S7"), DAY2),
    ]
    later = coordinator.poll_events(ROUTE)
    assert len(later) == 3
    for item in later:
        _assert_not_attributed(item, "unmatched")
    # The triple on its own day is still ours, and teaches its venue id.
    provider.events = [
        _on_day(_session_order_event(DirectOrderStatus.ACCEPTED, "S1"), DAY1),
        _on_day(_venue_only_trade_event("T1", "S1"), DAY1),
    ]
    order, fill = coordinator.poll_events(ROUTE)
    _assert_attributed(order, "marker-1")
    _assert_attributed(fill, "marker-1")


def test_day_scoped_route_learns_the_venue_id_on_the_day_named_only_by_the_event():
    """The learned venue key takes the return's validated day, also when only
    the event field (not the identity) names it."""

    coordinator, provider, calls = _day_scoped_coordinator()
    coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="uuid-1")
    order = _on_day(_session_order_event(DirectOrderStatus.ACCEPTED, "S1"), None, DAY1)
    assert "trading_day" not in order.identity.fields and order.trading_day == DAY1
    provider.events = [order]
    (own,) = coordinator.poll_events(ROUTE)
    _assert_attributed(own, "marker-1")
    provider.events = [
        _on_day(_venue_only_trade_event("T1", "S1"), DAY1),
        _on_day(_venue_only_trade_event("T2", "S1"), DAY2),
    ]
    same_day, later = coordinator.poll_events(ROUTE)
    _assert_attributed(same_day, "marker-1")
    _assert_not_attributed(later, "unmatched")


def test_day_scoped_route_keeps_own_orders_of_two_days_with_identical_ids_apart():
    calls = []
    provider = _DayReservingProvider(calls)
    coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda _n: provider)
    coordinator.place(
        ROUTE, object(), commit_port=FakePort(calls, result="marker-day1"), host_scope="uuid-1"
    )
    provider.events = [_on_day(_session_order_event(DirectOrderStatus.ACCEPTED, "S1"), DAY1)]
    (first,) = coordinator.poll_events(ROUTE)
    _assert_attributed(first, "marker-day1")
    provider.day = DAY2  # the same triple is reserved again; the venue reuses S1
    coordinator.place(
        ROUTE, object(), commit_port=FakePort(calls, result="marker-day2"), host_scope="uuid-2"
    )
    provider.events = [
        _on_day(_session_order_event(DirectOrderStatus.ACCEPTED, "S1"), DAY2),
        _on_day(_day_trade_event("T1", "S1"), DAY2),
        _on_day(_day_trade_event("T1", "S1"), DAY1),  # a late day-1 fill (query replay)
    ]
    day2_order, day2_fill, day1_fill = coordinator.poll_events(ROUTE)
    _assert_attributed(day2_order, "marker-day2", "uuid-2")
    _assert_attributed(day2_fill, "marker-day2", "uuid-2")
    _assert_attributed(day1_fill, "marker-day1", "uuid-1")


def test_restored_identity_on_a_day_scoped_route_matches_only_returns_of_its_day():
    coordinator, provider, _calls = _day_scoped_coordinator()
    restored = _identity_on(DAY1, order_sys_id="S1")
    coordinator.register_restored_identity(ROUTE, restored, "marker-old", "uuid-1")
    provider.events = [
        _on_day(_session_order_event(DirectOrderStatus.ACCEPTED, "S1"), DAY2),
        _on_day(_venue_only_trade_event("T9", "S1"), DAY2),
        _on_day(_session_order_event(DirectOrderStatus.PARTIALLY_FILLED, "S1"), DAY1),
        _on_day(_venue_only_trade_event("T1", "S1"), DAY1),
        _on_day(_day_trade_event("T2", "S1"), DAY1),
    ]
    later_order, later_fill, order, fill, trade = coordinator.poll_events(ROUTE)
    _assert_not_attributed(later_order, "unmatched")
    _assert_not_attributed(later_fill, "unmatched")
    for item in (order, fill, trade):
        _assert_attributed(item, "marker-old")


_UNREADABLE_DAYS = [None, "2026-11-06", "", 20261106]


@pytest.mark.parametrize("day", _UNREADABLE_DAYS)
def test_day_scoped_route_refuses_an_order_without_one_trading_day(day):
    """Its returns could never be attributed: refused before any host marker."""

    calls = []
    provider = _DayReservingProvider(calls)
    provider.day = day
    coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda _n: provider)
    port = FakePort(calls)
    outcome = coordinator.place(ROUTE, object(), commit_port=port, host_scope="uuid-1")
    assert outcome.sent is False and outcome.marker_id is None
    assert outcome.error_code == "direct_identity_trading_day_unreadable"
    assert calls == ["reserve", "release"] and port.records == []


def test_undeclared_route_still_sends_an_order_without_a_trading_day():
    calls = []
    provider = _UndeclaredDayReservingProvider(calls)
    provider.day = None
    coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda _n: provider)
    outcome = coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="u")
    assert outcome.sent is True and outcome.error_code is None
    assert calls == ["reserve", "commit", "submit"]


@pytest.mark.parametrize("day", _UNREADABLE_DAYS)
def test_day_scoped_route_refuses_to_restore_an_identity_without_one_trading_day(day):
    coordinator, provider, _calls = _day_scoped_coordinator()
    with pytest.raises(DirectWriteError) as info:
        coordinator.register_restored_identity(
            ROUTE, _identity_on(day, order_sys_id="S1"), "marker-old", "uuid-1"
        )
    assert info.value.code == "direct_identity_trading_day_unreadable"
    # Nothing was registered: its returns of a valid day stay nobody's.
    provider.events = [
        _on_day(_session_order_event(DirectOrderStatus.ACCEPTED, "S1"), DAY1),
        _on_day(_venue_only_trade_event("T1", "S1"), DAY1),
    ]
    for item in coordinator.poll_events(ROUTE):
        _assert_not_attributed(item, "unmatched")


@pytest.mark.parametrize(
    "fields_day,event_day",
    [
        (None, None),  # no trading day at all
        (DAY1, DAY2),  # the identity and the event disagree
        ("2026-11-06", ...),  # not YYYYMMDD
        (20261106, None),  # not text
        (None, [DAY1]),  # not text (and not hashable)
    ],
)
def test_day_scoped_route_never_attributes_a_return_without_one_trading_day(fields_day, event_day):
    coordinator, provider, calls = _day_scoped_coordinator()
    coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="uuid-1")
    # An identity without session fields: its (trading day, OrderRef) key is strong.
    no_session = NativeOrderIdentity(
        exchange_name=ROUTE,
        client_order_id="000000000201",
        fields={"order_ref": "000000000201", "exchange_id": "SHFE", "trading_day": DAY1},
    )
    coordinator.register_restored_identity(ROUTE, no_session, "marker-2", "uuid-1")
    provider.events = [_on_day(_session_order_event(DirectOrderStatus.ACCEPTED, "S1"), DAY1)]
    (own,) = coordinator.poll_events(ROUTE)
    _assert_attributed(own, "marker-1")  # teaches (DAY1, SHFE, S1)
    provider.events = [
        _on_day(
            _session_order_event(DirectOrderStatus.PARTIALLY_FILLED, "S1"), fields_day, event_day
        ),
        _on_day(_day_trade_event("T1", "S1"), fields_day, event_day),
        _on_day(_venue_only_trade_event("T2", "S1"), fields_day, event_day),
        _on_day(_day_trade_event("T3", "S9", ref="000000000201"), fields_day, event_day),
    ]
    items = coordinator.poll_events(ROUTE)
    assert len(items) == 4
    for item in items:
        assert item.matched is False and item.evidence != "direct_identity_match"
        assert item.marker_id is None and item.host_scope is None
    # Control: the same fill of one readable day is attributed.
    provider.events = [_on_day(_day_trade_event("T4", "S9", ref="000000000201"), DAY1)]
    (control,) = coordinator.poll_events(ROUTE)
    _assert_attributed(control, "marker-2")


def test_undeclared_route_keeps_its_day_less_session_and_venue_keys():
    """OKX/Binance-like routes: the strong keys hold no trading day, exactly as
    before, so returns naming another day or none are still attributed."""

    coordinator, provider, calls = _coordinator()
    coordinator.place(ROUTE, object(), commit_port=FakePort(calls), host_scope="uuid-1")
    provider.events = [_on_day(_session_order_event(DirectOrderStatus.ACCEPTED, "S1"), DAY1)]
    (own,) = coordinator.poll_events(ROUTE)
    _assert_attributed(own, "marker-1")
    provider.events = [
        _on_day(_session_order_event(DirectOrderStatus.PARTIALLY_FILLED, "S1"), DAY2),
        _on_day(_venue_only_trade_event("T1", "S1"), DAY2),
        _venue_only_trade_event("T2", "S1"),
        _sys_id_only_trade_event("T3", "S1", DAY2),
    ]
    items = coordinator.poll_events(ROUTE)
    assert len(items) == 4
    for item in items:
        _assert_attributed(item, "marker-1")
    strong = coordinator._correlators[ROUTE]._by_key
    assert ("session", ROUTE, 1, 7, "000000000101") in strong
    assert ("venue", ROUTE, "SHFE", "S1") in strong


# --------------------------------------- real provider declarations (cross-package)


class _CtpTrader:
    connection_generation = 4

    def __init__(self):
        self.listeners = []

    def add_execution_listener(self, listener):
        self.listeners.append(listener)


def _ctp_trade_snapshot(day, *, native_day=True):
    snapshot = {
        "InstrumentID": "rb2701",
        "ExchangeID": "SHFE",
        "OrderRef": "000000000041",
        "OrderSysID": "      1001",
        "TradeID": "        77",
        "Direction": "0",
        "OffsetFlag": "0",
        "Price": 3500.0,
        "Volume": 1,
        "TradeDate": day,
        "TradeTime": "09:01:00",
        "_trading_day": day,  # the receiving session's login day
        "_connection_generation": 4,
    }
    if native_day:
        snapshot["TradingDay"] = day
    return snapshot


def test_ctp_provider_fills_of_two_trading_days_with_reused_ids_are_both_delivered():
    """Native-shaped CTP fills through the real provider projection and queue."""

    dwp = pytest.importorskip("bt_api_ctp.direct_write_provider")
    live = pytest.importorskip("bt_api_ctp.feeds.live_ctp_feed")
    feed = live.CtpRequestDataFuture(
        broker_id="broker-test",
        user_id="investor-test",
        password="synthetic-only",
        td_front="tcp://127.0.0.1:19991",
        md_front="tcp://127.0.0.1:19992",
        auto_settlement_confirm=False,
    )
    feed._connect_timeout = 0.01
    trader = _CtpTrader()
    feed._trader = trader
    provider = dwp.CtpDirectWriteProvider(feed)
    provider.ensure_listening()
    (listener,) = trader.listeners
    route = dwp.CTP_DIRECT_WRITE_EXCHANGE
    coordinator = DirectWriteCoordinator(WriteAdmission(None), lambda _n: provider)

    listener("trade", _ctp_trade_snapshot(DAY1))
    listener("trade", _ctp_trade_snapshot(DAY1))  # same-day replay
    first = coordinator.poll_events(route)
    assert [item.event.trading_day for item in first] == [DAY1]
    listener("trade", _ctp_trade_snapshot(DAY2))  # same OrderSysID/TradeID next day
    listener("trade", _ctp_trade_snapshot(DAY2, native_day=False))  # login-day fallback
    second = coordinator.poll_events(route)
    assert [item.event.trading_day for item in second] == [DAY2]
    assert second[0].event.trade_id == first[0].event.trade_id == "77"
    assert second[0].event.exchange_order_id == first[0].event.exchange_order_id == "1001"


@pytest.mark.parametrize(
    "module,name",
    [
        ("bt_api_okx.direct_write_provider", "OkxDirectWriteProvider"),
        ("bt_api_binance.direct_write_provider", "BinanceDirectWriteProvider"),
    ],
)
def test_okx_and_binance_providers_keep_undated_deduplication(module, name):
    provider_class = getattr(pytest.importorskip(module), name)
    assert getattr(provider_class, "execution_ids_trading_day_scoped", False) is False
