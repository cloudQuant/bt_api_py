"""UNIT/FAKE coverage for iteration 67.06 SDK-side status composition.

Every provider callback is a synthetic native field dictionary; the calendar
answers are synthetic declarations or committed SDK result shapes built in
memory.  No account, network, provider transport, order path or wall clock is
used.  Nothing here authorizes trading.
"""

import ast
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from bt_api_ctp.instrument_status_observation import (
    RECEIVE_CLOCK_MONOTONIC_NS,
    InstrumentStatusLedger,
    InstrumentStatusSessionFacts,
    project_native_instrument_status,
)

from bt_api_py import instrument_status_composition as module
from bt_api_py.instrument_status_composition import (
    CALENDAR_COMPLETE,
    CALENDAR_UNKNOWN,
    PHASE_AUCTION,
    PHASE_CLOSED,
    PHASE_CONTINUOUS,
    PHASE_PAUSED,
    PHASE_UNKNOWN,
    REASON_CALENDAR_CLOSED_DAY,
    REASON_CALENDAR_PHASE_UNSPECIFIED,
    REASON_CALENDAR_UNAVAILABLE,
    REASON_OBSERVATION_AUCTION,
    REASON_OBSERVATION_CATEGORY_UNKNOWN,
    REASON_OBSERVATION_CLOSED,
    REASON_OBSERVATION_CONTINUOUS,
    REASON_OBSERVATION_DUPLICATE,
    REASON_OBSERVATION_OUT_OF_ORDER,
    REASON_OBSERVATION_PAUSED,
    REASON_OBSERVATION_SEQUENCE_CONFLICT,
    REASON_OBSERVATION_STALE_GENERATION,
    REASON_OBSERVATION_STALE_TRADING_DAY,
    STATE_CLOSED,
    STATE_PAUSED,
    STATE_TRADABLE,
    STATE_UNKNOWN,
    CalendarDayAnswer,
    InstrumentStatusScope,
    calendar_answer_from_current_query,
    calendar_answer_from_venue_day,
    compose_instrument_status,
)
from bt_api_py.trading_calendar_binding import BindingStatus, VenueDayQueryResult
from bt_api_py.trading_calendar_runtime_status import (
    TradingCalendarCurrentQuery,
    TradingCalendarQueryWindow,
)

UTC = timezone.utc  # noqa: UP017 - Python 3.9
VENUE = "SHFE"
SYMBOL = "rb2701"
DAY1 = date(2026, 11, 16)
DAY2 = date(2026, 11, 17)
GEN1 = "gen-1"
GEN2 = "gen-2"


def _fields(status="2", *, exchange_symbol=SYMBOL):
    return {
        "ExchangeID": VENUE,
        "ExchangeInstID": exchange_symbol,
        "InstrumentID": SYMBOL,
        "InstrumentStatus": status,
        "TradingSegmentSN": 1,
        "EnterTime": "09:00:00",
        "EnterReason": "1",
    }


def _session(sequence=1, *, generation=GEN1, day=DAY1):
    return InstrumentStatusSessionFacts(
        session_generation=generation,
        trading_day=day.strftime("%Y%m%d"),
        source_sequence=sequence,
        receive_clock_domain=RECEIVE_CLOCK_MONOTONIC_NS,
        receive_time=1_000 + sequence,
        source_available=True,
    )


def _scope(*, day=DAY1, generation=GEN1, instrument=SYMBOL):
    return InstrumentStatusScope(VENUE, instrument, day, generation)


def _ledger(*, day=DAY1, generation=GEN1):
    return InstrumentStatusLedger(session_generation=generation, trading_day=day.strftime("%Y%m%d"))


def _project(status="2", *, sequence=1, generation=GEN1, day=DAY1, exchange_symbol=SYMBOL):
    return project_native_instrument_status(
        _fields(status, exchange_symbol=exchange_symbol),
        session=_session(sequence, generation=generation, day=day),
    )


def _current(ledger, *, status="2", sequence=1, generation=GEN1, day=DAY1):
    observation = _project(status, sequence=sequence, generation=generation, day=day)
    result = ledger.observe(observation)
    assert result.applied is True
    return result


