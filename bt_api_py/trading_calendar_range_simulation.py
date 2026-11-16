"""Simulation-only UTC range query over one already captured calendar view.

The owner closure is deployment-declared and unverified. Inspecting the
snapshot cannot prove the absence of owners beyond the declared bounds.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bt_api_py.trading_calendar_binding import SimulationCalendarReadView

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
_ZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class CalendarRangePolicy:
    policy_id: str
    route: str
    venue: str
    kind: str
    product: str
    contract: str
    max_advance_days: int
    max_spill_days: int

    def __post_init__(self):
        if (any(type(v) is not str or _ID.fullmatch(v) is None for v in
                (self.policy_id, self.route, self.venue, self.kind, self.product, self.contract))
                or self.kind not in {"future", "option"}
                or type(self.max_advance_days) is not int or not 0 <= self.max_advance_days <= 31
                or type(self.max_spill_days) is not int or not 0 <= self.max_spill_days <= 7):
            raise ValueError("INVALID_CALENDAR_RANGE_POLICY")

    @property
    def scope_key(self):
        return self.route, self.venue, self.kind, self.product, self.contract


@dataclass(frozen=True)
class SimulationRangeResult:
    status: str
    reason: str
    first_owner_day: date | None = None
    last_owner_day: date | None = None
    windows: tuple = ()
    gaps: tuple = ()


def query_simulation_range(view, policy, start_utc, end_utc):
    """Query every possible owner from a single public read view; never reads CURRENT."""
    def unknown(reason, first=None, last=None, *, gap_day=None):
        gaps = () if gap_day is None else ((gap_day, reason),)
        return SimulationRangeResult("UNKNOWN", reason, first, last, (), gaps)

    if (type(view) is not SimulationCalendarReadView or type(policy) is not CalendarRangePolicy
            or type(start_utc) is not datetime or type(end_utc) is not datetime
            or start_utc.tzinfo is None or end_utc.tzinfo is None
            or start_utc.utcoffset() != timedelta(0) or end_utc.utcoffset() != timedelta(0)
            or not start_utc < end_utc or end_utc - start_utc > timedelta(days=366)):
        raise ValueError("INVALID_CALENDAR_RANGE")
    try:
        status = view.status()
        first_local = start_utc.astimezone(_ZONE).date()
        last_local = (end_utc - timedelta(microseconds=1)).astimezone(_ZONE).date()
        first = first_local - timedelta(days=policy.max_spill_days)
        last = last_local + timedelta(days=policy.max_advance_days)
        if (status.route != policy.route or not status.calendar_ref
                or first_local < status.bound_trading_day - timedelta(days=policy.max_advance_days)
                or last_local > status.bound_trading_day + timedelta(days=366)
                or (last - first).days + 1 > 405):
            return unknown("DAY_OUT_OF_RANGE", first, last)
        coverage = next((row for row in status.declared_coverage if row.venue == policy.venue), None)
        if coverage is None or first < coverage.start_day or last > coverage.end_day:
            return unknown("COVERAGE_UNAVAILABLE", first, last)
        # Candidate-day scanning alone misses long-holiday owners outside the
        # candidate range. The fixed view must expose a complete observation of
        # all compiled scope windows, not a fresh CURRENT read.
        try:
            from bt_api_base.trading_calendar_owner_bounds import SimulationCalendarOwnerBounds
            bounds_result = view.owner_day_bounds(mode="SIMULATION_ONLY", venue=policy.venue,
                kind=policy.kind, product=policy.product, contract=policy.contract)
        except Exception:
            return unknown("CLOSURE_UNAVAILABLE", first, last)
        bounds = bounds_result.bounds
        if (bounds_result.binding is not status or type(bounds) is not SimulationCalendarOwnerBounds
                or bounds.status != "COMPLETE" or bounds.calendar_ref != status.calendar_ref
                or (bounds.venue, bounds.kind, bounds.product, bounds.contract)
                   != (policy.venue, policy.kind, policy.product, policy.contract)
                or bounds.sources_verified is not False
                or type(bounds.max_advance_days) is not int or bounds.max_advance_days < 0
                or type(bounds.max_spill_days) is not int or bounds.max_spill_days < 0):
            return unknown("CLOSURE_UNAVAILABLE", first, last)
        if (bounds.max_advance_days > policy.max_advance_days
                or bounds.max_spill_days > policy.max_spill_days):
            return unknown("POLICY_CONTRADICTED", first, last)
        observed, selected = [], []
        current = first
        while current <= last:
            day = view.query_venue_day(mode="SIMULATION_ONLY", venue=policy.venue, trading_day=current)
            result = view.query(venue=policy.venue, kind=policy.kind, product=policy.product,
                                contract=policy.contract, trading_day=current)
            if (day.binding is not status or result.binding is not status or day.status != "COMPLETE"
                    or day.day_status not in {"open", "closed"} or result.result.status != "COMPLETE"):
                return unknown("OWNER_DATA_UNAVAILABLE", first, last, gap_day=current)
            windows = result.result.windows
            if type(windows) is not tuple or (day.day_status == "closed" and windows):
                return unknown("CONTRADICTORY_DATA", first, last, gap_day=current)
            if len(windows) > 128:
                return unknown("RESULT_TOO_LARGE", first, last)
            ids = set()
            for window in windows:
                start, end = window.start_utc, window.end_utc
                if (window.trading_day != current or type(start) is not datetime or type(end) is not datetime
                        or start.tzinfo is None or end.tzinfo is None
                        or start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0)
                        or start >= end or end - start > timedelta(days=2)
                        or type(window.window_id) is not str or _ID.fullmatch(window.window_id) is None
                        or window.window_id in ids or window.phase not in {"continuous", "auction"}):
                    return unknown("CONTRADICTORY_DATA", first, last, gap_day=current)
                ids.add(window.window_id)
                if (start.astimezone(_ZONE).date() < current - timedelta(days=policy.max_advance_days)
                        or (end - timedelta(microseconds=1)).astimezone(_ZONE).date()
                        > current + timedelta(days=policy.max_spill_days)):
                    return unknown("POLICY_CONTRADICTED", first, last, gap_day=current)
                observed.append(window)
                if start < end_utc and end > start_utc:
                    if current < status.bound_trading_day:
                        return unknown("PAST_OWNER_OVERLAP", first, last, gap_day=current)
                    selected.append(window)
                    if len(selected) > 128:
                        return unknown("RESULT_TOO_LARGE", first, last)
            current += timedelta(days=1)
        observed.sort(key=lambda w: (w.start_utc, w.end_utc))
        if any(a.end_utc > b.start_utc for a, b in zip(observed, observed[1:])):
            return unknown("CONTRADICTORY_DATA", first, last)
        selected.sort(key=lambda w: (w.start_utc, w.end_utc))
        return SimulationRangeResult("COMPLETE", "OK", first, last, tuple(selected))
    except Exception:
        return unknown("OWNER_DATA_UNAVAILABLE")
