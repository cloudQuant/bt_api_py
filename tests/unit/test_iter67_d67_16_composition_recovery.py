"""Iteration 67 D67-16: composition, port, and return-maintenance recovery linkage tests."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from bt_api_ctp.direct_write_provider import CtpDirectWriteProvider
from bt_api_ctp.instrument_status_observation import (
    RECEIVE_CLOCK_MONOTONIC_NS,
    InstrumentStatusLedger,
    InstrumentStatusSessionFacts,
    project_native_instrument_status,
)
from test_instrument_status_source_fence import _payload
from test_iter67_instrument_status_ingress import FakeTrader as _BaseFakeTrader, _feed_with


class FakeTrader(_BaseFakeTrader):
    def __init__(self, *, generation=7, day="20261116"):
        super().__init__()
        self.trading_day = day
        self.session_state["trading_day"] = day
        self.generation = generation
        self.spi = object()

    @property
    def generation(self):
        return self.connection_generation

    @generation.setter
    def generation(self, value):
        self.connection_generation = value
        self.session_state["connection_generation"] = value

from bt_api_py.ctp_gateway_return_maintenance import CtpGatewayReturnMaintenance
from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.instrument_status_composition import (
    CALENDAR_COMPLETE,
    CALENDAR_UNKNOWN,
    PHASE_AUCTION,
    PHASE_CLOSED,
    PHASE_CONTINUOUS,
    PHASE_UNKNOWN,
    REASON_CALENDAR_CONTINUOUS_NO_OBSERVATION,
    REASON_CALENDAR_UNKNOWN_DAY,
    REASON_OBSERVATION_CONTINUOUS,
    REASON_OBSERVATION_LISTENER_UNHEALTHY,
    REASON_OBSERVATION_NEVER_OBSERVED,
    REASON_OBSERVATION_PRIOR_PAUSE_UNRECOVERED,
    REASON_OBSERVATION_RECOVERY_INCOMPLETE,
    REASON_OBSERVATION_RECOVERY_UNPROVEN,
    REASON_OBSERVATION_STALE_GENERATION,
    STATE_CLOSED,
    STATE_PAUSED,
    STATE_TRADABLE,
    STATE_UNKNOWN,
    CalendarDayAnswer,
    InstrumentStatusObservationPort,
    InstrumentStatusRecoveryProof,
    InstrumentStatusScope,
    compose_instrument_status,
)
from bt_api_py.write_admission import WriteAdmission

VENUE = "SHFE"
SYMBOL = "rb2701"
DAY = date(2026, 11, 16)
DAY_STR = DAY.strftime("%Y%m%d")
GEN1 = "1"
GEN2 = "2"


def _scope(generation: str = GEN2, day: date = DAY) -> InstrumentStatusScope:
    return InstrumentStatusScope(VENUE, SYMBOL, day, generation)


def _calendar(phase: str = PHASE_CONTINUOUS, *, usable: bool = True) -> CalendarDayAnswer:
    if not usable:
        return CalendarDayAnswer(
            CALENDAR_UNKNOWN,
            PHASE_UNKNOWN,
            REASON_CALENDAR_UNKNOWN_DAY,
            None,
            None,
            None,
            None,
        )
    return CalendarDayAnswer(
        CALENDAR_COMPLETE,
        phase,
        "CALENDAR_CLOSED_DAY" if phase == PHASE_CLOSED else "CALENDAR_OPEN_UNCLASSIFIED",
        "cal-v1",
        VENUE,
        SYMBOL,
        DAY,
        coverage_status="DECLARED_UNVERIFIED",
    )


def _proof(
    *,
    generation: str = GEN2,
    day: date = DAY,
    prior_pause_observed: bool = True,
    prior_pause_invalidated: bool = True,
    prior_pause_generation: str | None = GEN1,
    prior_pause_trading_day: str | None = DAY_STR,
    prior_pause_status: str | None = "no_trading",
    return_recovery_completed: bool = True,
    listener_healthy: bool = True,
    trader_id: str | None = "trader-2",
    listener_id: str | None = "listener-2",
    spi_id: str | None = "spi-2",
    recovery_fence: str | None = "ctp_gateway_return_maintenance",
) -> InstrumentStatusRecoveryProof:
    return InstrumentStatusRecoveryProof(
        venue=VENUE,
        instrument=SYMBOL,
        trading_day=day,
        session_generation=generation,
        prior_pause_observed=prior_pause_observed,
        prior_pause_invalidated=prior_pause_invalidated,
        return_recovery_completed=return_recovery_completed,
        listener_healthy=listener_healthy,
        prior_pause_generation=prior_pause_generation,
        prior_pause_trading_day=prior_pause_trading_day,
        prior_pause_status=prior_pause_status,
        trader_id=trader_id,
        listener_id=listener_id,
        spi_id=spi_id,
        recovery_fence=recovery_fence,
    )


def _ledger_view(status: str = "1", *, generation: str = GEN1, sequence: int = 1, bump_to: str | None = None):
    ledger = InstrumentStatusLedger(session_generation=generation, trading_day=DAY_STR)
    obs = project_native_instrument_status(
        {
            "ExchangeID": VENUE,
            "ExchangeInstID": SYMBOL,
            "InstrumentID": SYMBOL,
            "InstrumentStatus": status,
            "TradingSegmentSN": 1,
            "EnterTime": "09:30:00",
            "EnterReason": "1",
        },
        session=InstrumentStatusSessionFacts(
            session_generation=generation,
            trading_day=DAY_STR,
            source_sequence=sequence,
            receive_clock_domain=RECEIVE_CLOCK_MONOTONIC_NS,
            receive_time=1000 + sequence,
            source_available=True,
        ),
    )
    assert ledger.observe(obs).applied is True
    if bump_to is not None:
        ledger.begin_session_generation(bump_to, DAY_STR)
    return ledger.view(VENUE, SYMBOL)


def test_absent_after_prior_pause_cannot_clear_without_proof():
    absent_view = InstrumentStatusLedger(session_generation=GEN2, trading_day=DAY_STR).view(
        VENUE, SYMBOL
    )
    assert absent_view.reason_code == "ctp_instrument_status_never_observed"

    # Without recovery proof, ABSENT + prior_pause_observed keeps pause, never TRADABLE.
    res = compose_instrument_status(
        scope=_scope(GEN2),
        observation=absent_view,
        calendar=_calendar(PHASE_CONTINUOUS),
        recovery_proof=_proof(return_recovery_completed=False, recovery_fence=None),
    )
    assert res.state == STATE_PAUSED
    assert res.reason_code == REASON_OBSERVATION_RECOVERY_INCOMPLETE
    assert res.prior_pause_invalidated is False
    assert res.recovery_verified is False
    assert res.authorizes_trading is False

    # Even with observation=None, prior_pause_observed without recovery_proof fails closed.
    stale_after_pause = _ledger_view("1", generation=GEN1, bump_to=GEN2)
    res_no_proof = compose_instrument_status(
        scope=_scope(GEN2),
        observation=stale_after_pause,
        calendar=_calendar(PHASE_CONTINUOUS),
    )
    assert res_no_proof.state == STATE_UNKNOWN
    assert res_no_proof.reason_code == REASON_OBSERVATION_STALE_GENERATION
    assert res_no_proof.authorizes_trading is False


@pytest.mark.parametrize(
    "bad_kwargs,expected_reason",
    [
        (
            {"return_recovery_completed": False, "recovery_fence": None},
            REASON_OBSERVATION_RECOVERY_INCOMPLETE,
        ),
        ({"recovery_fence": None}, REASON_OBSERVATION_RECOVERY_INCOMPLETE),
        ({"listener_healthy": False}, REASON_OBSERVATION_LISTENER_UNHEALTHY),
        ({"spi_id": None}, REASON_OBSERVATION_LISTENER_UNHEALTHY),
        ({"listener_id": None}, REASON_OBSERVATION_LISTENER_UNHEALTHY),
        ({"trader_id": None}, REASON_OBSERVATION_LISTENER_UNHEALTHY),
        ({"generation": GEN1}, REASON_OBSERVATION_LISTENER_UNHEALTHY),
        ({"day": date(2026, 11, 17)}, REASON_OBSERVATION_LISTENER_UNHEALTHY),
        (
            {"prior_pause_invalidated": False},
            REASON_OBSERVATION_PRIOR_PAUSE_UNRECOVERED,
        ),
        (
            {"prior_pause_generation": GEN2, "prior_pause_trading_day": DAY_STR},
            REASON_OBSERVATION_PRIOR_PAUSE_UNRECOVERED,
        ),
    ],
)
def test_absent_or_stale_with_prior_pause_fails_closed_unless_all_three_conditions_hold(
    bad_kwargs, expected_reason
):
    absent_view = InstrumentStatusLedger(session_generation=GEN2, trading_day=DAY_STR).view(
        VENUE, SYMBOL
    )
    proof = _proof(**bad_kwargs)
    res = compose_instrument_status(
        scope=_scope(GEN2),
        observation=absent_view,
        calendar=_calendar(PHASE_CONTINUOUS),
        recovery_proof=proof,
    )
    assert res.state != STATE_TRADABLE
    assert res.state == STATE_PAUSED
    assert res.reason_code == expected_reason
    assert res.prior_pause_invalidated is False
    assert res.recovery_verified is False
    assert res.authorizes_trading is False


def test_verified_recovery_and_healthy_listener_clears_invalidated_prior_pause():
    stale_view = _ledger_view("1", generation=GEN1, bump_to=GEN2)
    proof = _proof(generation=GEN2, prior_pause_generation=GEN1)

    res = compose_instrument_status(
        scope=_scope(GEN2),
        observation=stale_view,
        calendar=_calendar(PHASE_CONTINUOUS),
        recovery_proof=proof,
    )
    assert res.state == STATE_TRADABLE
    assert res.reason_code == REASON_CALENDAR_CONTINUOUS_NO_OBSERVATION
    assert res.source == "CALENDAR"
    assert res.prior_pause_invalidated is True
    assert res.recovery_verified is True
    assert res.authorizes_trading is False

    # Verified recovery still respects non-continuous calendar phases (never blindly TRADABLE).
    auction = compose_instrument_status(
        scope=_scope(GEN2),
        observation=stale_view,
        calendar=_calendar(PHASE_AUCTION),
        recovery_proof=proof,
    )
    assert auction.state == STATE_PAUSED

    closed = compose_instrument_status(
        scope=_scope(GEN2),
        observation=stale_view,
        calendar=_calendar(PHASE_CLOSED),
        recovery_proof=proof,
    )
    assert closed.state == STATE_CLOSED

    unknown_cal = compose_instrument_status(
        scope=_scope(GEN2),
        observation=stale_view,
        calendar=_calendar(usable=False),
        recovery_proof=proof,
    )
    assert unknown_cal.state == STATE_UNKNOWN


def test_unhealthy_listener_blocks_even_current_observation():
    current_view = _ledger_view("2", generation=GEN2, sequence=1)
    unhealthy = _proof(
        generation=GEN2,
        prior_pause_observed=False,
        prior_pause_invalidated=False,
        prior_pause_generation=None,
        prior_pause_trading_day=None,
        prior_pause_status=None,
        listener_healthy=False,
    )
    res = compose_instrument_status(
        scope=_scope(GEN2),
        observation=current_view,
        calendar=_calendar(PHASE_CONTINUOUS),
        recovery_proof=unhealthy,
    )
    assert res.state == STATE_UNKNOWN
    assert res.reason_code == REASON_OBSERVATION_LISTENER_UNHEALTHY
    assert res.authorizes_trading is False


def test_proof_contradicting_attached_recovery_state_or_invalid_type_fails_closed():
    trader = FakeTrader(generation=1, day=DAY_STR)
    provider = CtpDirectWriteProvider(_feed_with(trader))
    try:
        provider.ensure_listening()
        trader.status_listeners[0](_payload(generation=1, status="1", sequence=1))
        trader.generation = 2
        trader.spi = object()

        view = provider.instrument_status_view(VENUE, SYMBOL)
        forged_proof = _proof(generation=GEN2, prior_pause_observed=False)
        res = compose_instrument_status(
            scope=_scope(GEN2),
            observation=view,
            calendar=_calendar(PHASE_CONTINUOUS),
            recovery_proof=forged_proof,
        )
        assert res.state == STATE_UNKNOWN
        assert res.reason_code == REASON_OBSERVATION_RECOVERY_UNPROVEN
        assert res.authorizes_trading is False

        bad_type = compose_instrument_status(
            scope=_scope(GEN2),
            observation=None,
            calendar=_calendar(PHASE_CONTINUOUS),
            recovery_proof="forged",
        )
        assert bad_type.state == STATE_UNKNOWN
        assert bad_type.reason_code == REASON_OBSERVATION_RECOVERY_UNPROVEN
    finally:
        provider.close()


def test_port_and_coordinator_end_to_end_with_late_callback_and_return_maintenance(tmp_path, monkeypatch):
    from bt_api_base.account_protocol import AccountKey
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_gateway.session_guard import LocalAccountSessionGuard
    from bt_api_py import ctp_gateway_return_maintenance as maint_mod

    monkeypatch.setattr(maint_mod, "CtpGatewayReturnIngestor", lambda *a, **kw: None)
    guard_dir = tmp_path.resolve() / "guard"
    guard_dir.mkdir(mode=0o700)
    guard = LocalAccountSessionGuard(guard_dir, "acct-1", "owner")
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal.create(tmp_path / "journal.sqlite", "acct-1", generation=generation)
    trader = FakeTrader(generation=1, day=DAY_STR)
    provider = CtpDirectWriteProvider(_feed_with(trader))
    coordinator = DirectWriteCoordinator(
        WriteAdmission(),
        lambda _route: provider,
    )
    try:
        provider.ensure_listening()
        port = coordinator.instrument_status_port("CTP")
        assert isinstance(port, InstrumentStatusObservationPort)

        # Gen 1 observes trusted pause ('1').
        trader.status_listeners[0](_payload(generation=1, status="1", sequence=1))
        scope1 = _scope(GEN1)
        paused = compose_instrument_status(
            scope=scope1,
            observation=port.observation(scope1),
            calendar=_calendar(PHASE_CONTINUOUS),
            recovery_proof=port.recovery_proof(scope1),
        )
        assert paused.state == STATE_PAUSED

        # Reconnect to Gen 2 with return-maintenance fence initially incomplete.
        trader.generation = 2
        trader.spi = object()
        scope2 = _scope(GEN2)

        # Fake return maintenance object exposing instrument_status_recovery_fence.
        class _FakeSession:
            def __init__(self, gen):
                self.gen = gen
                self.gap = False
                self.records = 0

            class _Buf:
                def __init__(self, outer):
                    self.outer = outer

                def status(self):
                    return SimpleNamespace(gap=self.outer.gap, record_count=self.outer.records)

                def offer(self, limit=64):
                    return SimpleNamespace(records=())

                def ack(self, batch):
                    pass

            @property
            def return_buffer(self):
                return self._Buf(self)

            def create_return_query_reader(self):
                session_gen = self.gen

                class _Reader:
                    def assert_session_current(self):
                        if trader.generation != session_gen:
                            raise RuntimeError("SESSION_CHANGED")

                    def cancel_read(self, _req):
                        pass

                return _Reader()

            def assert_return_record_current(self, _rec):
                pass

        class _FakeLedger:
            def reconcile_pending(self, limit=256):
                pass

            def account_fact_health(self):
                return {"unattributed": 0, "conflict": 0, "verified_external": 0}

        # Wait: real guard/journal replaces _FakeGuard!
        fake_session = _FakeSession(2)
        settings = SimpleNamespace(
            instruments=(),
            callback_batch_limit=64,
            query_timeout_seconds=1.0,
            query_interval_seconds=1.0,
        )
        maint = CtpGatewayReturnMaintenance(
            fake_session,
            provider,
            journal,
            guard,
            AccountKey("ctp", "SHFE", "simnow", "acct-1"),
            settings,
        )
        provider.bind_instrument_status_recovery_fence(maint.instrument_status_recovery_fence)

        # Poll before query pair completed -> incomplete return recovery -> fail closed.
        maint.poll()
        r_incomplete = compose_instrument_status(
            scope=scope2,
            observation=port.observation(scope2),
            calendar=_calendar(PHASE_CONTINUOUS),
            recovery_proof=port.recovery_proof(scope2),
        )
        assert r_incomplete.state != STATE_TRADABLE
        assert r_incomplete.reason_code == REASON_OBSERVATION_RECOVERY_INCOMPLETE

        # Late gen 1 '2' (continuous) callback arrives after reconnect -> refused!
        late_listener = trader.status_listeners[0]
        late_listener(_payload(generation=1, tag_generation=1, status="2", sequence=99))
        r_after_late = compose_instrument_status(
            scope=scope2,
            observation=port.observation(scope2),
            calendar=_calendar(PHASE_CONTINUOUS),
            recovery_proof=port.recovery_proof(scope2),
        )
        assert r_after_late.state != STATE_TRADABLE

        # Simulate return maintenance completing both queries cleanly and polling HEALTHY.
        maint._done = {"orders", "trades"}
        maint._pair_seen = True
        maint._ingestor = SimpleNamespace(
            assert_session_current=lambda: None,
            rebind_same_native=lambda: fake_session.create_return_query_reader(),
        )
        maint.poll()

        # Works BOTH with explicit port.recovery_proof AND when composed from port.observation alone
        # (via attached recovery_state, matching BtApiStore behavior).
        r_verified = compose_instrument_status(
            scope=scope2,
            observation=port.observation(scope2),
            calendar=_calendar(PHASE_CONTINUOUS),
            recovery_proof=port.recovery_proof(scope2),
        )
        assert r_verified.state == STATE_TRADABLE
        assert r_verified.source == "CALENDAR"
        assert r_verified.prior_pause_invalidated is True
        assert r_verified.recovery_verified is True

        r_store_path = compose_instrument_status(
            scope=scope2,
            observation=port.observation(scope2),
            calendar=_calendar(PHASE_CONTINUOUS),
        )
        assert r_store_path.state == STATE_TRADABLE
        assert r_store_path.prior_pause_invalidated is True
        assert r_store_path.recovery_verified is True

        # Return gap or reconnect pause clears recovery fence -> fail closed again immediately.
        maint.pause_for_reconnect()
        r_paused_again = compose_instrument_status(
            scope=scope2,
            observation=port.observation(scope2),
            calendar=_calendar(PHASE_CONTINUOUS),
            recovery_proof=port.recovery_proof(scope2),
        )
        assert r_paused_again.state != STATE_TRADABLE
        assert r_paused_again.reason_code == REASON_OBSERVATION_RECOVERY_INCOMPLETE

        # Explicit new-generation TRADABLE ('2') callback clears pause even when fence is paused.
        trader.status_listeners[0](_payload(generation=2, status="2", sequence=2))
        r_explicit = compose_instrument_status(
            scope=scope2,
            observation=port.observation(scope2),
            calendar=_calendar(PHASE_CONTINUOUS),
            recovery_proof=port.recovery_proof(scope2),
        )
        assert r_explicit.state == STATE_TRADABLE
        assert r_explicit.reason_code == REASON_OBSERVATION_CONTINUOUS
        assert r_explicit.source == "OBSERVATION"
    finally:
        provider.close()
        guard.release()
