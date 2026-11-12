"""SIMULATION_ONLY UTC projection from one pinned local calendar version.

Coverage closures, identities, source locators and night lead bounds are explicit
caller assertions, not authenticated facts. COMPLETE describes only a simulated
structural result and grants no trading authority. Missing or contradictory
evidence yields UNKNOWN, including any request not wholly inside one closure.
This module does not connect to BtApi, a provider session or Backtrader.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from bt_api_base.trading_calendar_protocol import (
    CalendarCoverage,
    CalendarCoverageStatus,
    CalendarGap,
    CalendarGapReason,
    CalendarPhase,
    CalendarQuery,
    CalendarQueryResult,
    CalendarResultReason,
    CalendarScope,
    CalendarSource,
    CalendarVersion,
    CalendarWindow,
    UtcInterval,
)
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py.trading_calendar_binding import SIMULATION_ONLY, _covered_source

_ZONE = ZoneInfo("Asia/Shanghai")
_MAX_BYTES = 16 * 1024 * 1024
_MAX_DAYS = 800
_MAX_INTERVAL = timedelta(days=366)
_MAX_LEAD = 31
_MAX_WINDOWS = 10000
_MAX_SOURCES = 1000
_MAX_BUILD_WORK = 4_000_000
_MAX_NODES = 200_000
_CLOSURE_ASSUMPTION = "ALL_POTENTIAL_OWNERS_INCLUDED_SIMULATION_ONLY"


@dataclass(frozen=True)
class SimulationUtcClosure:
    """Unverified closed-world assertion bound to one scope and exact artifacts.

    The inclusive day range claims to include *all* possible trading-day owners
    of instants in ``interval``. The lead bound also applies to potential owners
    outside the stored snapshot; checking stored rows cannot establish that fact.
    All source locators are supplied by the caller and remain unauthenticated.
    """

    scope: CalendarScope
    version: CalendarVersion
    coverage_manifest_sha256: str
    binding_sha256: str
    version_sha256: str
    interval: UtcInterval
    first_day: date
    last_day: date
    max_night_lead_days: int
    source_ids: tuple[str, ...]
    sources: tuple[CalendarSource, ...]
    assumption: str = _CLOSURE_ASSUMPTION


def _bounded_content(content):
    # Check before the binding helper recursively canonicalizes the object.
    # Counting every visit also bounds cycles and shared containers.
    pending = [content]
    nodes = 0
    size = 0
    while pending:
        value = pending.pop()
        nodes += 1
        if nodes > _MAX_NODES:
            raise ValueError("snapshot node limit")
        if type(value) is dict:
            if len(value) > _MAX_NODES - nodes - len(pending):
                raise ValueError("snapshot node limit")
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError("invalid content key")
                if len(key) > _MAX_BYTES:
                    raise ValueError("snapshot byte limit")
                size += len(key.encode("utf-8"))
                pending.append(item)
        elif type(value) is list:
            if len(value) > _MAX_NODES - nodes - len(pending):
                raise ValueError("snapshot node limit")
            pending.extend(value)
        elif type(value) is str:
            if len(value) > _MAX_BYTES:
                raise ValueError("snapshot byte limit")
            size += len(value.encode("utf-8"))
        elif type(value) is int and value.bit_length() > 1024:
            raise ValueError("snapshot integer limit")
        elif type(value) not in (int, float, bool, type(None)):
            raise ValueError("invalid content value")
        if size > _MAX_BYTES:
            raise ValueError("snapshot byte limit")
    days, rows, exceptions, sources = (
        content["trading_days"],
        content["trading_hours"],
        content["exceptions"],
        content["sources"],
    )
    if any(type(value) is not list for value in (days, rows, exceptions, sources)):
        raise ValueError("invalid content layers")
    if not days or not rows or not 0 < len(sources) <= _MAX_SOURCES:
        raise ValueError("invalid content size")
    if len(rows) > _MAX_BUILD_WORK or len(days) * len(rows) > _MAX_BUILD_WORK:
        raise ValueError("snapshot work limit")
    contracts = sum(len(row["contracts"]) for row in rows)
    if len(days) * contracts * (contracts + len(exceptions) + 1) > _MAX_BUILD_WORK:
        raise ValueError("snapshot work limit")
    # Every day can match every row. The provider builds day windows, then
    # copies/scans them per contract and again when collecting each scope.
    # A cancellation can scan the contract's windows twice per exception.
    # Include even closed or out-of-range days and unrelated exceptions: the
    # budget is a conservative bound before provider validation occurs.
    window_work = sum(
        len(days)
        * (len(row["day_windows"]) + len(row["night_windows"]))
        * (2 + len(row["contracts"]) * (5 + 2 * len(exceptions)))
        for row in rows
    )
    if window_work > _MAX_BUILD_WORK:
        raise ValueError("window work limit")


def _unknown(request, version):
    return CalendarQueryResult(
        request,
        CalendarCoverageStatus.UNKNOWN,
        version,
        (),
        (CalendarGap(request.interval, CalendarGapReason.COVERAGE_UNAVAILABLE),),
        (),
        (),
        CalendarResultReason.COVERAGE_UNAVAILABLE,
    )


class SimulationCalendarQuery:
    """A single pinned, local version; no reload, live observation or authority."""

    def __init__(self, *, mode, store, closure):
        if type(mode) is not str or mode != SIMULATION_ONLY:
            raise ValueError("explicit SIMULATION_ONLY mode required")
        self._closure = closure
        self._snapshot = None
        self._version = None
        self._coverage = ()
        self._content = None
        self._hashes = None
        try:
            stored = store.read_current()
            _bounded_content(stored.content)
            if any(
                type(value) is not bytes or len(value) > _MAX_BYTES
                for value in (stored.coverage_manifest_bytes, stored.binding_bytes)
            ):
                raise ValueError("artifact byte limit")
            coverage, content_bytes = _covered_source(stored)
            if len(content_bytes) > _MAX_BYTES:
                raise ValueError("snapshot byte limit")
            content = json.loads(content_bytes)
            _bounded_content(content)
            version = CalendarVersion(stored.version_id, stored.content_sha256)
            snapshot = CalendarSnapshot(content, calendar_ref=version.calendar_ref)
            self._coverage = coverage
            self._content = content
            self._version = version
            self._hashes = (
                stored.coverage_manifest_sha256,
                stored.binding_sha256,
                stored.version_sha256,
            )
            self._snapshot = snapshot
        except Exception:
            # A missing or invalid store can only produce UNKNOWN.
            return

    def query(self, request):
        if type(request) is not CalendarQuery:
            raise ValueError("exact CalendarQuery required")
        try:
            return self._project(request)
        except Exception:
            return _unknown(request, self._version)

    def _project(self, request):
        closure = self._closure
        if (
            self._snapshot is None
            or type(closure) is not SimulationUtcClosure
            or type(closure.scope) is not CalendarScope
            or closure.scope != request.scope
            or type(closure.version) is not CalendarVersion
            or closure.version != self._version
            or (closure.coverage_manifest_sha256, closure.binding_sha256, closure.version_sha256)
            != self._hashes
            or type(closure.interval) is not UtcInterval
            or closure.interval.end_utc - closure.interval.start_utc > _MAX_INTERVAL
            or not (
                closure.interval.start_utc
                <= request.interval.start_utc
                < request.interval.end_utc
                <= closure.interval.end_utc
            )
            or type(closure.first_day) is not date
            or type(closure.last_day) is not date
            or not 0 <= (closure.last_day - closure.first_day).days < _MAX_DAYS
            or type(closure.max_night_lead_days) is not int
            or not 0 <= closure.max_night_lead_days <= _MAX_LEAD
            or closure.assumption != _CLOSURE_ASSUMPTION
            or type(closure.source_ids) is not tuple
            or not closure.source_ids
            or len(closure.source_ids) > _MAX_SOURCES
            or any(type(item) is not str for item in closure.source_ids)
            or len(closure.source_ids) != len(set(closure.source_ids))
            or type(closure.sources) is not tuple
            or not 0 < len(closure.sources) <= _MAX_SOURCES
            or any(type(item) is not CalendarSource for item in closure.sources)
        ):
            raise ValueError("closure unavailable")
        local_start = closure.interval.start_utc.astimezone(_ZONE).date()
        local_end = (closure.interval.end_utc - timedelta(microseconds=1)).astimezone(_ZONE).date()
        if closure.first_day > local_start or closure.last_day < local_end + timedelta(
            days=closure.max_night_lead_days
        ):
            raise ValueError("incomplete owner-day closure")
        coverage = next(
            (entry for entry in self._coverage if entry.venue == request.scope.venue), None
        )
        if (
            coverage is None
            or closure.first_day < coverage.start_day
            or closure.last_day > coverage.end_day
        ):
            raise ValueError("outside declared trading-day coverage")

        source_map = {item.source_id: item for item in closure.sources}
        inventory = {item["id"] for item in self._content["sources"]}
        if (
            len(source_map) != len(closure.sources)
            or not set(source_map) <= inventory
            or not set(closure.source_ids) <= set(source_map)
        ):
            raise ValueError("invalid source declarations")
        scope = request.scope
        matching_rows = [
            row
            for row in self._content["trading_hours"]
            if (row["venue"], row["kind"], row["product"])
            == (scope.venue, scope.kind.value, scope.product)
            and scope.instrument in row["contracts"]
        ]
        if not matching_rows:
            raise ValueError("unknown exact contract")
        for row in matching_rows:
            for mapping in row["night_mappings"]:
                if mapping["start_date"] is not None:
                    lead = (
                        date.fromisoformat(mapping["trading_day"])
                        - date.fromisoformat(mapping["start_date"])
                    ).days
                    if lead > closure.max_night_lead_days:
                        raise ValueError("night ownership contradicts closure")

        all_sources = set(closure.source_ids)
        windows = []
        day = closure.first_day
        while day <= closure.last_day:
            observed = self._snapshot.query(
                venue=scope.venue,
                kind=scope.kind.value,
                product=scope.product,
                contract=scope.instrument,
                trading_day=day,
            )
            if (
                observed.status != "COMPLETE"
                or observed.calendar_ref != closure.version.calendar_ref
                or observed.sources_verified is not False
                or (
                    observed.venue,
                    observed.kind,
                    observed.product,
                    observed.contract,
                    observed.trading_day,
                )
                != (scope.venue, scope.kind.value, scope.product, scope.instrument, day)
                or not observed.source_ids
                or not set(observed.source_ids) <= set(source_map)
            ):
                raise ValueError("incomplete day")
            all_sources.update(observed.source_ids)
            for window in observed.windows:
                start = max(window.start_utc, request.interval.start_utc)
                end = min(window.end_utc, request.interval.end_utc)
                if start >= end:
                    continue
                if window.trading_day != day or window.phase not in {"auction", "continuous"}:
                    raise ValueError("invalid window")
                interval = UtcInterval(start, end)
                windows.append(
                    CalendarWindow(
                        interval,
                        CalendarPhase(window.phase),
                        day,
                        "Asia/Shanghai",
                        start.astimezone(_ZONE),
                        end.astimezone(_ZONE),
                        tuple(sorted(observed.source_ids)),
                    )
                )
                if len(windows) > _MAX_WINDOWS:
                    raise ValueError("window limit")
            if day == closure.last_day:
                break
            day += timedelta(days=1)
        windows.sort(key=lambda item: item.interval.start_utc)
        if any(a.interval.end_utc > b.interval.start_utc for a, b in zip(windows, windows[1:])):  # noqa: B905 - Python 3.9
            raise ValueError("overlapping day windows")
        return CalendarQueryResult(
            request,
            CalendarCoverageStatus.COMPLETE,
            self._version,
            (CalendarCoverage(request.interval, tuple(sorted(all_sources))),),
            (),
            tuple(windows),
            tuple(source_map[key] for key in sorted(all_sources)),
            None,
        )
