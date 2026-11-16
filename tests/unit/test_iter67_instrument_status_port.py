"""Iteration 67 G3: SDK status port and coordinator exposure.

UNIT/FAKE only: synthetic provider views and calendar declarations.  Nothing
here contacts a provider, reads a clock, sends an order or authorizes trading.
"""

from __future__ import annotations

from datetime import date

import pytest
from bt_api_ctp.instrument_status_observation import (
    RECEIVE_CLOCK_MONOTONIC_NS,
    InstrumentStatusLedger,
    InstrumentStatusSessionFacts,
    project_native_instrument_status,
)

from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.instrument_status_composition import (
    CALENDAR_COMPLETE,
    PHASE_CONTINUOUS,
    REASON_CALENDAR_OPEN_UNCLASSIFIED,
    REASON_OBSERVATION_CONTINUOUS,
    STATE_TRADABLE,
    STATE_UNKNOWN,
    CalendarDayAnswer,
    InstrumentStatusObservationPort,
    InstrumentStatusScope,
    compose_instrument_status,
)
from bt_api_py.write_admission import WriteAdmission

VENUE = "SHFE"
SYMBOL = "rb2701"
DAY = date(2026, 11, 16)
GENERATION = "7"


def _scope():
    return InstrumentStatusScope(VENUE, SYMBOL, DAY, GENERATION)


def _view(status="2", *, sequence=1, symbol=SYMBOL, generation=GENERATION, day=DAY):
    fields = {
        "ExchangeID": VENUE,
        "ExchangeInstID": symbol,
        "InstrumentID": symbol,
        "InstrumentStatus": status,
        "TradingSegmentSN": 1,
        "EnterTime": "09:00:00",
        "EnterReason": "1",
    }
    observation = project_native_instrument_status(
        fields,
        session=InstrumentStatusSessionFacts(
            session_generation=generation,
            trading_day=day.strftime("%Y%m%d"),
            source_sequence=sequence,
            receive_clock_domain=RECEIVE_CLOCK_MONOTONIC_NS,
            receive_time=1000 + sequence,
            source_available=True,
        ),
    )
    assert observation.accepted is True
    ledger = InstrumentStatusLedger(
        session_generation=generation, trading_day=day.strftime("%Y%m%d")
    )
    assert ledger.observe(observation).applied is True
    return ledger.view(VENUE, symbol)


def _calendar():
    return CalendarDayAnswer(
        CALENDAR_COMPLETE,
        PHASE_CONTINUOUS,
        REASON_CALENDAR_OPEN_UNCLASSIFIED,
        "cal-v1",
        VENUE,
        SYMBOL,
        DAY,
        coverage_status="DECLARED_UNVERIFIED",
    )


def test_port_passes_the_provider_view_to_the_composition():
    view = _view("2")
    port = InstrumentStatusObservationPort(lambda venue, symbol: view)
    assert port.observation(_scope()) is view
    result = compose_instrument_status(
        scope=_scope(), observation=port.observation(_scope()), calendar=_calendar()
    )
    assert (result.state, result.reason_code, result.observation_state, result.source) == (
        STATE_TRADABLE,
        REASON_OBSERVATION_CONTINUOUS,
        "CURRENT",
        "OBSERVATION",
    )
    assert result.authorizes_trading is False


def test_port_returns_an_explicit_absence_for_a_raising_or_missing_reader():
    def broken(_venue, _symbol):
        raise RuntimeError("private provider text")

    port = InstrumentStatusObservationPort(broken)
    assert port.observation(_scope()) is None

    missing = InstrumentStatusObservationPort(lambda _venue, _symbol: None)
    assert missing.observation(_scope()) is None
    result = compose_instrument_status(
        scope=_scope(), observation=missing.observation(_scope()), calendar=_calendar()
    )
    # The calendar may answer, but the absence is explicit and is never
    # reported as an observation-backed fact.
    assert result.observation_state == "ABSENT"
    assert result.source == "CALENDAR"


def test_port_requires_a_callable_and_a_scope():
    with pytest.raises(ValueError):
        InstrumentStatusObservationPort(None)
    port = InstrumentStatusObservationPort(lambda _venue, _symbol: _view())
    assert port.observation(object()) is None
    assert port.observation(None) is None


def test_port_does_not_override_a_foreign_or_stale_view():
    # A view for another identity keeps its own scope; the composition refuses
    # it instead of relabeling it as the caller's instrument.
    port = InstrumentStatusObservationPort(lambda _venue, _symbol: _view(symbol="cu2701"))
    result = compose_instrument_status(
        scope=_scope(), observation=port.observation(_scope()), calendar=_calendar()
    )
    assert (result.state, result.observation_state) == (STATE_UNKNOWN, "INVALID")


def test_stale_generation_view_never_becomes_tradable():
    port = InstrumentStatusObservationPort(lambda _venue, _symbol: _view(generation="6"))
    result = compose_instrument_status(
        scope=_scope(), observation=port.observation(_scope()), calendar=_calendar()
    )
    assert result.state == STATE_UNKNOWN
    assert result.observation_state in ("INVALID", "STALE")


class _Provider:
    def __init__(self, reader):
        self._reader = reader

    def instrument_status_view(self, venue, symbol):
        return self._reader(venue, symbol)


def test_coordinator_exposes_a_port_only_for_providers_that_can_answer():
    admission = WriteAdmission({})
    view = _view()
    coordinator = DirectWriteCoordinator(
        admission, lambda _route: _Provider(lambda _venue, _symbol: view)
    )
    port = coordinator.instrument_status_port("CTP___FUTURE")
    assert port is not None
    assert port.observation(_scope()) is view

    unsupported = DirectWriteCoordinator(admission, lambda _route: object())
    assert unsupported.instrument_status_port("CTP___FUTURE") is None


def test_coordinator_port_catches_a_raising_provider_reader():
    admission = WriteAdmission({})

    def broken(_venue, _symbol):
        raise RuntimeError("private")

    coordinator = DirectWriteCoordinator(admission, lambda _route: _Provider(broken))
    port = coordinator.instrument_status_port("CTP___FUTURE")
    assert port.observation(_scope()) is None
