"""Bounded venue-day ranges from one SIMULATION_ONLY snapshot.

Each result is a declaration, not an authenticated venue calendar, historical
as-of knowledge, a real clock, or trade authorization. No weekday, product,
contract, session, or closure is inferred.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from bt_api_py.trading_calendar_binding import (
    SIMULATION_ONLY,
    BindingStatus,
    SimulationCalendarBinding,
    VenueDayQueryResult,
)

MAX_RANGE_DAYS = 3660


@dataclass(frozen=True)
class ExpectedVenueDaysSimulation:
    """All inclusive natural days, or UNKNOWN with no partial declarations."""

    status: str
    reason: str
    binding: BindingStatus | None
    days: tuple[VenueDayQueryResult, ...] = ()
    mode: str = SIMULATION_ONLY
    source_authentication: str = "NOT_PROVEN"
    trade_authorization: str = "NONE"


def query_expected_venue_days(binding, *, mode, venue, start_day, end_day):
    """Read an inclusive range of at most MAX_RANGE_DAYS from one pinned view.

    Every day must have one independently checked explicit venue declaration.
    UNKNOWN returns no partial data, including when any date is out of coverage.
    """
    if type(binding) is not SimulationCalendarBinding:
        return ExpectedVenueDaysSimulation("UNKNOWN", "BINDING_UNAVAILABLE", None)
    view = binding._venue_day_view()
    status = view.binding

    def unknown(reason):
        return ExpectedVenueDaysSimulation("UNKNOWN", reason, status)

    if type(mode) is not str or mode != SIMULATION_ONLY:
        return unknown("SIMULATION_MODE_REQUIRED")
    if type(start_day) is not date or type(end_day) is not date or start_day > end_day:
        return unknown("INVALID_QUERY_SCOPE")
    count = (end_day - start_day).days + 1
    if count > MAX_RANGE_DAYS:
        return unknown("RANGE_TOO_LARGE")
    days = []
    for offset in range(count):
        day = start_day + timedelta(days=offset)
        item = view.query_venue_day(mode=mode, venue=venue, trading_day=day)
        if item.status != "COMPLETE":
            return unknown(item.reason)
        days.append(item)
    return ExpectedVenueDaysSimulation(
        "COMPLETE", "EXPLICIT_SIMULATION_DECLARATIONS", status, tuple(days)
    )
