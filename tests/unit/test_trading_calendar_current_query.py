"""Explicit route CURRENT queries over synthetic, unauthenticated local artifacts."""

import builtins
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from threading import Event, Thread

import pytest

from bt_api_py import trading_calendar_runtime_status as module
from bt_api_py.bt_api import BtApi
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_query_simulation import (
    FRIDAY,
    MONDAY,
    dataset,
    publish,
)

UTC = timezone.utc  # noqa: UP017 - Python 3.9


def query(api, route="route-a", **overrides):
    scope = {
        "venue": "XSH",
        "kind": "future",
        "product": "cu",
        "contract": "cu2602",
        "trading_day": MONDAY,
    }
    scope.update(overrides)
    return api.trading_calendar_query(route, mode="SIMULATION_ONLY", **scope)


def test_facade_routes_current_and_explicit_window_ownership(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    first, second = FixtureCalendarStore(a), FixtureCalendarStore(b)
    publish(first, dataset())
    different = deepcopy(dataset())
    different["trading_hours"][0]["day_windows"][0]["start"] = "10:00"
    publish(second, different, "v2")
    api = BtApi(debug=False)
    api.configure_trading_calendar_status(route_roots={"route-a": str(a), "route-b": str(b)})
    observed = query(api)
    assert (observed.status, observed.calendar_ref, observed.publication_id) == (
        "COMPLETE",
        "v1",
        "pub-v1",
    )
    night, day = observed.windows
    assert (
        night.window_id,
        night.phase,
        night.trading_day,
        night.start_local.date(),
        night.end_local.date(),
    ) == ("night", "continuous", MONDAY, FRIDAY, FRIDAY.replace(day=10))
    assert (night.start_local.hour, night.end_local.hour, day.start_local.hour) == (21, 1, 9)
    assert night.start_utc == datetime(2026, 10, 9, 13, tzinfo=UTC)
    assert night.end_utc == datetime(2026, 10, 9, 17, tzinfo=UTC)
    assert night.interval_semantics == "START_INCLUSIVE_END_EXCLUSIVE"
    assert night.start_utc <= night.start_utc < night.end_utc
    assert not night.start_utc <= night.end_utc < night.end_utc
    assert (
        observed.source_authentication,
        observed.identity_authentication,
        observed.bound,
        observed.observation,
        observed.trading_day_transition,
    ) == ("NOT_PROVEN", "NOT_PROVEN", "NOT_BOUND", "CURRENT_NOT_PINNED", "NOT_PROVEN")
    assert observed.coverage_status == "DECLARED_UNVERIFIED"
    assert observed.source_ids == ("sim",)
    assert observed.source_classification == "UNVERIFIED"
    assert (
        observed.environment,
        observed.account_use,
        observed.provider,
        observed.provider_selection,
    ) == ("SIMULATION_ONLY", "NOT_AUTHORIZED", "CTP", "EXPLICIT_CTP_ONLY_ROUTE_UNVERIFIED")
    assert query(api, "route-b").windows[-1].start_local.hour == 10
    option = query(api, kind="option", contract="cu2601-C-100")
    assert option.status == "COMPLETE" and len(option.windows) == 1
    assert option.windows[0].start_local.hour == 9
    # Explicit exception may produce no windows but is not certified as a real closure.
    assert query(api, contract="cu2601").windows == ()
    assert query(api, "missing").reason == "ROUTE_UNCONFIGURED"
    assert query(api, contract="missing").status == "UNKNOWN"
    assert query(api, venue="MISSING").status == "UNKNOWN"
    assert query(api, trading_day=date(2027, 1, 1)).status == "UNKNOWN"
    assert query(api, trading_day="2026-10-12").status == "UNKNOWN"
    assert query(api, trading_day=None).status == "UNKNOWN"
    assert BtApi(debug=False).trading_calendar_status("route-a").reason == "ROUTE_UNCONFIGURED"

    # CURRENT is re-read, not pinned or switched through a simulated event.
    publish(first, different, "v3")
    newer = query(api)
    assert (newer.calendar_ref, newer.windows[-1].start_local.hour) == ("v3", 10)
    assert observed.calendar_ref == "v1"


def test_explicit_simulation_mode_and_aggregate_mock_source_ids(tmp_path):
    data = dataset()
    data["sources"].append({"id": "MOCK.simulation_only", "description": "fictional"})
    mapping = next(
        m
        for m in data["trading_hours"][0]["night_mappings"]
        if m["trading_day"] == MONDAY.isoformat()
    )
    mapping["source_ids"].append("MOCK.simulation_only")
    publish(FixtureCalendarStore(tmp_path), data)
    api = BtApi(debug=False)
    api.configure_trading_calendar_status(route_roots={"route-a": str(tmp_path)})
    result = query(api)
    assert result.status == "COMPLETE"
    assert result.source_ids == ("MOCK.simulation_only", "sim")
    assert result.source_classification == "DECLARED_MOCK"
    assert result.source_authentication == "NOT_PROVEN"
    assert result.account_use == "NOT_AUTHORIZED"
    assert all(not hasattr(window, "source_ids") for window in result.windows)
    scope = {
        "venue": "XSH",
        "kind": "future",
        "product": "cu",
        "contract": "cu2602",
        "trading_day": MONDAY,
    }
    for kwargs in ({}, {"mode": "LIVE"}, {"mode": None}):
        denied = api.trading_calendar_query("route-a", **scope, **kwargs)
        assert (denied.status, denied.reason, denied.source_ids, denied.account_use) == (
            "UNKNOWN",
            "SIMULATION_MODE_REQUIRED",
            (),
            "NOT_AUTHORIZED",
        )
    del scope["trading_day"]
    with pytest.raises(TypeError):
        api.trading_calendar_query("route-a", mode="SIMULATION_ONLY", **scope)


def test_missing_coverage_corruption_and_unavailable_provider(tmp_path, monkeypatch):
    root = tmp_path / "missing"
    root.mkdir()
    store = FixtureCalendarStore(root)
    data = dataset()
    assert store.publish(data, publication_id="p", version_id="v").visibility == "COMMITTED"
    api = BtApi(debug=False)
    api.configure_trading_calendar_status(route_roots={"route-a": str(root)})
    assert query(api).status == "UNKNOWN"
    (root / "CURRENT.json").write_bytes(b"invalid")
    result = query(api)
    assert (result.status, result.reason) == ("UNKNOWN", "CURRENT_UNAVAILABLE")
    assert str(root) not in repr(result)

    other = tmp_path / "other"
    other.mkdir()
    publish(FixtureCalendarStore(other), dataset())
    api.configure_trading_calendar_status(route_roots={"route-a": str(other)})
    original = builtins.__import__

    def no_provider(name, *args, **kwargs):
        if name == "bt_api_ctp.calendar_snapshot":
            raise ImportError("unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_provider)
    assert query(api).reason == "PROVIDER_UNAVAILABLE"


def test_concurrent_reconfiguration_invalidates_query(tmp_path, monkeypatch):
    publish(FixtureCalendarStore(tmp_path), dataset())
    api = BtApi(debug=False)
    api.configure_trading_calendar_status(route_roots={"route-a": str(tmp_path)})
    read_done, resume = Event(), Event()
    original = module.TradingCalendarStore.read_current_with_consistent_receipts

    def delayed(store, **kwargs):
        result = original(store, **kwargs)
        read_done.set()
        assert resume.wait(5)
        return result

    monkeypatch.setattr(
        module.TradingCalendarStore, "read_current_with_consistent_receipts", delayed
    )
    results = []
    thread = Thread(target=lambda: results.append(query(api)))
    thread.start()
    try:
        assert read_done.wait(5)
        api.configure_trading_calendar_status(route_roots={"route-a": str(tmp_path)})
    finally:
        resume.set()
        thread.join(5)
    assert not thread.is_alive()
    assert (results[0].status, results[0].reason, results[0].windows) == (
        "UNKNOWN",
        "CONFIGURATION_CHANGED",
        (),
    )


def test_binding_reference_and_read_budget_fail_closed(tmp_path, monkeypatch):
    publish(FixtureCalendarStore(tmp_path), dataset())
    api = BtApi(debug=False)
    api.configure_trading_calendar_status(route_roots={"route-a": str(tmp_path)})
    original = module.TradingCalendarStore.read_current_with_consistent_receipts

    def invalid_binding(store, **kwargs):
        current = original(store, **kwargs)
        return replace(current, snapshot=replace(current.snapshot, binding_sha256="0" * 64))

    with monkeypatch.context() as patch:
        patch.setattr(
            module.TradingCalendarStore, "read_current_with_consistent_receipts", invalid_binding
        )
        assert query(api).reason == "CALENDAR_UNAVAILABLE"
    monkeypatch.setattr(module, "_READ_BUDGET", module.CalendarReadBudget(10, 64_000_000, 10))
    assert query(api).reason == "CURRENT_UNAVAILABLE"


def test_concurrent_current_change_keeps_one_consistent_version(tmp_path, monkeypatch):
    store = FixtureCalendarStore(tmp_path)
    publish(store, dataset())
    api = BtApi(debug=False)
    api.configure_trading_calendar_status(route_roots={"route-a": str(tmp_path)})
    old = query(api)
    read_done, resume = Event(), Event()
    original = module.TradingCalendarStore.read_current_with_consistent_receipts

    def delayed(store, **kwargs):
        result = original(store, **kwargs)
        read_done.set()
        assert resume.wait(5)
        return result

    monkeypatch.setattr(
        module.TradingCalendarStore, "read_current_with_consistent_receipts", delayed
    )
    results = []
    thread = Thread(target=lambda: results.append(query(api)))
    thread.start()
    try:
        assert read_done.wait(5)
        newer = deepcopy(dataset())
        newer["trading_hours"][0]["day_windows"][0]["start"] = "10:00"
        publish(store, newer, "v2")
    finally:
        resume.set()
        thread.join(5)
    assert not thread.is_alive()
    assert results == [old]