def _calendar(
    phase=PHASE_CONTINUOUS,
    *,
    day=DAY1,
    venue=VENUE,
    instrument=SYMBOL,
    ref="cal-v1",
):
    reason = (
        REASON_CALENDAR_CLOSED_DAY
        if phase == PHASE_CLOSED
        else module.REASON_CALENDAR_OPEN_UNCLASSIFIED
    )
    return CalendarDayAnswer(
        CALENDAR_COMPLETE,
        phase,
        reason,
        ref,
        venue,
        instrument,
        day,
        coverage_status="DECLARED_UNVERIFIED",
    )


def _unknown_calendar(*, day=DAY1, venue=VENUE, instrument=SYMBOL):
    return CalendarDayAnswer(
        CALENDAR_UNKNOWN,
        PHASE_UNKNOWN,
        REASON_CALENDAR_UNAVAILABLE,
        None,
        venue,
        instrument,
        day,
    )


def _composed(status="2", *, calendar_phase=PHASE_CONTINUOUS, day=DAY1, generation=GEN1):
    ledger = _ledger(day=day, generation=generation)
    _current(ledger, status=status, day=day, generation=generation)
    return compose_instrument_status(
        scope=_scope(day=day, generation=generation),
        observation=ledger.view(VENUE, SYMBOL),
        calendar=_calendar(calendar_phase, day=day),
    )


def test_continuous_observation_matches_calendar_as_tradable_without_authority():
    result = _composed("2")
    assert (result.state, result.session_phase) == (STATE_TRADABLE, PHASE_CONTINUOUS)
    assert result.reason_code == REASON_OBSERVATION_CONTINUOUS
    assert (result.conflict, result.calendar_stale) == (None, False)
    assert (result.source, result.observation_state) == ("OBSERVATION", "CURRENT")
    assert result.observation_generation == GEN1
    assert result.observation_trading_day == DAY1.strftime("%Y%m%d")
    assert result.observation_sequence == 1
    assert result.observation_status == "continuous"
    assert result.calendar_ref == "cal-v1"
    assert result.calendar_phase == PHASE_CONTINUOUS
    assert result.trading_day == DAY1.isoformat()
    assert result.authorizes_trading is False


def test_valid_current_pause_wins_over_a_continuous_calendar():
    result = _composed("1")
    assert (result.state, result.session_phase) == (STATE_PAUSED, PHASE_PAUSED)
    assert result.reason_code == REASON_OBSERVATION_PAUSED
    assert result.conflict == "CALENDAR_OBSERVATION_CONFLICT"
    assert result.calendar_stale is False
    assert result.calendar_ref == "cal-v1"
    assert result.authorizes_trading is False


def test_valid_continuous_observation_overrides_closed_calendar_and_flags_it():
    result = _composed("2", calendar_phase=PHASE_CLOSED)
    assert (result.state, result.session_phase) == (STATE_TRADABLE, PHASE_CONTINUOUS)
    assert result.reason_code == REASON_OBSERVATION_CONTINUOUS
    assert result.conflict == "CALENDAR_OBSERVATION_CONFLICT"
    assert result.calendar_stale is True
    assert result.calendar_phase == PHASE_CLOSED
    assert result.authorizes_trading is False


def test_closed_observation_wins_over_an_open_calendar():
    result = _composed("6")
    assert (result.state, result.session_phase) == (STATE_CLOSED, PHASE_CLOSED)
    assert result.reason_code == REASON_OBSERVATION_CLOSED
    assert result.conflict == "CALENDAR_OBSERVATION_CONFLICT"
    assert result.authorizes_trading is False


@pytest.mark.parametrize("native_status", ["3", "4", "5"])
def test_auction_categories_stay_auction_and_never_continuous(native_status):
    balanced = _composed(native_status, calendar_phase=PHASE_AUCTION)
    assert (balanced.state, balanced.session_phase) == (STATE_PAUSED, PHASE_AUCTION)
    assert balanced.reason_code == REASON_OBSERVATION_AUCTION
    assert balanced.conflict is None
    assert balanced.observation_status in {
        "auction_ordering",
        "auction_balance",
        "auction_match",
    }
    conflicting = _composed(native_status)
    assert conflicting.state == STATE_PAUSED
    assert conflicting.session_phase == PHASE_AUCTION
    assert conflicting.conflict == "CALENDAR_OBSERVATION_CONFLICT"
    assert conflicting.state != STATE_TRADABLE


