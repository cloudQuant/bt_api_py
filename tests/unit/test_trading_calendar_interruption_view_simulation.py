"""Direct local Store, fixed view and CTP prepared ports; MOCK only."""

import hashlib
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from bt_api_base.trading_session_protocol import TradingPhase
from bt_api_ctp.calendar_snapshot import CalendarSnapshot
from bt_api_ctp.simulation_calendar_ports import (
    SimulationCalendarUnavailable,
    SimulationMapping,
    SimulationOwnerClosure,
    create_prepared_simulation_calendar_ports,
)

from bt_api_py.trading_calendar_binding import GateDecision, SimulationCalendarReadView
from bt_api_py.trading_calendar_store import _canonical
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_binding import binding

MOCK = "MOCK.simulation_only"
OWNER = date(2026, 6, 2)
FIRST, LAST = date(2026, 1, 1), date(2026, 12, 31)
FUTURE = SimulationMapping("declared.future", "SHFE", "future", "p", "mock_f1")
SECOND = SimulationMapping("declared.second", "SHFE", "future", "p", "mock_f2")
OPTION = SimulationMapping("declared.option", "SHFE", "option", "p", "mock_opt")


def fixture(*, end="2026-06-02T02:30:00.000002Z"):
    days = [FIRST + timedelta(days=i) for i in range(365)]

    def hours(kind, contracts):
        return {
            "venue": "SHFE",
            "kind": kind,
            "product": "p",
            "contracts": contracts,
            "valid_from": FIRST.isoformat(),
            "valid_to": LAST.isoformat(),
            "day_windows": [
                {
                    "id": "day",
                    "phase": "continuous",
                    "start": "09:00",
                    "end": "11:00",
                    "end_day_offset": 0,
                }
            ],
            "night_windows": [
                {
                    "id": "night",
                    "phase": "continuous",
                    "start": "21:00",
                    "end": "01:00",
                    "end_day_offset": 1,
                }
            ],
            "night_mappings": [
                {"trading_day": OWNER.isoformat(), "start_date": "2026-06-01", "source_ids": [MOCK]}
            ],
            "source_ids": [MOCK],
            "windows_complete": True,
            "exceptions_complete": True,
        }

    def exception(name, start, finish):
        return {
            "venue": "SHFE",
            "kind": "future",
            "product": "p",
            "contract": None,
            "trading_day": OWNER.isoformat(),
            "action": "interrupt_window",
            "window_id": name,
            "exception_schema": "bt_api.calendar-window-interruption/1",
            "start_utc": start,
            "end_utc": finish,
            "source_ids": [MOCK],
        }

    return {
        "sources": [{"id": MOCK, "description": "simulation"}],
        "trading_days": [
            {
                "venue": "SHFE",
                "day": day.isoformat(),
                "status": "open" if day == OWNER else "closed",
                "source_ids": [MOCK],
            }
            for day in days
        ],
        "trading_hours": [hours("future", ["mock_f1", "mock_f2"]), hours("option", ["mock_opt"])],
        "exceptions": [
            exception("day", "2026-06-02T02:00:00.000001Z", end),
            exception("night", "2026-06-01T15:30:00.000001Z", "2026-06-01T16:15:00.000002Z"),
        ],
    }


def publish(store, content, number):
    snapshot_sha = hashlib.sha256(_canonical(content)).hexdigest()
    manifest = {
        "schema": "bt_api.calendar-simulation-coverage/1",
        "snapshot_sha256": snapshot_sha,
        "coverage": [
            {
                "venue": "SHFE",
                "coverage_start": FIRST.isoformat(),
                "coverage_end": LAST.isoformat(),
                "semantics": "DECLARED_INCLUSIVE",
            }
        ],
    }
    manifest_bytes = _canonical(manifest)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    bind = {
        "schema": "bt_api.calendar-simulation-binding/1",
        "snapshot_sha256": snapshot_sha,
        "manifest_sha256": manifest_sha,
    }
    bind_bytes = _canonical(bind)
    result = store.publish(
        content,
        publication_id=f"pub-v{number}",
        version_id=f"v{number}",
        coverage_manifest_bytes=manifest_bytes,
        coverage_manifest_sha256=manifest_sha,
        binding_bytes=bind_bytes,
        binding_sha256=hashlib.sha256(bind_bytes).hexdigest(),
    )
    assert result.visibility == "COMMITTED"


def query(view, mapping=FUTURE):
    return view.query(
        venue=mapping.venue,
        kind=mapping.kind,
        product=mapping.product,
        contract=mapping.mock_contract,
        trading_day=OWNER,
    )


