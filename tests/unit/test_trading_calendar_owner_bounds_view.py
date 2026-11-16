from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py.trading_calendar_binding import SimulationUnknown
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_binding import (
    DAY2,
    binding,
    content,
    decision,
    publish,
    query,
)

SCOPE = {"venue": "SIM", "kind": "future", "product": "x", "contract": "x2601"}


def observe(view, mode="SIMULATION_ONLY"):
    return view.owner_day_bounds(mode=mode, **SCOPE)


def test_captured_view_binds_windows_and_whole_snapshot_bounds(tmp_path):
    data = content()
    row = data["trading_hours"][0]
    row["night_windows"] = [
        {
            "id": "night",
            "phase": "continuous",
            "start": "21:00",
            "end": "01:00",
            "end_day_offset": 1,
        }
    ]
    row["night_mappings"][2]["start_date"] = "2025-12-20"
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", data).visibility == "COMMITTED"
    bound = binding(store)
    old = bound.capture_read_view(mode="SIMULATION_ONLY")
    assert publish(store, "v2", content()).visibility == "COMMITTED"
    assert bound.activate(decision()).outcome == "ACTIVATED"
    new = bound.capture_read_view(mode="SIMULATION_ONLY")
    for view, version, advance in ((old, "v1", 14), (new, "v2", 0)):
        observation = observe(view)
        windows = query(view)
        assert observation.binding is windows.binding is view.status()
        assert observation.bounds.calendar_ref == windows.result.calendar_ref == version
        assert observation.bounds.max_advance_days == advance
        assert observation.bounds.sources_verified is False
        with pytest.raises(FrozenInstanceError):
            observation.binding = None
    assert query(old, day=date(2026, 1, 4)).result.status == "UNKNOWN"
    assert observe(old, "LIVE").bounds.reason == "INVALID_MODE"


@pytest.mark.parametrize(
    "variant",
    ["missing", "error", "wrong_scope", "wrong_ref", "wrong_type", "incomplete", "tampered"],
)
def test_unavailable_or_forged_provider_cannot_supply_complete_bounds(tmp_path, variant):
    class Provider:
        def __init__(self, data, *, calendar_ref):
            self.inner = CalendarSnapshot(data, calendar_ref=calendar_ref)

        def query(self, **kwargs):
            return self.inner.query(**kwargs)

        def owner_day_bounds(self, **kwargs):
            if variant in ("missing", "error"):
                raise AttributeError("unavailable")
            result = self.inner.owner_day_bounds(**kwargs)
            if variant == "wrong_scope":
                return replace(result, contract="other")
            if variant == "wrong_ref":
                return replace(result, calendar_ref="other")
            if variant == "wrong_type":
                return object()
            if variant == "tampered":
                object.__setattr__(result, "max_advance_days", -1)
                return result
            return replace(
                result,
                status="UNKNOWN",
                reason="NO_COMPILED_ROWS",
                max_advance_days=None,
                max_spill_days=None,
            )

    if variant == "missing":
        del Provider.owner_day_bounds
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    view = binding(store, factory=Provider).capture_read_view(mode="SIMULATION_ONLY")
    result = observe(view)
    assert result.binding is view.status()
    assert type(result.bounds) is SimulationUnknown and result.bounds.status == "UNKNOWN"


def test_missing_compiled_day_does_not_turn_bounds_into_day_coverage(tmp_path):
    data = content()
    data["trading_days"] = [row for row in data["trading_days"] if row["day"] != DAY2.isoformat()]
    data["trading_hours"][0]["night_mappings"] = [
        row
        for row in data["trading_hours"][0]["night_mappings"]
        if row["trading_day"] != DAY2.isoformat()
    ]
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", data).visibility == "COMMITTED"
    view = binding(store).capture_read_view(mode="SIMULATION_ONLY")
    assert observe(view).bounds.status == "COMPLETE"
    assert query(view, day=DAY2).result.status == "UNKNOWN"