@pytest.mark.parametrize("native_status", ["0", "7"])
def test_before_trading_and_transaction_processing_are_paused(native_status):
    result = _composed(native_status)
    assert (result.state, result.session_phase) == (STATE_PAUSED, PHASE_PAUSED)
    assert result.reason_code == REASON_OBSERVATION_PAUSED
    assert result.conflict == "CALENDAR_OBSERVATION_CONFLICT"


def test_unmapped_provider_status_is_explicitly_unknown():
    ledger = _ledger()
    _current(ledger, status="9")
    result = compose_instrument_status(
        scope=_scope(),
        observation=ledger.view(VENUE, SYMBOL),
        calendar=_calendar(),
    )
    assert result.state == STATE_UNKNOWN
    assert result.reason_code == REASON_OBSERVATION_CATEGORY_UNKNOWN
    assert result.observation_status == "unknown"
    assert result.authorizes_trading is False


def test_generation_bump_invalidates_the_prior_observation_never_reused():
    ledger = _ledger()
    _current(ledger, status="1", sequence=1)
    ledger.begin_session_generation(GEN2, DAY1.strftime("%Y%m%d"))
    view = ledger.view(VENUE, SYMBOL)
    assert view.state == "stale"

    new_scope = compose_instrument_status(
        scope=_scope(generation=GEN2), observation=view, calendar=_calendar()
    )
    assert new_scope.state == STATE_UNKNOWN
    assert new_scope.reason_code == REASON_OBSERVATION_STALE_GENERATION
    assert new_scope.observation_state == "STALE"
    assert new_scope.observation_trading_day == DAY1.strftime("%Y%m%d")
    assert new_scope.calendar_ref == "cal-v1"

    old_scope = compose_instrument_status(
        scope=_scope(generation=GEN1), observation=view, calendar=_calendar()
    )
    assert old_scope.state == STATE_UNKNOWN
    assert old_scope.reason_code == REASON_OBSERVATION_STALE_GENERATION


def test_day_rollover_invalidates_the_prior_observation():
    ledger = _ledger()
    _current(ledger, status="2", sequence=1)
    ledger.begin_session_generation(GEN1, DAY2.strftime("%Y%m%d"))
    result = compose_instrument_status(
        scope=_scope(day=DAY2),
        observation=ledger.view(VENUE, SYMBOL),
        calendar=_calendar(day=DAY2),
    )
    assert result.state == STATE_UNKNOWN
    assert result.reason_code == REASON_OBSERVATION_STALE_TRADING_DAY
    assert result.observation_state == "STALE"
    assert result.observation_trading_day == DAY1.strftime("%Y%m%d")
    assert result.trading_day == DAY2.isoformat()


def test_observation_for_another_instrument_scope_is_unknown():
    ledger = _ledger()
    _current(ledger, status="2")
    result = compose_instrument_status(
        scope=_scope(instrument="rb2705"),
        observation=ledger.view(VENUE, SYMBOL),
        calendar=_calendar(instrument="rb2705"),
    )
    assert result.state == STATE_UNKNOWN
    assert result.reason_code == "OBSERVATION_SCOPE_MISMATCH"
    assert result.calendar_ref == "cal-v1"


def test_applied_view_that_disagrees_with_scope_generation_is_unknown():
    ledger = _ledger()
    admission = _current(ledger, status="2", sequence=1)
    result = compose_instrument_status(
        scope=_scope(generation=GEN2),
        observation=ledger.view(VENUE, SYMBOL),
        calendar=_calendar(),
        apply_result=admission,
    )
    assert result.state == STATE_UNKNOWN
    assert result.reason_code == "OBSERVATION_GENERATION_MISMATCH"


def test_out_of_order_sequence_is_refused_and_not_reused():
    ledger = _ledger()
    _current(ledger, status="2", sequence=2)
    late = _project("1", sequence=1)
    admission = ledger.observe(late)
    assert (admission.applied, admission.action) == (False, "out_of_order")
    result = compose_instrument_status(
        scope=_scope(),
        observation=ledger.view(VENUE, SYMBOL),
        calendar=_calendar(),
        apply_result=admission,
    )
    assert result.state == STATE_UNKNOWN
    assert result.reason_code == REASON_OBSERVATION_OUT_OF_ORDER
    assert result.observation_state == "REFUSED"
    # The refused event cannot resurrect the pause, and no old status is reused.
    assert result.session_phase == PHASE_UNKNOWN


