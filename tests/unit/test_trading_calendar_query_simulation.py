"""Offline, synthetic query projection; no venue or account authority."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from bt_api_base.trading_calendar_protocol import (
    CalendarContractKind,
    CalendarCoverageStatus,
    CalendarQuery,
    CalendarScope,
    CalendarSource,
    CalendarVersion,
    UtcInterval,
)
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py import trading_calendar_query_simulation as simulation
from bt_api_py.trading_calendar_binding import SIMULATION_ONLY
from bt_api_py.trading_calendar_composite import compose_simulated
from bt_api_py.trading_calendar_query_simulation import (
    SimulationCalendarQuery,
    SimulationUtcClosure,
)
from bt_api_py.trading_calendar_update_simulation import VenueContent
from tests.calendar_test_store import FixtureCalendarStore

UTC = timezone.utc  # noqa: UP017 - Python 3.9
FRIDAY = date(2026, 10, 9)
MONDAY = date(2026, 10, 12)
TUESDAY = date(2026, 10, 13)
THURSDAY = date(2026, 10, 15)
SCOPE = CalendarScope("route-a", "XSH", CalendarContractKind.FUTURE, "cu", "cu2602")
SOURCES = (
    CalendarSource("sim", "simulation://rules"),
    CalendarSource("exception", "simulation://exception"),
)


def interval(start, end):
    return UtcInterval(
        datetime.fromisoformat(start).replace(tzinfo=UTC),
        datetime.fromisoformat(end).replace(tzinfo=UTC),
    )


def dataset():
    days = [FRIDAY + timedelta(days=i) for i in range(7)]
    source = ["sim"]

    def rule(kind, contracts, night):
        return {
            "venue": "XSH",
            "kind": kind,
            "product": "cu",
            "contracts": contracts,
            "valid_from": FRIDAY.isoformat(),
            "valid_to": THURSDAY.isoformat(),
            "day_windows": [
                {
                    "id": "day",
                    "phase": "continuous",
                    "start": "09:00",
                    "end": "11:00",
                    "end_day_offset": 0,
                }
            ],
            "night_windows": (
                [
                    {
                        "id": "night",
                        "phase": "continuous",
                        "start": "21:00",
                        "end": "01:00",
                        "end_day_offset": 1,
                    }
                ]
                if night
                else []
            ),
            "night_mappings": [
                {
                    "trading_day": day.isoformat(),
                    "start_date": (FRIDAY.isoformat() if day == MONDAY and night else None),
                    "source_ids": source,
                }
                for day in (FRIDAY, MONDAY, TUESDAY, date(2026, 10, 14), THURSDAY)
            ],
            "windows_complete": True,
            "exceptions_complete": True,
            "source_ids": source,
        }

    return {
        "sources": [
            {"id": "sim", "description": "synthetic"},
            {"id": "exception", "description": "synthetic exception"},
        ],
        "trading_days": [
            {
                "venue": "XSH",
                "day": day.isoformat(),
                "status": "closed" if day.weekday() >= 5 else "open",
                "source_ids": source,
            }
            for day in days
        ],
        "trading_hours": [
            rule("future", ["cu2601", "cu2602"], True),
            rule("option", ["cu2601-C-100"], False),
        ],
        "exceptions": [
            {
                "venue": "XSH",
                "kind": "future",
                "product": "cu",
                "contract": "cu2601",
                "trading_day": MONDAY.isoformat(),
                "action": "close_day",
                "window_id": None,
                "source_ids": ["exception"],
            }
        ],
    }


def publish(store, data, version="v1"):
    import json

    bundle = compose_simulated(
        mode=SIMULATION_ONLY,
        venues=(VenueContent.from_layers("XSH", FRIDAY, THURSDAY, data),),
        snapshot_factory=CalendarSnapshot,
    )
    assert bundle.status == "READY_SIMULATED"
    result = store.publish(
        json.loads(bundle.snapshot_bytes),
        publication_id=f"pub-{version}",
        version_id=version,
        coverage_manifest_bytes=bundle.manifest_bytes,
        coverage_manifest_sha256=bundle.manifest_sha256,
        binding_bytes=bundle.binding_bytes,
        binding_sha256=bundle.binding_sha256,
    )
    assert result.visibility == "COMMITTED"
    return store.read_current()


def closure(stored, scope=SCOPE, span=None, first=FRIDAY, last=TUESDAY, lead=3, sources=SOURCES):
    return SimulationUtcClosure(
        scope,
        CalendarVersion(stored.version_id, stored.content_sha256),
        stored.coverage_manifest_sha256,
        stored.binding_sha256,
        stored.version_sha256,
        span or interval("2026-10-09T13:00", "2026-10-09T17:00"),
        first,
        last,
        lead,
        ("sim",),
        sources,
    )


class StaticStore:
    def __init__(self, current):
        self.current = current
        self.reads = 0

    def read_current(self):
        self.reads += 1
        return self.current


def projector(stored, declaration):
    return SimulationCalendarQuery(
        mode=SIMULATION_ONLY, store=StaticStore(stored), closure=declaration
    )


def query(projection, declaration, scope=None, span=None):
    return projection.query(CalendarQuery(scope or declaration.scope, span or declaration.interval))


@pytest.fixture
def stored(tmp_path):
    return publish(FixtureCalendarStore(tmp_path), dataset())


def test_friday_night_is_monday_and_half_open_clipped(stored):
    declared = closure(stored)
    clipped = interval("2026-10-09T14:00", "2026-10-09T16:00")
    result = query(projector(stored, declared), declared, span=clipped)
    assert result.status is CalendarCoverageStatus.COMPLETE
    assert result.version == declared.version and len(result.windows) == 1
    window = result.windows[0]
    assert window.interval == clipped and window.trading_day == MONDAY
    assert (window.start_local.hour, window.end_local.hour, window.end_local.date()) == (
        22,
        0,
        date(2026, 10, 10),
    )
    assert result.sources == (SOURCES[0],)
    boundary = interval("2026-10-09T17:00", "2026-10-09T17:30")
    declaration = closure(stored, span=boundary)
    assert query(projector(stored, declaration), declaration).known_empty


def test_contract_and_option_exceptions_do_not_leak(stored):
    for scope, expected in (
        (replace(SCOPE, instrument="cu2601"), True),
        (SCOPE, False),
        (replace(SCOPE, kind=CalendarContractKind.OPTION, instrument="cu2601-C-100"), False),
    ):
        span = interval("2026-10-12T01:00", "2026-10-12T03:00")
        declared = closure(stored, scope, span, MONDAY, THURSDAY, 3)
        result = query(projector(stored, declared), declared)
        assert result.status is CalendarCoverageStatus.COMPLETE
        assert result.known_empty is expected
        assert ("exception" in {s.source_id for s in result.sources}) is expected


def test_product_exception_applies_only_to_its_kind(tmp_path):
    data = dataset()
    data["exceptions"] = [{**data["exceptions"][0], "contract": None}]
    stored = publish(FixtureCalendarStore(tmp_path), data)
    span = interval("2026-10-12T01:00", "2026-10-12T03:00")
    for scope, empty in (
        (replace(SCOPE, instrument="cu2601"), True),
        (SCOPE, True),
        (replace(SCOPE, kind=CalendarContractKind.OPTION, instrument="cu2601-C-100"), False),
    ):
        declared = closure(stored, scope, span, MONDAY, THURSDAY, 3)
        result = query(projector(stored, declared), declared)
        assert result.status is CalendarCoverageStatus.COMPLETE and result.known_empty is empty


def test_entire_closure_explicitly_closed_is_known_empty(tmp_path):
    data = dataset()
    for day in data["trading_days"]:
        day["status"] = "closed"
    for row in data["trading_hours"]:
        row["night_mappings"] = []
    data["exceptions"] = []
    stored = publish(FixtureCalendarStore(tmp_path), data)
    span = interval("2026-10-09T02:00", "2026-10-09T03:00")
    declared = closure(stored, span=span, first=FRIDAY, last=FRIDAY, lead=0)
    assert query(projector(stored, declared), declared).known_empty


def test_closed_day_and_missing_day_are_distinct(tmp_path):
    data = dataset()
    # The explicit closure includes every potential owner through Tuesday.
    span = interval("2026-10-10T02:00", "2026-10-10T03:00")
    full = publish(FixtureCalendarStore(tmp_path / "full"), data)
    declared = closure(full, span=span, first=date(2026, 10, 10), last=TUESDAY, lead=3)
    assert query(projector(full, declared), declared).known_empty
    data["trading_days"] = [row for row in data["trading_days"] if row["day"] != "2026-10-10"]
    missing = publish(FixtureCalendarStore(tmp_path / "missing"), data)
    declared = closure(missing, span=span, first=date(2026, 10, 10), last=TUESDAY, lead=3)
    assert query(projector(missing, declared), declared).status is CalendarCoverageStatus.UNKNOWN


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", replace(SCOPE, route="other")),
        ("version", CalendarVersion("v2", "0" * 64)),
        ("coverage_manifest_sha256", "0" * 64),
        ("binding_sha256", "0" * 64),
        ("version_sha256", "0" * 64),
        ("max_night_lead_days", 2),
        ("assumption", ""),
        ("source_ids", ("missing",)),
        ("sources", (CalendarSource("missing", "simulation://x"),)),
        ("sources", ()),
    ],
)
def test_mismatched_declaration_is_unknown(stored, field, value):
    declared = closure(stored)
    broken = replace(declared, **{field: value})
    result = query(projector(stored, broken), declared)
    assert result.status is CalendarCoverageStatus.UNKNOWN and not result.windows


def test_missing_source_and_exact_scope_are_unknown(stored):
    declared = closure(stored, scope=replace(SCOPE, instrument="cu2601"), sources=(SOURCES[0],))
    assert query(projector(stored, declared), declared).status is CalendarCoverageStatus.UNKNOWN
    declared = closure(stored)
    projection = projector(stored, declared)
    for scope in (
        replace(SCOPE, route="other"),
        replace(SCOPE, venue="OTHER"),
        replace(SCOPE, product="other"),
        replace(SCOPE, instrument="cu9999"),
        replace(SCOPE, kind=CalendarContractKind.OPTION),
    ):
        assert query(projection, declared, scope=scope).status is CalendarCoverageStatus.UNKNOWN
    assert (
        query(projection, declared, span=interval("2026-10-09T12:00", "2026-10-09T14:00")).status
        is CalendarCoverageStatus.UNKNOWN
    )


def test_unproven_coverage_and_missing_current_are_unknown(stored):
    declared = closure(stored)
    for broken in (
        replace(declared, first_day=date(2026, 10, 10)),
        replace(declared, last_day=date(2026, 10, 11)),
        replace(declared, max_night_lead_days=0),
    ):
        assert query(projector(stored, broken), broken).status is CalendarCoverageStatus.UNKNOWN
    projection = projector(replace(stored, content_sha256="0" * 64), declared)
    assert query(projection, declared).status is CalendarCoverageStatus.UNKNOWN
    assert query(projector(None, declared), declared).status is CalendarCoverageStatus.UNKNOWN
    with pytest.raises(ValueError):
        SimulationCalendarQuery(mode="LIVE", store=StaticStore(stored), closure=declared)


def test_concurrent_queries_pin_one_snapshot(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    first = publish(store, dataset())
    declared = closure(first)
    current = StaticStore(first)
    pinned = SimulationCalendarQuery(mode=SIMULATION_ONLY, store=current, closure=declared)
    changed = dataset()
    changed["trading_hours"][0]["night_windows"][0]["start"] = "22:00"
    current.current = publish(store, changed, "v2")
    newer = SimulationCalendarQuery(
        mode=SIMULATION_ONLY, store=current, closure=closure(current.current)
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: query(pinned, declared), range(100)))
    assert all(result == results[0] and result.version.calendar_ref == "v1" for result in results)
    assert current.reads == 2
    assert results[0].windows[0].interval.start_utc.hour == 13
    assert query(newer, closure(current.current)).windows[0].interval.start_utc.hour == 14


def test_resource_limits_fail_closed(stored):
    declared = closure(stored)
    oversized = deepcopy(stored.content)
    oversized["sources"][0]["description"] = "x" * (16 * 1024 * 1024 + 1)
    cyclic = deepcopy(stored.content)
    cyclic["sources"].append(cyclic["sources"])
    for invalid in (
        replace(stored, content=oversized),
        replace(stored, content=cyclic),
        replace(stored, binding_bytes=b"x" * (16 * 1024 * 1024 + 1)),
    ):
        assert (
            query(projector(invalid, declared), declared).status is CalendarCoverageStatus.UNKNOWN
        )
    assert (
        query(projector(stored, replace(declared, source_ids=("sim",) * 1001)), declared).status
        is CalendarCoverageStatus.UNKNOWN
    )


def test_window_work_rejected_before_provider_construction(stored, monkeypatch):
    content = deepcopy(stored.content)
    row = content["trading_hours"][0]
    content["trading_days"] = [content["trading_days"][0]] * 700
    content["trading_hours"] = [row]
    content["exceptions"] = []
    row["contracts"] = [f"contract-{i}" for i in range(50)]
    row["day_windows"] = [row["day_windows"][0]] * 1000
    row["night_windows"] = []
    with pytest.raises(ValueError, match="window work limit"):
        simulation._bounded_content(content)
    provider = Mock()
    covered_source = Mock()
    monkeypatch.setattr(simulation, "CalendarSnapshot", provider)
    monkeypatch.setattr(simulation, "_covered_source", covered_source)
    declared = closure(stored)
    result = query(projector(replace(stored, content=content), declared), declared)
    assert result.status is CalendarCoverageStatus.UNKNOWN
    covered_source.assert_not_called()
    provider.assert_not_called()


def test_window_work_budget_counts_all_rows_at_boundary(stored, monkeypatch):
    import json

    content = dataset()
    content["trading_days"] = content["trading_days"][:2]
    content["exceptions"] = []
    for row in content["trading_hours"]:
        row["contracts"] = row["contracts"][:1]
        row["day_windows"] = [row["day_windows"][0]] * 3
        row["night_windows"] = []
    # 2 rows * 2 days * 3 windows * (2 + 1 contract * 5) = 84.
    monkeypatch.setattr(simulation, "_MAX_BUILD_WORK", 84)
    simulation._bounded_content(content)
    provider = Mock()
    monkeypatch.setattr(simulation, "CalendarSnapshot", provider)
    monkeypatch.setattr(
        simulation, "_covered_source", Mock(return_value=((), json.dumps(content).encode()))
    )
    declared = closure(stored)
    projector(replace(stored, content=content), declared)
    provider.assert_called_once()
    monkeypatch.setattr(simulation, "_MAX_BUILD_WORK", 83)
    with pytest.raises(ValueError, match="window work limit"):
        simulation._bounded_content(content)
    projector(replace(stored, content=content), declared)
    provider.assert_called_once()