def ports(view, *, mappings=(FUTURE, SECOND, OPTION), budget=131072, advance=1):
    closure = SimulationOwnerClosure(
        view.status().version_sha256,
        datetime(2026, 6, 1, 13, tzinfo=UTC),
        datetime(2026, 6, 2, 4, tzinfo=UTC),
        OWNER - timedelta(days=1),
        OWNER + timedelta(days=1),
        advance,
        0,
    )
    return create_prepared_simulation_calendar_ports(
        mode="SIMULATION_ONLY",
        view=view,
        mappings=mappings,
        closure=closure,
        required_instruments=tuple(m.instrument_id for m in mappings),
        required_years=(("SHFE", 2026),),
        max_cached_windows=budget,
    )


def test_direct_store_view_prepared_boundaries_and_v1_v2(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    data = fixture()
    publish(store, data, 1)
    bound = binding(store, day=date(2026, 6, 1))
    old = bound.capture_read_view(mode="SIMULATION_ONLY")
    result = query(old)
    assert result.result.status == "COMPLETE" and result.result.sources_verified is False
    proof = result.declared_mock_provenance
    assert proof["schema"] == "bt_api.calendar-declared-mock-windows/2"
    assert proof["binding"]["version_sha256"] == old.status().version_sha256
    assert len(proof["windows"]) == len(result.result.windows) == 4
    assert {row["segment"] for row in proof["windows"]} == {"PRE", "POST"}
    assert all(row["source_authentication"] == "NOT_PROVEN" for row in proof["windows"])
    assert query(old, OPTION).declared_mock_provenance["schema"].endswith("/1")
    assert len(query(old, SECOND).result.windows) == 4
    assert query(old, replace(FUTURE, mock_contract="mock_missing")).result.status == "OUT_OF_SCOPE"
    outside = old.query(
        venue="SHFE", kind="future", product="p", contract="mock_f1", trading_day=date(2027, 1, 1)
    )
    assert outside.result.status == "UNKNOWN"
    proof["windows"].clear()
    assert len(query(old).declared_mock_provenance["windows"]) == 4
    prepared = ports(old)
    assert prepared.identity.source_authentication == "NOT_PROVEN"
    assert prepared.expected_days.expected_trading_days("SHFE", 2026) == (OWNER,)
    schedule = prepared.schedule
    instant = datetime(2026, 6, 2, 2, tzinfo=UTC)
    assert schedule.session_state(FUTURE.instrument_id, instant).phase is TradingPhase.CONTINUOUS
    assert (
        schedule.session_state(FUTURE.instrument_id, instant + timedelta(microseconds=1)).phase
        is TradingPhase.CLOSED
    )
    assert (
        schedule.session_state(
            FUTURE.instrument_id, instant + timedelta(minutes=30, microseconds=1)
        ).phase
        is TradingPhase.CLOSED
    )
    assert (
        schedule.session_state(
            FUTURE.instrument_id, instant + timedelta(minutes=30, microseconds=2)
        ).phase
        is TradingPhase.CONTINUOUS
    )
    night = datetime(2026, 6, 1, 15, 30, tzinfo=UTC)
    assert schedule.session_state(FUTURE.instrument_id, night).phase is TradingPhase.CONTINUOUS
    assert (
        schedule.session_state(FUTURE.instrument_id, night + timedelta(microseconds=1)).phase
        is TradingPhase.CLOSED
    )
    assert (
        schedule.session_state(
            FUTURE.instrument_id, datetime(2026, 6, 1, 16, 15, 0, 2, tzinfo=UTC)
        ).phase
        is TradingPhase.CONTINUOUS
    )
    assert (
        len(
            schedule.windows(
                FUTURE.instrument_id,
                datetime(2026, 6, 1, 13, tzinfo=UTC),
                datetime(2026, 6, 2, 4, tzinfo=UTC),
            )
        )
        == 4
    )
    with pytest.raises(SimulationCalendarUnavailable):
        ports(old, budget=3)
    with pytest.raises(SimulationCalendarUnavailable):
        ports(old, advance=0)
    publish(store, fixture(end="2026-06-02T02:45:00.000002Z"), 2)
    assert (
        bound.activate(GateDecision(True, "route-a", "gen-1", OWNER, 2, "event-v2")).outcome
        == "ACTIVATED"
    )
    new = bound.capture_read_view(mode="SIMULATION_ONLY")
    new_ports = ports(new)
    assert old.status().calendar_ref == prepared.identity.calendar_ref == "v1"
    assert new.status().calendar_ref == new_ports.identity.calendar_ref == "v2"
    assert prepared.identity.version_sha256 != new_ports.identity.version_sha256
    assert (
        query(old).declared_mock_provenance["windows"]
        != query(new).declared_mock_provenance["windows"]
    )
    assert (
        schedule.session_state(
            FUTURE.instrument_id, instant + timedelta(minutes=30, microseconds=2)
        ).phase
        is TradingPhase.CONTINUOUS
    )
    assert (
        new_ports.schedule.session_state(
            FUTURE.instrument_id, instant + timedelta(minutes=30, microseconds=2)
        ).phase
        is TradingPhase.CLOSED
    )


@pytest.mark.parametrize(
    "bad", ["id", "phase", "owner", "range", "source", "ref", "scope", "local"]
)
def test_provider_result_must_match_reconstructed_mock(bad, tmp_path):
    class Tampered:
        def __init__(self, data, *, calendar_ref):
            self.snapshot = CalendarSnapshot(data, calendar_ref=calendar_ref)

        def query_venue_day(self, **kwargs):
            return self.snapshot.query_venue_day(**kwargs)

        def query(self, **kwargs):
            row = self.snapshot.query(**kwargs)
            if row.status != "COMPLETE" or not row.windows:
                return row
            first = row.windows[0]
            if bad == "id":
                first = replace(first, window_id="bad")
            elif bad == "phase":
                first = replace(first, phase="auction")
            elif bad == "owner":
                first = replace(first, trading_day=OWNER + timedelta(days=1))
            elif bad == "range":
                first = replace(first, end_utc=first.end_utc - timedelta(microseconds=1))
            elif bad == "local":
                first = replace(first, start_local=first.start_local + timedelta(microseconds=1))
            elif bad == "source":
                return replace(row, source_ids=("other",))
            elif bad == "ref":
                return replace(row, calendar_ref="other")
            elif bad == "scope":
                return replace(row, venue="DCE")
            return replace(row, windows=(first, *row.windows[1:]))

    store = FixtureCalendarStore(tmp_path)
    publish(store, fixture(), 1)
    view = binding(store, factory=Tampered).capture_read_view(mode="SIMULATION_ONLY")
    assert query(view).result.status == "UNKNOWN"
    with pytest.raises(SimulationCalendarUnavailable):
        ports(view, mappings=(FUTURE,))


def test_mixed_interruption_is_unknown_in_view_and_prepared(tmp_path):
    data = fixture()
    store = FixtureCalendarStore(tmp_path)
    publish(store, data, 1)
    bound = binding(store)
    view = bound.capture_read_view(mode="SIMULATION_ONLY")
    fake_mixed = SimulationCalendarReadView(
        CalendarSnapshot(data, calendar_ref="v1"), view.status(), {"schema": "fake"}, deepcopy(data)
    )
    assert query(fake_mixed).result.status == "UNKNOWN"
    with pytest.raises(SimulationCalendarUnavailable):
        ports(fake_mixed, mappings=(FUTURE,))


def test_specific_contract_interruption_keeps_sibling_legacy_provenance(tmp_path):
    data = fixture()
    for item in data["exceptions"]:
        item["contract"] = "mock_f1"
    store = FixtureCalendarStore(tmp_path)
    publish(store, data, 1)
    view = binding(store).capture_read_view(mode="SIMULATION_ONLY")
    assert query(view, FUTURE).declared_mock_provenance["schema"].endswith("/2")
    assert query(view, SECOND).declared_mock_provenance["schema"].endswith("/1")
    assert len(query(view, SECOND).result.windows) == 2
    assert (
        len(
            ports(view).schedule.windows(
                SECOND.instrument_id,
                datetime(2026, 6, 1, 13, tzinfo=UTC),
                datetime(2026, 6, 2, 4, tzinfo=UTC),
            )
        )
        == 2
    )


def test_independent_generated_id_collision_is_unknown(tmp_path, monkeypatch):
    from bt_api_py import trading_calendar_binding as module

    store = FixtureCalendarStore(tmp_path)
    publish(store, fixture(), 1)
    view = binding(store).capture_read_view(mode="SIMULATION_ONLY")
    monkeypatch.setattr(module, "_mock_interruption_ids", lambda *args: ("day", "other"))
    assert query(view).result.status == "UNKNOWN"
    with pytest.raises(SimulationCalendarUnavailable):
        ports(view, mappings=(FUTURE,))


@pytest.mark.parametrize(
    "case",
    [
        "exception_source",
        "schema",
        "timestamp",
        "missing_source",
        "day_source",
        "duplicate",
        "inventory",
        "missing_kind",
        "missing_contract",
        "missing_action",
        "layer_shape",
    ],
)
def test_untrusted_factory_cannot_bypass_pinned_mock_declarations(tmp_path, case):
    original = fixture()
    altered = deepcopy(original)
    if case == "exception_source":
        altered["sources"].append({"id": "other", "description": "unverified"})
        altered["exceptions"][0]["source_ids"] = ["other"]
    elif case == "schema":
        altered["exceptions"][0]["exception_schema"] = "other"
    elif case == "timestamp":
        altered["exceptions"][0]["start_utc"] = "2026-06-02T02:00Z"
    elif case == "missing_source":
        del altered["exceptions"][0]["source_ids"]
    elif case == "day_source":
        altered["sources"].append({"id": "other", "description": "unverified"})
        next(row for row in altered["trading_days"] if row["day"] == OWNER.isoformat())[
            "source_ids"
        ] = ["other"]
    elif case == "inventory":
        altered["sources"] = []
    elif case in {"missing_kind", "missing_contract", "missing_action"}:
        del altered["exceptions"][0][case.removeprefix("missing_")]
    elif case == "layer_shape":
        altered["exceptions"] = {"action": "interrupt_window"}
    else:
        altered["exceptions"].append(deepcopy(altered["exceptions"][0]))

    class ForgingFactory:
        def __init__(self, data, *, calendar_ref):
            self.snapshot = CalendarSnapshot(original, calendar_ref=calendar_ref)

        def query(self, **kwargs):
            return self.snapshot.query(**kwargs)

        def query_venue_day(self, **kwargs):
            return self.snapshot.query_venue_day(**kwargs)

    store = FixtureCalendarStore(tmp_path)
    publish(store, altered, 1)
    view = binding(store, factory=ForgingFactory).capture_read_view(mode="SIMULATION_ONLY")
    assert query(view).result.status == "UNKNOWN"
    with pytest.raises(SimulationCalendarUnavailable):
        ports(view, mappings=(FUTURE,))


def test_direct_view_detaches_external_content_before_interruption_flag(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    plain = fixture()
    plain["exceptions"] = []
    publish(store, plain, 1)
    status1 = binding(store).status()
    supplied = deepcopy(plain)
    first = SimulationCalendarReadView(
        CalendarSnapshot(plain, calendar_ref="v1"), status1, None, supplied
    )
    assert query(first).declared_mock_provenance["schema"].endswith("/1")
    supplied["exceptions"].extend(deepcopy(fixture()["exceptions"]))
    assert query(first).declared_mock_provenance["schema"].endswith("/1")
    assert (
        first.query_venue_day(mode="SIMULATION_ONLY", venue="SHFE", trading_day=OWNER).day_status
        == "open"
    )
    inconsistent = SimulationCalendarReadView(
        CalendarSnapshot(plain, calendar_ref="v1"), status1, None, supplied
    )
    assert query(inconsistent).result.status == "UNKNOWN"

    publish(store, supplied, 2)
    status2 = binding(store).status()
    second = SimulationCalendarReadView(
        CalendarSnapshot(supplied, calendar_ref="v2"), status2, None, supplied
    )
    assert query(second).declared_mock_provenance["schema"].endswith("/2")
    supplied["exceptions"].clear()
    assert query(second).declared_mock_provenance["schema"].endswith("/2")
    fresh_plain = SimulationCalendarReadView(
        CalendarSnapshot(supplied, calendar_ref="v1"), status1, None, supplied
    )
    assert query(fresh_plain).declared_mock_provenance["schema"].endswith("/1")


def test_direct_view_detaches_external_mixed_provenance(tmp_path):
    from bt_api_py.trading_calendar_store import TradingCalendarStore
    from tests.unit.test_trading_calendar_bound_session_simulation import bind
    from tests.unit.test_trading_calendar_runtime_mixed_query import (
        SCOPE,
    )
    from tests.unit.test_trading_calendar_runtime_mixed_query import (
        publish as publish_mixed,
    )

    publish_mixed(tmp_path, mixed_version=True)
    store = TradingCalendarStore(str(tmp_path))
    source = store.read_current()
    status = bind(store).status()
    supplied = deepcopy(source.simulation_provenance)
    view = SimulationCalendarReadView(
        CalendarSnapshot(source.content, calendar_ref=status.calendar_ref),
        status,
        supplied,
        source.content,
    )
    initial = view.query(**SCOPE)
    assert initial.result.status == "COMPLETE" and initial.simulation_provenance["windows"]
    initial_id = initial.simulation_provenance["evidence_locator"]["candidate_id"]
    supplied["evidence_locator"]["candidate_id"] = "changed"
    supplied["windows"].clear()
    assert (
        view.query(**SCOPE).simulation_provenance["evidence_locator"]["candidate_id"] == initial_id
    )
    assert view.query(**SCOPE).simulation_provenance["windows"]
    newer = SimulationCalendarReadView(
        CalendarSnapshot(source.content, calendar_ref=status.calendar_ref),
        status,
        supplied,
        source.content,
    )
    assert newer.query(**SCOPE).result.status == "UNKNOWN"