def test_duplicate_sequence_is_refused():
    ledger = _ledger()
    _current(ledger, status="2", sequence=1)
    admission = ledger.observe(_project("2", sequence=1))
    assert (admission.applied, admission.action) == (False, "duplicate")
    result = compose_instrument_status(
        scope=_scope(),
        observation=ledger.view(VENUE, SYMBOL),
        calendar=_calendar(),
        apply_result=admission,
    )
    assert result.state == STATE_UNKNOWN
    assert result.reason_code == REASON_OBSERVATION_DUPLICATE
    assert result.authorizes_trading is False


def test_same_sequence_conflict_is_refused():
    ledger = _ledger()
    _current(ledger, status="2", sequence=1)
    admission = ledger.observe(_project("1", sequence=1))
    assert (admission.applied, admission.action) == (False, "sequence_conflict")
    result = compose_instrument_status(
        scope=_scope(),
        observation=ledger.view(VENUE, SYMBOL),
        calendar=_calendar(),
        apply_result=admission,
    )
    assert result.state == STATE_UNKNOWN
    assert result.reason_code == REASON_OBSERVATION_SEQUENCE_CONFLICT
    assert result.session_phase == PHASE_UNKNOWN


def test_identity_conflict_is_refused():
    ledger = _ledger()
    _current(ledger, status="2", sequence=1)
    admission = ledger.observe(_project("1", sequence=2, exchange_symbol="rb2705"))
    assert (admission.applied, admission.action) == (False, "identity_conflict")
    result = compose_instrument_status(
        scope=_scope(),
        observation=ledger.view(VENUE, SYMBOL),
        calendar=_calendar(),
        apply_result=admission,
    )
    assert result.state == STATE_UNKNOWN
    assert result.reason_code == "OBSERVATION_IDENTITY_CONFLICT"


def test_source_unavailable_observation_never_falls_back_to_the_calendar():
    ledger = _ledger()
    _current(ledger, status="1", sequence=1)
    ledger.mark_source_unavailable()
    view = ledger.view(VENUE, SYMBOL)
    assert view.state == "stale"
    result = compose_instrument_status(
        scope=_scope(), observation=view, calendar=_calendar(PHASE_CONTINUOUS)
    )
    assert result.state == STATE_UNKNOWN
    assert result.reason_code == "OBSERVATION_SOURCE_UNAVAILABLE"
    assert result.observation_state == "STALE"
    assert result.session_phase == PHASE_UNKNOWN


def test_never_observed_view_is_treated_as_no_effective_observation():
    ledger = _ledger()
    view = ledger.view(VENUE, SYMBOL)
    assert view.reason_code == "ctp_instrument_status_never_observed"
    result = compose_instrument_status(
        scope=_scope(), observation=view, calendar=_calendar(PHASE_CONTINUOUS)
    )
    assert result.state == STATE_TRADABLE
    assert result.reason_code == "CALENDAR_CONTINUOUS_NO_OBSERVATION"
    assert result.observation_state == "ABSENT"
    assert result.observation_reason == "OBSERVATION_NEVER_OBSERVED"
    assert result.observation_status is None


def test_missing_observation_reports_absence_and_uses_the_calendar_answer():
    continuous = compose_instrument_status(
        scope=_scope(), observation=None, calendar=_calendar(PHASE_CONTINUOUS)
    )
    assert (continuous.state, continuous.session_phase) == (STATE_TRADABLE, PHASE_CONTINUOUS)
    assert continuous.reason_code == "CALENDAR_CONTINUOUS_NO_OBSERVATION"
    assert continuous.observation_state == "ABSENT"
    assert continuous.source == "CALENDAR"
    assert continuous.observation_generation is None
    assert continuous.observation_trading_day is None
    assert continuous.calendar_ref == "cal-v1"

    closed = compose_instrument_status(
        scope=_scope(), observation=None, calendar=_calendar(PHASE_CLOSED)
    )
    assert (closed.state, closed.session_phase) == (STATE_CLOSED, PHASE_CLOSED)
    assert closed.reason_code == "CALENDAR_CLOSED_NO_OBSERVATION"

    auction = compose_instrument_status(
        scope=_scope(), observation=None, calendar=_calendar(PHASE_AUCTION)
    )
    assert (auction.state, auction.session_phase) == (STATE_PAUSED, PHASE_AUCTION)
    assert auction.reason_code == "CALENDAR_AUCTION_NO_OBSERVATION"


