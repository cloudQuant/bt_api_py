"""Read-only composition of one instrument-status observation and one calendar day.

This module is the SDK-side neutral composition under D67-08 B.  It accepts an
already-admitted provider view (``bt_api_ctp.instrument_status_observation``
``InstrumentStatusView``) or an explicit absence, plus one calendar answer for
the *exact* venue/instrument/trading day, and returns a frozen per-instrument
answer: ``TRADABLE``, ``PAUSED``, ``CLOSED`` or ``UNKNOWN``.

Rules
-----
* A valid, current-day provider status wins over the calendar, in both
  directions, and the disagreement is recorded as a conflict.
* An observation whose session generation or trading day does not match the
  caller-supplied scope is invalid and never reused: the result is ``UNKNOWN``.
* An admission refusal (out-of-order, duplicate, sequence conflict, identity
  conflict, stale generation/day, unavailable source) is ``UNKNOWN``; it never
  resurrects the previous status.
* A missing observation is reported as "no effective observation".  The
  calendar may then supply the answer, but the absence itself never means
  tradable and the result records ``observation_state == "ABSENT"``.
* A missing, unknown, out-of-scope or inconsistent calendar is ``UNKNOWN``.
  No session boundary is inferred from a wall clock anywhere in this module.
* Nothing here sends orders, changes state, reads a clock, contacts a provider,
  reads credentials, or grants authority.  Every result carries
  ``authorizes_trading is False``; the field is enforced by the result type.

The calendar answer is a neutral, separately constructed value.  Two adapters
validate the committed SDK result shapes without asserting anything they do not
carry: :func:`calendar_answer_from_current_query` (a version-bound per-day
window query; the caller must name the exact window/segment) and
:func:`calendar_answer_from_venue_day` (a venue-day declaration that cannot by
itself distinguish auction from continuous).  Both keep caller-declared scope
attribution visible via ``scope_source``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

__all__ = (
    "AUTHORIZES_TRADING",
    "CALENDAR_PHASES",
    "CALENDAR_STATES",
    "CONFLICT_CODES",
    "CalendarDayAnswer",
    "CALENDAR_COMPLETE",
    "CALENDAR_UNKNOWN",
    "InstrumentStatusComposition",
    "InstrumentStatusObservationPort",
    "InstrumentStatusScope",
    "OBSERVATION_STATES",
    "PHASE_AUCTION",
    "PHASE_CLOSED",
    "PHASE_CONTINUOUS",
    "PHASE_OPEN_UNSPECIFIED",
    "PHASE_PAUSED",
    "PHASE_UNKNOWN",
    "REASON_CODES",
    "SESSION_PHASES",
    "SOURCES",
    "STATE_CLOSED",
    "STATE_PAUSED",
    "STATE_TRADABLE",
    "STATE_UNKNOWN",
    "STATES",
    "calendar_answer_from_current_query",
    "calendar_answer_from_venue_day",
    "compose_instrument_status",
)

AUTHORIZES_TRADING = False

STATE_TRADABLE = "TRADABLE"
STATE_PAUSED = "PAUSED"
STATE_CLOSED = "CLOSED"
STATE_UNKNOWN = "UNKNOWN"
STATES = frozenset({STATE_TRADABLE, STATE_PAUSED, STATE_CLOSED, STATE_UNKNOWN})

PHASE_CONTINUOUS = "CONTINUOUS"
PHASE_AUCTION = "AUCTION"
PHASE_PAUSED = "PAUSED"
PHASE_CLOSED = "CLOSED"
PHASE_OPEN_UNSPECIFIED = "OPEN_UNSPECIFIED"
PHASE_UNKNOWN = "UNKNOWN"
SESSION_PHASES = frozenset(
    {
        PHASE_CONTINUOUS,
        PHASE_AUCTION,
        PHASE_PAUSED,
        PHASE_CLOSED,
        PHASE_UNKNOWN,
    }
)
CALENDAR_PHASES = frozenset(
    {
        PHASE_CONTINUOUS,
        PHASE_AUCTION,
        PHASE_OPEN_UNSPECIFIED,
        PHASE_CLOSED,
        PHASE_UNKNOWN,
    }
)

CALENDAR_COMPLETE = "COMPLETE"
CALENDAR_UNKNOWN = "UNKNOWN"
CALENDAR_STATES = frozenset({CALENDAR_COMPLETE, CALENDAR_UNKNOWN, "MISSING", "INVALID"})

OBSERVATION_STATES = frozenset({"CURRENT", "STALE", "UNKNOWN", "ABSENT", "INVALID", "REFUSED"})

SOURCES = frozenset({"OBSERVATION", "CALENDAR", "NONE"})

# Finite composition reason codes.  Every produced result uses exactly one.
REASON_INVALID_SCOPE = "INVALID_SCOPE"
REASON_PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
REASON_COMPOSITION_ERROR = "COMPOSITION_ERROR"

REASON_CALENDAR_MISSING = "CALENDAR_MISSING"
REASON_CALENDAR_INVALID = "CALENDAR_INVALID"
REASON_CALENDAR_UNAVAILABLE = "CALENDAR_UNAVAILABLE"
REASON_CALENDAR_SCOPE_MISMATCH = "CALENDAR_SCOPE_MISMATCH"
REASON_CALENDAR_REFERENCE_MISSING = "CALENDAR_REFERENCE_MISSING"
REASON_CALENDAR_PHASE_UNSPECIFIED = "CALENDAR_PHASE_UNSPECIFIED"
REASON_CALENDAR_PHASE_UNSUPPORTED = "CALENDAR_PHASE_UNSUPPORTED"
REASON_CALENDAR_CLOSED_DAY = "CALENDAR_CLOSED_DAY"
REASON_CALENDAR_OPEN_UNCLASSIFIED = "CALENDAR_OPEN_UNCLASSIFIED"
REASON_CALENDAR_UNKNOWN_DAY = "CALENDAR_UNKNOWN_DAY"

REASON_OBSERVATION_INVALID = "OBSERVATION_INVALID"
REASON_OBSERVATION_UNPROVEN = "OBSERVATION_UNPROVEN"
REASON_OBSERVATION_SCOPE_MISMATCH = "OBSERVATION_SCOPE_MISMATCH"
REASON_OBSERVATION_GENERATION_MISMATCH = "OBSERVATION_GENERATION_MISMATCH"
REASON_OBSERVATION_TRADING_DAY_MISMATCH = "OBSERVATION_TRADING_DAY_MISMATCH"
REASON_OBSERVATION_STALE_GENERATION = "OBSERVATION_STALE_GENERATION"
REASON_OBSERVATION_STALE_TRADING_DAY = "OBSERVATION_STALE_TRADING_DAY"
REASON_OBSERVATION_SOURCE_UNAVAILABLE = "OBSERVATION_SOURCE_UNAVAILABLE"
REASON_OBSERVATION_NEVER_OBSERVED = "OBSERVATION_NEVER_OBSERVED"
REASON_OBSERVATION_CATEGORY_UNKNOWN = "OBSERVATION_CATEGORY_UNKNOWN"
REASON_OBSERVATION_UNKNOWN_REASON = "OBSERVATION_UNKNOWN_REASON"
REASON_OBSERVATION_OUT_OF_ORDER = "OBSERVATION_OUT_OF_ORDER"
REASON_OBSERVATION_DUPLICATE = "OBSERVATION_DUPLICATE"
REASON_OBSERVATION_SEQUENCE_CONFLICT = "OBSERVATION_SEQUENCE_CONFLICT"
REASON_OBSERVATION_IDENTITY_CONFLICT = "OBSERVATION_IDENTITY_CONFLICT"
REASON_OBSERVATION_ADMISSION_REFUSED = "OBSERVATION_ADMISSION_REFUSED"

REASON_OBSERVATION_CONTINUOUS = "OBSERVATION_CONTINUOUS"
REASON_OBSERVATION_AUCTION = "OBSERVATION_AUCTION"
REASON_OBSERVATION_PAUSED = "OBSERVATION_PAUSED"
REASON_OBSERVATION_CLOSED = "OBSERVATION_CLOSED"

REASON_CALENDAR_CONTINUOUS_NO_OBSERVATION = "CALENDAR_CONTINUOUS_NO_OBSERVATION"
REASON_CALENDAR_AUCTION_NO_OBSERVATION = "CALENDAR_AUCTION_NO_OBSERVATION"
REASON_CALENDAR_CLOSED_NO_OBSERVATION = "CALENDAR_CLOSED_NO_OBSERVATION"

CONFLICT_CALENDAR_OBSERVATION = "CALENDAR_OBSERVATION_CONFLICT"
CONFLICT_CODES = frozenset({CONFLICT_CALENDAR_OBSERVATION})

REASON_CODES = frozenset(
    {
        REASON_INVALID_SCOPE,
        REASON_PROVIDER_UNAVAILABLE,
        REASON_COMPOSITION_ERROR,
        REASON_CALENDAR_MISSING,
        REASON_CALENDAR_INVALID,
        REASON_CALENDAR_UNAVAILABLE,
        REASON_CALENDAR_SCOPE_MISMATCH,
        REASON_CALENDAR_REFERENCE_MISSING,
        REASON_CALENDAR_PHASE_UNSPECIFIED,
        REASON_CALENDAR_PHASE_UNSUPPORTED,
        REASON_CALENDAR_CLOSED_DAY,
        REASON_CALENDAR_OPEN_UNCLASSIFIED,
        REASON_CALENDAR_UNKNOWN_DAY,
        REASON_OBSERVATION_INVALID,
        REASON_OBSERVATION_UNPROVEN,
        REASON_OBSERVATION_SCOPE_MISMATCH,
        REASON_OBSERVATION_GENERATION_MISMATCH,
        REASON_OBSERVATION_TRADING_DAY_MISMATCH,
        REASON_OBSERVATION_STALE_GENERATION,
        REASON_OBSERVATION_STALE_TRADING_DAY,
        REASON_OBSERVATION_SOURCE_UNAVAILABLE,
        REASON_OBSERVATION_NEVER_OBSERVED,
        REASON_OBSERVATION_CATEGORY_UNKNOWN,
        REASON_OBSERVATION_UNKNOWN_REASON,
        REASON_OBSERVATION_OUT_OF_ORDER,
        REASON_OBSERVATION_DUPLICATE,
        REASON_OBSERVATION_SEQUENCE_CONFLICT,
        REASON_OBSERVATION_IDENTITY_CONFLICT,
        REASON_OBSERVATION_ADMISSION_REFUSED,
        REASON_OBSERVATION_CONTINUOUS,
        REASON_OBSERVATION_AUCTION,
        REASON_OBSERVATION_PAUSED,
        REASON_OBSERVATION_CLOSED,
        REASON_CALENDAR_CONTINUOUS_NO_OBSERVATION,
        REASON_CALENDAR_AUCTION_NO_OBSERVATION,
        REASON_CALENDAR_CLOSED_NO_OBSERVATION,
    }
)

_CALENDAR_COMPLETE_REASONS = frozenset(
    {REASON_CALENDAR_CLOSED_DAY, REASON_CALENDAR_OPEN_UNCLASSIFIED}
)
_CALENDAR_UNKNOWN_REASONS = frozenset(
    {
        REASON_CALENDAR_MISSING,
        REASON_CALENDAR_INVALID,
        REASON_CALENDAR_UNAVAILABLE,
        REASON_CALENDAR_SCOPE_MISMATCH,
        REASON_CALENDAR_REFERENCE_MISSING,
        REASON_CALENDAR_PHASE_UNSPECIFIED,
        REASON_CALENDAR_PHASE_UNSUPPORTED,
        REASON_CALENDAR_UNKNOWN_DAY,
    }
)
_SCOPE_SOURCES = frozenset({"CALLER_DECLARED", "VENUE_DAY_BOUND"})

# Provider categories are finite and lowercase; unknown is explicit.
_PROVIDER_CONTINUOUS = "continuous"
_PROVIDER_AUCTION = frozenset({"auction_ordering", "auction_balance", "auction_match"})
_PROVIDER_PAUSED = frozenset({"before_trading", "no_trading", "transaction_processing"})
_PROVIDER_CLOSED = "closed"
_PROVIDER_UNKNOWN = "unknown"
_PROVIDER_VIEW_STATES = frozenset({"current", "stale", "unknown"})

_PROVIDER_REASON_MAP = {
    "ctp_instrument_status_generation_mismatch": REASON_OBSERVATION_GENERATION_MISMATCH,
    "ctp_instrument_status_trading_day_mismatch": REASON_OBSERVATION_TRADING_DAY_MISMATCH,
    "ctp_instrument_status_stale_generation": REASON_OBSERVATION_STALE_GENERATION,
    "ctp_instrument_status_stale_trading_day": REASON_OBSERVATION_STALE_TRADING_DAY,
    "ctp_instrument_status_source_unavailable": REASON_OBSERVATION_SOURCE_UNAVAILABLE,
    "ctp_instrument_status_never_observed": REASON_OBSERVATION_NEVER_OBSERVED,
    "ctp_instrument_status_status_unknown": REASON_OBSERVATION_CATEGORY_UNKNOWN,
    "ctp_instrument_status_identity_conflict": REASON_OBSERVATION_IDENTITY_CONFLICT,
    "ctp_instrument_status_sequence_out_of_order": REASON_OBSERVATION_OUT_OF_ORDER,
    "ctp_instrument_status_duplicate": REASON_OBSERVATION_DUPLICATE,
    "ctp_instrument_status_sequence_conflict": REASON_OBSERVATION_SEQUENCE_CONFLICT,
    "ctp_instrument_status_observation_invalid": REASON_OBSERVATION_INVALID,
}

_ACTION_REASON_MAP = {
    "out_of_order": REASON_OBSERVATION_OUT_OF_ORDER,
    "duplicate": REASON_OBSERVATION_DUPLICATE,
    "sequence_conflict": REASON_OBSERVATION_SEQUENCE_CONFLICT,
    "identity_conflict": REASON_OBSERVATION_IDENTITY_CONFLICT,
    "stale_generation": REASON_OBSERVATION_STALE_GENERATION,
    "stale_trading_day": REASON_OBSERVATION_STALE_TRADING_DAY,
    "source_unavailable": REASON_OBSERVATION_SOURCE_UNAVAILABLE,
    "refused": REASON_OBSERVATION_ADMISSION_REFUSED,
}

_IDENTIFIER = re.compile(r"[\x21-\x7e]{1,128}\Z", re.ASCII)
_TRADING_DAY = re.compile(r"[0-9]{8}\Z", re.ASCII)


def _identifier(value: object) -> bool:
    return type(value) is str and _IDENTIFIER.fullmatch(value) is not None


def _trading_day_text(value: object) -> bool:
    if type(value) is not str or _TRADING_DAY.fullmatch(value) is None:
        return False
    try:
        date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class InstrumentStatusScope:
    """Trusted caller scope for one composition: exact identity and session.

    ``session_generation`` and ``trading_day`` are the *current* authenticated
    session facts.  They are compared with the provider view; this module does
    not derive either from a clock or from the observation itself.
    """

    venue: str
    instrument: str
    trading_day: date
    session_generation: str

    def __post_init__(self) -> None:
        if not _identifier(self.venue) or not _identifier(self.instrument):
            raise ValueError("scope identity must be bounded ASCII identifiers")
        if type(self.trading_day) is not date:
            raise ValueError("scope trading_day must be a date")
        if not _identifier(self.session_generation):
            raise ValueError("scope session_generation must be a bounded identifier")


class InstrumentStatusObservationPort:
    """Read-only adapter from a provider view reader to the Store port contract.

    ``view_reader(venue, instrument)`` returns the provider's current
    ``InstrumentStatusView`` for that identity, or ``None`` for an explicit
    absence.  It must be a pure read: no connection, no order, no clock-derived
    ordering.  A missing, raising or foreign-type answer becomes an explicit
    absence (``None``) and is never replaced by a guessed status; the compose
    step then reports the absence and can never make the answer ``TRADABLE``
    without a current, scope-matching observation.

    The adapter is intentionally provider-neutral: it holds no route, file
    path, credential or account value and adds no authority of its own.
    """

    def __init__(self, view_reader: object) -> None:
        if not callable(view_reader):
            raise ValueError("instrument status view reader must be callable")
        self._view_reader = view_reader

    def observation(self, scope: object) -> object:
        """Return the provider view for the exact scope, or ``None``."""

        if type(scope) is not InstrumentStatusScope:
            return None
        try:
            return self._view_reader(scope.venue, scope.instrument)
        except Exception:
            # The provider cannot answer: an explicit absence, never a guess.
            # Log one finite code only; never the provider text, exception,
            # identity or account value.
            from bt_api_base.logging_factory import get_logger

            get_logger("api").warning("ctp_instrument_status_port_read_unavailable")
            return None


@dataclass(frozen=True)
class CalendarDayAnswer:
    """One calendar answer for the exact scope; a declaration, not authority.

    ``status`` is ``COMPLETE`` when this answer is usable, otherwise
    ``UNKNOWN``.  ``phase`` distinguishes continuous from auction; a venue-day
    declaration that cannot distinguish them uses ``OPEN_UNSPECIFIED`` and can
    never by itself make the composed state ``TRADABLE``.  ``scope_source``
    records how the answer was bound to the instrument: ``CALLER_DECLARED`` for
    the current-query adapter (the result carries no contract identity) or
    ``VENUE_DAY_BOUND`` for the venue-day adapter (venue and day validated).
    """

    status: str
    phase: str
    reason: str
    calendar_ref: str | None
    venue: str | None
    instrument: str | None
    trading_day: date | None
    scope_source: str = "CALLER_DECLARED"
    coverage_status: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if self.status not in (CALENDAR_COMPLETE, CALENDAR_UNKNOWN):
            raise ValueError("calendar status must be COMPLETE or UNKNOWN")
        if self.phase not in CALENDAR_PHASES:
            raise ValueError("calendar phase must be finite")
        if type(self.reason) is not str or self.reason not in REASON_CODES:
            raise ValueError("calendar reason must be a finite composition reason")
        for name in ("venue", "instrument"):
            value = getattr(self, name)
            if value is not None and not _identifier(value):
                raise ValueError(f"calendar {name} must be a bounded identifier or None")
        if self.trading_day is not None and type(self.trading_day) is not date:
            raise ValueError("calendar trading_day must be a date or None")
        if self.scope_source not in _SCOPE_SOURCES:
            raise ValueError("calendar scope_source must be finite")
        if not _identifier(self.coverage_status):
            raise ValueError("calendar coverage_status must be a bounded identifier")
        if self.status == CALENDAR_COMPLETE:
            if self.phase == PHASE_UNKNOWN:
                raise ValueError("a complete calendar answer needs a known phase")
            if self.reason not in _CALENDAR_COMPLETE_REASONS:
                raise ValueError("complete calendar reason is not a success reason")
            if not _identifier(self.calendar_ref):
                raise ValueError("a complete calendar answer needs a calendar_ref")
        else:
            if self.phase != PHASE_UNKNOWN:
                raise ValueError("an unknown calendar answer has unknown phase")
            if self.reason not in _CALENDAR_UNKNOWN_REASONS:
                raise ValueError("unknown calendar reason is not an unavailability reason")


@dataclass(frozen=True)
class InstrumentStatusComposition:
    """Frozen read-only per-instrument answer; it never authorizes trading.

    ``reason_code`` is exactly one finite composition reason.  ``conflict`` is
    recorded when a usable calendar and a valid observation disagree, and
    ``calendar_stale`` marks the specific case of a ``CLOSED`` calendar against
    a valid continuous/auction observation.  Diagnostics keep the provider
    generation, sequence and category separate from the composed state.
    """

    state: str
    reason_code: str
    session_phase: str
    venue: str | None
    instrument: str | None
    trading_day: str | None
    observation_state: str
    observation_generation: str | None
    observation_trading_day: str | None
    observation_sequence: int | None
    observation_status: str | None
    observation_reason: str | None
    observation_provider_reason: str | None
    calendar_state: str
    calendar_phase: str
    calendar_ref: str | None
    calendar_reason: str | None
    conflict: str | None
    calendar_stale: bool
    source: str
    authorizes_trading: bool = AUTHORIZES_TRADING

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError("state must be finite")
        if self.reason_code not in REASON_CODES:
            raise ValueError("reason_code must be a finite composition reason")
        if self.session_phase not in SESSION_PHASES:
            raise ValueError("session_phase must be finite")
        if self.observation_state not in OBSERVATION_STATES:
            raise ValueError("observation_state must be finite")
        if self.calendar_state not in CALENDAR_STATES:
            raise ValueError("calendar_state must be finite")
        if self.calendar_phase not in CALENDAR_PHASES:
            raise ValueError("calendar_phase must be finite")
        if self.source not in SOURCES:
            raise ValueError("source must be finite")
        if self.conflict is not None and self.conflict not in CONFLICT_CODES:
            raise ValueError("conflict must be a finite conflict code")
        if self.authorizes_trading is not False:
            raise ValueError("a composition never authorizes trading")


@dataclass(frozen=True)
class _ObservationFacts:
    state: str
    reason: str | None
    provider_state: str | None
    generation: str | None
    sequence: int | None
    category: str | None
    provider_reason: str | None
    day: str | None = None


@dataclass(frozen=True)
class _CalendarFacts:
    state: str
    phase: str
    calendar_ref: str | None
    reason: str | None
    usable: bool


def _provider_types() -> tuple[type, type, type] | None:
    """Load the provider view/result types lazily; never a transport module."""

    try:
        from bt_api_ctp.instrument_status_observation import (
            InstrumentStatusApplyResult,
            InstrumentStatusObservation,
            InstrumentStatusView,
        )
    except ImportError:
        return None
    return InstrumentStatusApplyResult, InstrumentStatusObservation, InstrumentStatusView


def _unknown_calendar(reason: str) -> CalendarDayAnswer:
    return CalendarDayAnswer(
        status=CALENDAR_UNKNOWN,
        phase=PHASE_UNKNOWN,
        reason=reason,
        calendar_ref=None,
        venue=None,
        instrument=None,
        trading_day=None,
    )


def calendar_answer_from_current_query(
    query: object,
    *,
    scope: InstrumentStatusScope,
    window_id: str | None = None,
) -> CalendarDayAnswer:
    """Bind a committed ``TradingCalendarCurrentQuery`` to one exact segment.

    The query result carries no contract identity, so ``scope_source`` stays
    ``CALLER_DECLARED``.  A complete day with no windows is a declared closed
    day.  Otherwise the caller must name the exact ``window_id``; its trading
    day must equal the scope day and its phase maps to continuous or auction.
    No wall clock is consulted and no window is selected by time.
    """

    if type(scope) is not InstrumentStatusScope:
        return _unknown_calendar(REASON_INVALID_SCOPE)

    def unknown(reason: str) -> CalendarDayAnswer:
        return CalendarDayAnswer(
            CALENDAR_UNKNOWN,
            PHASE_UNKNOWN,
            reason,
            None,
            scope.venue,
            scope.instrument,
            scope.trading_day,
            coverage_status="UNKNOWN",
        )

    try:
        from bt_api_py.trading_calendar_runtime_status import TradingCalendarCurrentQuery
    except ImportError:
        return unknown(REASON_PROVIDER_UNAVAILABLE)
    if type(query) is not TradingCalendarCurrentQuery:
        return unknown(REASON_CALENDAR_INVALID)
    if query.status != "COMPLETE":
        return unknown(REASON_CALENDAR_UNAVAILABLE)
    if not _identifier(query.calendar_ref):
        return unknown(REASON_CALENDAR_REFERENCE_MISSING)
    windows = query.windows
    if type(windows) is not tuple:
        return unknown(REASON_CALENDAR_INVALID)
    if not windows:
        return CalendarDayAnswer(
            CALENDAR_COMPLETE,
            PHASE_CLOSED,
            REASON_CALENDAR_CLOSED_DAY,
            query.calendar_ref,
            scope.venue,
            scope.instrument,
            scope.trading_day,
            coverage_status=query.coverage_status,
        )
    if type(window_id) is not str or not _identifier(window_id):
        return unknown(REASON_CALENDAR_PHASE_UNSPECIFIED)
    selected = [window for window in windows if getattr(window, "window_id", None) == window_id]
    if len(selected) != 1:
        return unknown(REASON_CALENDAR_PHASE_UNSPECIFIED)
    window = selected[0]
    if window.trading_day != scope.trading_day:
        return unknown(REASON_CALENDAR_SCOPE_MISMATCH)
    phase = {PHASE_CONTINUOUS.lower(): PHASE_CONTINUOUS, PHASE_AUCTION.lower(): PHASE_AUCTION}.get(
        window.phase
    )
    if phase is None:
        return unknown(REASON_CALENDAR_PHASE_UNSUPPORTED)
    return CalendarDayAnswer(
        CALENDAR_COMPLETE,
        phase,
        REASON_CALENDAR_OPEN_UNCLASSIFIED,
        query.calendar_ref,
        scope.venue,
        scope.instrument,
        scope.trading_day,
        coverage_status=query.coverage_status,
    )


def calendar_answer_from_venue_day(
    result: object, *, scope: InstrumentStatusScope
) -> CalendarDayAnswer:
    """Bind a committed ``VenueDayQueryResult``; open stays unclassified.

    The venue-day declaration validates venue and trading day but carries no
    instrument identity, contract kind, or continuous/auction distinction.
    A declared ``open`` therefore yields ``OPEN_UNSPECIFIED`` and never makes a
    composition ``TRADABLE`` on its own.
    """

    if type(scope) is not InstrumentStatusScope:
        return _unknown_calendar(REASON_INVALID_SCOPE)

    def unknown(reason: str) -> CalendarDayAnswer:
        return CalendarDayAnswer(
            CALENDAR_UNKNOWN,
            PHASE_UNKNOWN,
            reason,
            None,
            scope.venue,
            scope.instrument,
            scope.trading_day,
            coverage_status="UNKNOWN",
        )

    try:
        from bt_api_py.trading_calendar_binding import BindingStatus, VenueDayQueryResult
    except ImportError:
        return unknown(REASON_PROVIDER_UNAVAILABLE)
    if type(result) is not VenueDayQueryResult:
        return unknown(REASON_CALENDAR_INVALID)
    if result.status != "COMPLETE" or result.mode != "SIMULATION_ONLY":
        return unknown(REASON_CALENDAR_UNAVAILABLE)
    if type(result.binding) is not BindingStatus or not _identifier(result.binding.calendar_ref):
        return unknown(REASON_CALENDAR_REFERENCE_MISSING)
    if result.venue != scope.venue or result.trading_day != scope.trading_day:
        return unknown(REASON_CALENDAR_SCOPE_MISMATCH)
    if result.day_status == "closed":
        phase, reason = PHASE_CLOSED, REASON_CALENDAR_CLOSED_DAY
    elif result.day_status == "open":
        phase, reason = PHASE_OPEN_UNSPECIFIED, REASON_CALENDAR_OPEN_UNCLASSIFIED
    else:
        return unknown(REASON_CALENDAR_UNKNOWN_DAY)
    return CalendarDayAnswer(
        CALENDAR_COMPLETE,
        phase,
        reason,
        result.binding.calendar_ref,
        scope.venue,
        scope.instrument,
        scope.trading_day,
        scope_source="VENUE_DAY_BOUND",
        coverage_status=result.binding.coverage_status,
    )


def _calendar_facts(calendar: object, scope: InstrumentStatusScope) -> _CalendarFacts:
    if calendar is None:
        return _CalendarFacts("MISSING", PHASE_UNKNOWN, None, REASON_CALENDAR_MISSING, False)
    if type(calendar) is not CalendarDayAnswer:
        return _CalendarFacts("INVALID", PHASE_UNKNOWN, None, REASON_CALENDAR_INVALID, False)
    if calendar.status == CALENDAR_UNKNOWN:
        reason = (
            calendar.reason
            if calendar.reason in _CALENDAR_UNKNOWN_REASONS
            else REASON_CALENDAR_UNAVAILABLE
        )
        return _CalendarFacts(CALENDAR_UNKNOWN, PHASE_UNKNOWN, None, reason, False)
    if calendar.status != CALENDAR_COMPLETE:
        return _CalendarFacts("INVALID", PHASE_UNKNOWN, None, REASON_CALENDAR_INVALID, False)
    if calendar.phase == PHASE_UNKNOWN or calendar.reason not in _CALENDAR_COMPLETE_REASONS:
        return _CalendarFacts("INVALID", PHASE_UNKNOWN, None, REASON_CALENDAR_INVALID, False)
    if not _identifier(calendar.calendar_ref):
        return _CalendarFacts(
            "INVALID", PHASE_UNKNOWN, None, REASON_CALENDAR_REFERENCE_MISSING, False
        )
    if (
        calendar.venue != scope.venue
        or calendar.instrument != scope.instrument
        or calendar.trading_day != scope.trading_day
    ):
        return _CalendarFacts("INVALID", PHASE_UNKNOWN, None, REASON_CALENDAR_SCOPE_MISMATCH, False)
    return _CalendarFacts(CALENDAR_COMPLETE, calendar.phase, calendar.calendar_ref, None, True)


def _observation_sequence(observation: object) -> int | None:
    nested = getattr(observation, "observation", None)
    sequence = getattr(nested, "source_sequence", None)
    return sequence if type(sequence) is int and sequence >= 1 else None


def _observation_day(observation: object, fallback: str | None) -> str | None:
    # The ledger view reports its current scope day; the nested observation
    # keeps the day that actually produced the status.  Prefer the latter.
    nested = getattr(observation, "observation", None)
    day = getattr(nested, "trading_day", None)
    if type(day) is str and _trading_day_text(day):
        return day
    return fallback


def _observation_facts(
    observation: object, apply_result: object, scope: InstrumentStatusScope
) -> _ObservationFacts:
    types = _provider_types()
    if types is None:
        return _ObservationFacts(
            "INVALID",
            REASON_PROVIDER_UNAVAILABLE,
            None,
            None,
            None,
            None,
            None,
        )
    apply_type, observation_type, view_type = types

    if apply_result is not None:
        if type(apply_result) is not apply_type:
            return _ObservationFacts(
                "INVALID", REASON_OBSERVATION_UNPROVEN, None, None, None, None, None
            )
        applied = apply_result.applied
        action = apply_result.action
        if type(applied) is not bool or type(action) is not str:
            return _ObservationFacts(
                "INVALID", REASON_OBSERVATION_UNPROVEN, None, None, None, None, None
            )
        if applied is False:
            reason = _ACTION_REASON_MAP.get(action)
            if reason is None:
                reason = _PROVIDER_REASON_MAP.get(apply_result.reason_code)
            return _ObservationFacts(
                "REFUSED",
                reason or REASON_OBSERVATION_ADMISSION_REFUSED,
                None,
                None,
                None,
                None,
                apply_result.reason_code if type(apply_result.reason_code) is str else None,
            )
        if action != "applied":
            return _ObservationFacts(
                "INVALID", REASON_OBSERVATION_UNPROVEN, None, None, None, None, None
            )

    if observation is None:
        if apply_result is not None:
            return _ObservationFacts(
                "INVALID", REASON_OBSERVATION_UNPROVEN, None, None, None, None, None
            )
        return _ObservationFacts("ABSENT", None, None, None, None, None, None)
    if type(observation) is not view_type:
        return _ObservationFacts(
            "INVALID", REASON_OBSERVATION_INVALID, None, None, None, None, None
        )

    venue = observation.venue
    symbol = observation.native_symbol
    provider_state = observation.state
    generation = observation.session_generation
    day_text = observation.trading_day
    if venue != scope.venue or symbol != scope.instrument:
        return _ObservationFacts(
            "INVALID",
            REASON_OBSERVATION_SCOPE_MISMATCH,
            provider_state if type(provider_state) is str else None,
            generation if type(generation) is str else None,
            _observation_sequence(observation),
            None,
            observation.reason_code if type(observation.reason_code) is str else None,
            day=day_text if _trading_day_text(day_text) else None,
        )
    if type(provider_state) is not str or provider_state not in _PROVIDER_VIEW_STATES:
        return _ObservationFacts(
            "INVALID", REASON_OBSERVATION_INVALID, None, None, None, None, None
        )
    if not _identifier(generation) or not _trading_day_text(day_text):
        return _ObservationFacts(
            "INVALID", REASON_OBSERVATION_INVALID, provider_state, None, None, None, None
        )
    observation_day = _observation_day(observation, day_text)
    sequence = _observation_sequence(observation)
    if generation != scope.session_generation:
        reason = (
            REASON_OBSERVATION_STALE_GENERATION
            if provider_state == "stale"
            else REASON_OBSERVATION_GENERATION_MISMATCH
        )
        return _ObservationFacts(
            "INVALID" if provider_state != "stale" else "STALE",
            reason,
            provider_state,
            generation,
            _observation_sequence(observation),
            None,
            observation.reason_code if type(observation.reason_code) is str else None,
            day=observation_day,
        )
    if day_text != scope.trading_day.strftime("%Y%m%d"):
        reason = (
            REASON_OBSERVATION_STALE_TRADING_DAY
            if provider_state == "stale"
            else REASON_OBSERVATION_TRADING_DAY_MISMATCH
        )
        return _ObservationFacts(
            "INVALID" if provider_state != "stale" else "STALE",
            reason,
            provider_state,
            generation,
            _observation_sequence(observation),
            None,
            observation.reason_code if type(observation.reason_code) is str else None,
            day=observation_day,
        )

    category = observation.effective_status_category
    usable = observation.usable
    provider_reason = observation.reason_code if type(observation.reason_code) is str else None

    if provider_state == "current":
        if type(usable) is not bool or type(category) is not str:
            return _ObservationFacts(
                "INVALID",
                REASON_OBSERVATION_INVALID,
                provider_state,
                generation,
                sequence,
                category if type(category) is str else None,
                provider_reason,
            )
        if category == _PROVIDER_UNKNOWN:
            if usable is not False or provider_reason != "ctp_instrument_status_status_unknown":
                return _ObservationFacts(
                    "INVALID",
                    REASON_OBSERVATION_INVALID,
                    provider_state,
                    generation,
                    sequence,
                    category,
                    provider_reason,
                )
            return _ObservationFacts(
                "UNKNOWN",
                REASON_OBSERVATION_CATEGORY_UNKNOWN,
                provider_state,
                generation,
                sequence,
                category,
                provider_reason,
                day=observation_day,
            )
        if usable is not True or provider_reason is not None:
            return _ObservationFacts(
                "INVALID",
                REASON_OBSERVATION_INVALID,
                provider_state,
                generation,
                sequence,
                category,
                provider_reason,
            )
        nested = observation.observation
        if (
            type(nested) is not observation_type
            or nested.accepted is not True
            or nested.venue != venue
            or nested.native_symbol != symbol
            or nested.session_generation != generation
            or nested.trading_day != day_text
            or nested.status_category != category
            or sequence is None
            or observation.observed_status_category != category
        ):
            return _ObservationFacts(
                "INVALID",
                REASON_OBSERVATION_UNPROVEN,
                provider_state,
                generation,
                sequence,
                category if type(category) is str else None,
                provider_reason,
            )
        return _ObservationFacts(
            "CURRENT",
            None,
            provider_state,
            generation,
            sequence,
            category,
            provider_reason,
            day=observation_day,
        )

    if type(usable) is not bool or usable is not False:
        return _ObservationFacts(
            "INVALID",
            REASON_OBSERVATION_INVALID,
            provider_state,
            generation,
            sequence,
            category if type(category) is str else None,
            provider_reason,
        )
    if type(category) is not str or category != _PROVIDER_UNKNOWN:
        return _ObservationFacts(
            "INVALID",
            REASON_OBSERVATION_INVALID,
            provider_state,
            generation,
            sequence,
            None,
            provider_reason,
        )
    if provider_state == "unknown" and provider_reason == "ctp_instrument_status_never_observed":
        return _ObservationFacts(
            "ABSENT",
            REASON_OBSERVATION_NEVER_OBSERVED,
            provider_state,
            generation,
            None,
            None,
            provider_reason,
            day=observation_day,
        )
    mapped = _PROVIDER_REASON_MAP.get(provider_reason)
    if provider_state == "stale":
        reason = mapped or REASON_OBSERVATION_UNKNOWN_REASON
        if reason not in {
            REASON_OBSERVATION_STALE_GENERATION,
            REASON_OBSERVATION_STALE_TRADING_DAY,
            REASON_OBSERVATION_SOURCE_UNAVAILABLE,
        }:
            reason = REASON_OBSERVATION_UNKNOWN_REASON
        return _ObservationFacts(
            "STALE",
            reason,
            provider_state,
            generation,
            sequence,
            (
                observation.observed_status_category
                if type(observation.observed_status_category) is str
                else None
            ),
            provider_reason,
            day=observation_day,
        )
    return _ObservationFacts(
        "UNKNOWN",
        mapped or REASON_OBSERVATION_UNKNOWN_REASON,
        provider_state,
        generation,
        sequence,
        None,
        provider_reason,
        day=observation_day,
    )


def _result(
    *,
    state: str,
    reason: str,
    phase: str,
    scope: InstrumentStatusScope | None,
    observation: _ObservationFacts,
    calendar: _CalendarFacts,
    conflict: str | None = None,
    calendar_stale: bool = False,
    source: str = "NONE",
) -> InstrumentStatusComposition:
    return InstrumentStatusComposition(
        state=state,
        reason_code=reason,
        session_phase=phase,
        venue=scope.venue if scope is not None else None,
        instrument=scope.instrument if scope is not None else None,
        trading_day=scope.trading_day.isoformat() if scope is not None else None,
        observation_state=observation.state,
        observation_generation=observation.generation,
        observation_trading_day=observation.day,
        observation_sequence=observation.sequence,
        observation_status=observation.category,
        observation_reason=observation.reason,
        observation_provider_reason=observation.provider_reason,
        calendar_state=calendar.state,
        calendar_phase=calendar.phase,
        calendar_ref=calendar.calendar_ref,
        calendar_reason=calendar.reason,
        conflict=conflict,
        calendar_stale=calendar_stale,
        source=source,
    )


def _blank(reason: str) -> InstrumentStatusComposition:
    return InstrumentStatusComposition(
        state=STATE_UNKNOWN,
        reason_code=reason,
        session_phase=PHASE_UNKNOWN,
        venue=None,
        instrument=None,
        trading_day=None,
        observation_state="INVALID",
        observation_generation=None,
        observation_trading_day=None,
        observation_sequence=None,
        observation_status=None,
        observation_reason=None,
        observation_provider_reason=None,
        calendar_state="MISSING",
        calendar_phase=PHASE_UNKNOWN,
        calendar_ref=None,
        calendar_reason=None,
        conflict=None,
        calendar_stale=False,
        source="NONE",
    )


def _conflict(composed_phase: str, calendar: _CalendarFacts) -> tuple[str | None, bool]:
    if not calendar.usable or calendar.phase in (PHASE_UNKNOWN, PHASE_OPEN_UNSPECIFIED):
        return None, False
    if calendar.phase == composed_phase:
        return None, False
    calendar_stale = calendar.phase == PHASE_CLOSED and composed_phase in (
        PHASE_CONTINUOUS,
        PHASE_AUCTION,
    )
    return CONFLICT_CALENDAR_OBSERVATION, calendar_stale


def _compose(
    scope: object,
    observation: object,
    calendar: object,
    apply_result: object,
) -> InstrumentStatusComposition:
    if type(scope) is not InstrumentStatusScope:
        return _blank(REASON_INVALID_SCOPE)
    calendar_facts = _calendar_facts(calendar, scope)
    observation_facts = _observation_facts(observation, apply_result, scope)

    if observation_facts.state not in ("CURRENT", "ABSENT"):
        return _result(
            state=STATE_UNKNOWN,
            reason=observation_facts.reason or REASON_OBSERVATION_UNPROVEN,
            phase=PHASE_UNKNOWN,
            scope=scope,
            observation=observation_facts,
            calendar=calendar_facts,
        )

    if observation_facts.state == "CURRENT":
        if not calendar_facts.usable:
            return _result(
                state=STATE_UNKNOWN,
                reason=calendar_facts.reason or REASON_CALENDAR_UNAVAILABLE,
                phase=PHASE_UNKNOWN,
                scope=scope,
                observation=observation_facts,
                calendar=calendar_facts,
            )
        category = observation_facts.category
        if category == _PROVIDER_CONTINUOUS:
            state, phase, reason = STATE_TRADABLE, PHASE_CONTINUOUS, REASON_OBSERVATION_CONTINUOUS
        elif category in _PROVIDER_AUCTION:
            state, phase, reason = STATE_PAUSED, PHASE_AUCTION, REASON_OBSERVATION_AUCTION
        elif category in _PROVIDER_PAUSED:
            state, phase, reason = STATE_PAUSED, PHASE_PAUSED, REASON_OBSERVATION_PAUSED
        elif category == _PROVIDER_CLOSED:
            state, phase, reason = STATE_CLOSED, PHASE_CLOSED, REASON_OBSERVATION_CLOSED
        else:
            return _result(
                state=STATE_UNKNOWN,
                reason=REASON_OBSERVATION_CATEGORY_UNKNOWN,
                phase=PHASE_UNKNOWN,
                scope=scope,
                observation=observation_facts,
                calendar=calendar_facts,
            )
        conflict, calendar_stale = _conflict(phase, calendar_facts)
        return _result(
            state=state,
            reason=reason,
            phase=phase,
            scope=scope,
            observation=observation_facts,
            calendar=calendar_facts,
            conflict=conflict,
            calendar_stale=calendar_stale,
            source="OBSERVATION",
        )

    # Explicit absence or a never-observed view: no effective observation.
    if not calendar_facts.usable:
        return _result(
            state=STATE_UNKNOWN,
            reason=calendar_facts.reason or REASON_CALENDAR_UNAVAILABLE,
            phase=PHASE_UNKNOWN,
            scope=scope,
            observation=observation_facts,
            calendar=calendar_facts,
        )
    if calendar_facts.phase == PHASE_CONTINUOUS:
        state, reason = STATE_TRADABLE, REASON_CALENDAR_CONTINUOUS_NO_OBSERVATION
    elif calendar_facts.phase == PHASE_AUCTION:
        state, reason = STATE_PAUSED, REASON_CALENDAR_AUCTION_NO_OBSERVATION
    elif calendar_facts.phase == PHASE_CLOSED:
        state, reason = STATE_CLOSED, REASON_CALENDAR_CLOSED_NO_OBSERVATION
    elif calendar_facts.phase == PHASE_OPEN_UNSPECIFIED:
        return _result(
            state=STATE_UNKNOWN,
            reason=REASON_CALENDAR_PHASE_UNSPECIFIED,
            phase=PHASE_UNKNOWN,
            scope=scope,
            observation=observation_facts,
            calendar=calendar_facts,
        )
    else:
        return _result(
            state=STATE_UNKNOWN,
            reason=REASON_CALENDAR_UNAVAILABLE,
            phase=PHASE_UNKNOWN,
            scope=scope,
            observation=observation_facts,
            calendar=calendar_facts,
        )
    return _result(
        state=state,
        reason=reason,
        phase=calendar_facts.phase,
        scope=scope,
        observation=observation_facts,
        calendar=calendar_facts,
        source="CALENDAR",
    )


def compose_instrument_status(
    *,
    scope: InstrumentStatusScope,
    observation: object = None,
    calendar: object = None,
    apply_result: object = None,
) -> InstrumentStatusComposition:
    """Compose one read-only per-instrument status; never an order gate.

    ``observation`` is the provider ledger's ``InstrumentStatusView`` for the
    exact identity.  ``None`` means an explicit absence.  ``apply_result`` is
    the optional provider admission result that produced the view; a refusal
    (out-of-order, duplicate, sequence/identity conflict, stale scope,
    unavailable source) yields ``UNKNOWN`` and never reuses the prior status.
    ``calendar`` is a :class:`CalendarDayAnswer`; ``None`` is a missing answer.
    Every failure is returned as a finite, value-free result; this function
    does not raise for malformed inputs.
    """

    try:
        return _compose(scope, observation, calendar, apply_result)
    except Exception:
        # Never leak input values, paths or provider exception text.
        return _blank(REASON_COMPOSITION_ERROR)