def test_missing_observation_with_missing_or_unknown_calendar_is_unknown():
    missing = compose_instrument_status(scope=_scope(), observation=None, calendar=None)
    assert missing.state == STATE_UNKNOWN
    assert missing.reason_code == "CALENDAR_MISSING"
    assert missing.calendar_state == "MISSING"

    unknown = compose_instrument_status(
        scope=_scope(), observation=None, calendar=_unknown_calendar()
    )
    assert unknown.state == STATE_UNKNOWN
    assert unknown.reason_code == REASON_CALENDAR_UNAVAILABLE
    assert unknown.calendar_state == CALENDAR_UNKNOWN


def test_calendar_unavailability_forces_unknown_even_with_a_current_observation():
    ledger = _ledger()
    _current(ledger, status="2")
    view = ledger.view(VENUE, SYMBOL)
    missing = compose_instrument_status(scope=_scope(), observation=view, calendar=None)
    assert missing.state == STATE_UNKNOWN
    assert missing.reason_code == "CALENDAR_MISSING"
    assert missing.observation_status == "continuous"

    unavailable = compose_instrument_status(
        scope=_scope(), observation=view, calendar=_unknown_calendar()
    )
    assert unavailable.state == STATE_UNKNOWN
    assert unavailable.reason_code == REASON_CALENDAR_UNAVAILABLE


def test_calendar_scope_mismatch_is_unknown():
    result = compose_instrument_status(
        scope=_scope(),
        observation=None,
        calendar=_calendar(day=DAY2),
    )
    assert result.state == STATE_UNKNOWN
    assert result.reason_code == "CALENDAR_SCOPE_MISMATCH"

    wrong_instrument = compose_instrument_status(
        scope=_scope(),
        observation=None,
        calendar=_calendar(instrument="rb2705"),
    )
    assert wrong_instrument.state == STATE_UNKNOWN
    assert wrong_instrument.reason_code == "CALENDAR_SCOPE_MISMATCH"


def test_current_query_adapter_binds_one_explicit_window_without_a_clock():
    windows = (
        TradingCalendarQueryWindow(
            "day",
            "continuous",
            DAY1.replace(day=16),
            datetime(2026, 11, 16, 1, tzinfo=UTC),
            datetime(2026, 11, 16, 3, tzinfo=UTC),
            datetime(2026, 11, 16, 9, tzinfo=UTC),
            datetime(2026, 11, 16, 11, tzinfo=UTC),
        ),
        TradingCalendarQueryWindow(
            "night",
            "auction",
            DAY1,
            datetime(2026, 11, 15, 13, tzinfo=UTC),
            datetime(2026, 11, 15, 14, tzinfo=UTC),
            datetime(2026, 11, 15, 21, tzinfo=UTC),
            datetime(2026, 11, 15, 22, tzinfo=UTC),
        ),
    )
    query = TradingCalendarCurrentQuery(
        "COMPLETE",
        "EXPLICIT_DATA",
        windows,
        calendar_ref="cal-v1",
        coverage_status="DECLARED_UNVERIFIED",
    )
    selected = calendar_answer_from_current_query(query, scope=_scope(), window_id="night")
    assert (selected.status, selected.phase, selected.calendar_ref) == (
        CALENDAR_COMPLETE,
        PHASE_AUCTION,
        "cal-v1",
    )
    assert selected.scope_source == "CALLER_DECLARED"

    unselected = calendar_answer_from_current_query(query, scope=_scope())
    assert (unselected.status, unselected.phase) == (CALENDAR_UNKNOWN, PHASE_UNKNOWN)
    assert unselected.reason == REASON_CALENDAR_PHASE_UNSPECIFIED

    other_day = calendar_answer_from_current_query(query, scope=_scope(day=DAY2), window_id="night")
    assert other_day.reason == "CALENDAR_SCOPE_MISMATCH"

    empty = TradingCalendarCurrentQuery("COMPLETE", "EXPLICIT_DATA", (), calendar_ref="cal-v1")
    closed = calendar_answer_from_current_query(empty, scope=_scope())
    assert (closed.status, closed.phase) == (CALENDAR_COMPLETE, PHASE_CLOSED)

    unknown = TradingCalendarCurrentQuery("UNKNOWN", "CURRENT_UNAVAILABLE")
    unavailable = calendar_answer_from_current_query(unknown, scope=_scope())
    assert (unavailable.status, unavailable.reason) == (
        CALENDAR_UNKNOWN,
        REASON_CALENDAR_UNAVAILABLE,
    )


def test_venue_day_open_stays_unclassified_and_is_never_tradable_alone():
    binding = BindingStatus(
        mode="SIMULATION_ONLY",
        route="route-a",
        source_generation="src-1",
        bound_trading_day=DAY1,
        last_sequence=1,
        calendar_ref="cal-v1",
        publication_id="pub-1",
        content_sha256=None,
        coverage_status="DECLARED_UNVERIFIED",
        coverage_manifest_sha256=None,
        binding_sha256=None,
        version_sha256=None,
        declared_coverage=(),
        binding_reason="PROCESS_START_OR_RESTART",
        load_error=None,
    )

    def venue_day(status, venue=VENUE, day=DAY1):
        return VenueDayQueryResult(
            "COMPLETE", "EXPLICIT_SIMULATION_DECLARATION", binding, venue, day, status, ("sim",)
        )

    open_answer = calendar_answer_from_venue_day(venue_day("open"), scope=_scope())
    assert (open_answer.status, open_answer.phase) == (
        CALENDAR_COMPLETE,
        module.PHASE_OPEN_UNSPECIFIED,
    )
    assert open_answer.scope_source == "VENUE_DAY_BOUND"
    absent = compose_instrument_status(scope=_scope(), observation=None, calendar=open_answer)
    assert absent.state == STATE_UNKNOWN
    assert absent.reason_code == REASON_CALENDAR_PHASE_UNSPECIFIED

    closed_answer = calendar_answer_from_venue_day(venue_day("closed"), scope=_scope())
    closed = compose_instrument_status(scope=_scope(), observation=None, calendar=closed_answer)
    assert (closed.state, closed.session_phase) == (STATE_CLOSED, PHASE_CLOSED)

    mismatch = calendar_answer_from_venue_day(venue_day("open", venue="DCE"), scope=_scope())
    assert mismatch.reason == "CALENDAR_SCOPE_MISMATCH"


def test_malformed_inputs_return_finite_results_instead_of_raising():
    assert compose_instrument_status(scope=object()).reason_code == "INVALID_SCOPE"
    ledger = _ledger()
    _current(ledger, status="2")
    invalid_observation = compose_instrument_status(
        scope=_scope(), observation=object(), calendar=_calendar()
    )
    assert invalid_observation.state == STATE_UNKNOWN
    assert invalid_observation.reason_code == "OBSERVATION_INVALID"
    invalid_calendar = compose_instrument_status(
        scope=_scope(), observation=ledger.view(VENUE, SYMBOL), calendar=object()
    )
    assert invalid_calendar.state == STATE_UNKNOWN
    assert invalid_calendar.reason_code == "CALENDAR_INVALID"


def test_composition_is_frozen_hashable_and_deterministic():
    first = _composed("2")
    second = _composed("2")
    assert first == second
    assert hash(first) == hash(second)
    with pytest.raises(FrozenInstanceError):
        first.state = STATE_TRADABLE
    with pytest.raises(ValueError):
        replace(first, authorizes_trading=True)


def test_module_has_no_order_transport_network_or_clock_imports():
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    assert names
    forbidden = (
        "trader_api",
        "td_api",
        "order",
        "socket",
        "requests",
        "urllib",
        "http",
        "websocket",
        "transport",
        "ctp_gateway",
    )
    for name in names:
        assert not any(token in name for token in forbidden), name
    for token in ("datetime.now", "utcnow", "monotonic", "time.time"):
        assert token not in source, token
