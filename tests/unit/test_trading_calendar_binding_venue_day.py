"""Offline venue-day queries against a pinned simulation binding."""

from dataclasses import replace
from datetime import date, datetime
from threading import Event, Thread

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_binding import (
    DAY1,
    DAY2,
    binding,
    content,
    decision,
    publish,
    query,
)
from tests.unit.test_trading_calendar_review_bundle import VENUES
from tests.unit.test_trading_calendar_runtime_mixed_query import publish as publish_multi


def venue_day(bound, venue="SIM", day=DAY1, mode="SIMULATION_ONLY"):
    return bound.query_venue_day(mode=mode, venue=venue, trading_day=day)


def test_six_venues_open_and_closed_are_explicit(tmp_path):
    publish_multi(tmp_path, mixed_version=False)
    bound = binding(TradingCalendarStore(str(tmp_path)))
    for venue in VENUES:
        opened = venue_day(bound, venue, date(2027, 1, 4))
        closed = venue_day(bound, venue, date(2027, 1, 2))
        assert (opened.status, opened.day_status, opened.source_ids) == (
            "COMPLETE",
            "open",
            ("MOCK.simulation_only",),
        )
        assert (closed.status, closed.day_status, closed.source_ids) == (
            "COMPLETE",
            "closed",
            ("MOCK.simulation_only",),
        )
        assert opened.binding.calendar_ref == closed.binding.calendar_ref == "MOCK_ver"
        assert (opened.source_authentication, opened.trade_authorization) == ("NOT_PROVEN", "NONE")


def test_explicit_venue_day_is_independent_of_contract_closure(tmp_path):
    data = content()
    data["exceptions"] = [
        {
            "venue": "SIM",
            "kind": "future",
            "product": "x",
            "contract": "x2601",
            "trading_day": DAY1.isoformat(),
            "action": "close_day",
            "window_id": None,
            "source_ids": ["synthetic"],
        }
    ]
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", data).visibility == "COMMITTED"
    bound = binding(store)
    assert query(bound).result.windows == ()
    assert venue_day(bound).day_status == "open"


def test_scope_coverage_missing_date_unbound_and_legacy_provider(tmp_path):
    data = content()
    data["trading_days"] = [row for row in data["trading_days"] if row["day"] != DAY2.isoformat()]
    data["trading_hours"][0]["night_mappings"] = [
        row
        for row in data["trading_hours"][0]["night_mappings"]
        if row["trading_day"] != DAY2.isoformat()
    ]
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", data).visibility == "COMMITTED"
    bound = binding(store)
    assert venue_day(bound, day=DAY2).status == "UNKNOWN"
    assert venue_day(bound, venue="OTHER").status == "UNKNOWN"
    assert venue_day(bound, day=date(2027, 1, 1)).status == "UNKNOWN"
    assert venue_day(bound, day=datetime(2026, 1, 1)).status == "UNKNOWN"
    assert venue_day(bound, mode="LIVE").status == "UNKNOWN"
    missing = tmp_path / "missing"
    missing.mkdir()
    assert venue_day(binding(FixtureCalendarStore(missing))).status == "UNKNOWN"

    class Legacy:
        def __init__(self, data, *, calendar_ref):
            self.inner = CalendarSnapshot(data, calendar_ref=calendar_ref)

        def query(self, **kwargs):
            return self.inner.query(**kwargs)

    assert venue_day(binding(store, factory=Legacy)).reason == "PROVIDER_METHOD_UNAVAILABLE"


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "UNKNOWN"},
        {"day_status": "closed"},
        {"calendar_ref": "wrong"},
        {"venue": "OTHER"},
        {"trading_day": DAY2},
        {"source_ids": ()},
        {"source_ids": ("synthetic", "other")},
        {"source_ids": ["synthetic"]},
        {"sources_verified": True},
    ],
)
def test_malformed_provider_never_supplies_a_declaration(tmp_path, changes):
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"

    class Broken:
        def __init__(self, data, *, calendar_ref):
            self.inner = CalendarSnapshot(data, calendar_ref=calendar_ref)

        def query(self, **kwargs):
            return self.inner.query(**kwargs)

        def query_venue_day(self, **kwargs):
            return replace(self.inner.query_venue_day(**kwargs), **changes)

    result = venue_day(binding(store, factory=Broken))
    assert (result.status, result.day_status, result.source_ids) == ("UNKNOWN", None, ())


@pytest.mark.parametrize("raise_error", [False, True])
def test_provider_failure_is_unknown(tmp_path, raise_error):
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"

    class Broken:
        def __init__(self, data, *, calendar_ref):
            self.inner = CalendarSnapshot(data, calendar_ref=calendar_ref)

        def query(self, **kwargs):
            return self.inner.query(**kwargs)

        def query_venue_day(self, **kwargs):
            if raise_error:
                raise RuntimeError("fake provider failure")
            return None

    result = venue_day(binding(store, factory=Broken))
    assert (result.status, result.day_status, result.source_ids) == ("UNKNOWN", None, ())


def test_current_and_concurrent_switch_do_not_mix_versions(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    entered, release = Event(), Event()

    class Paused:
        def __init__(self, data, *, calendar_ref):
            self.inner = CalendarSnapshot(data, calendar_ref=calendar_ref)
            self.ref = calendar_ref

        def query(self, **kwargs):
            return self.inner.query(**kwargs)

        def query_venue_day(self, **kwargs):
            if self.ref == "v1":
                entered.set()
                assert release.wait(5)
            return self.inner.query_venue_day(**kwargs)

    bound = binding(store, factory=Paused)
    new = content()
    new["trading_days"][0]["status"] = "closed"
    new["trading_hours"][0]["night_mappings"] = new["trading_hours"][0]["night_mappings"][1:]
    assert publish(store, "v2", new).visibility == "COMMITTED"
    assert bound.status().calendar_ref == "v1"
    results = []
    thread = Thread(target=lambda: results.append(venue_day(bound)))
    thread.start()
    assert entered.wait(5)
    assert bound.activate(decision()).outcome == "ACTIVATED"
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert (results[0].binding.calendar_ref, results[0].day_status) == ("v1", "open")
    after = venue_day(bound)
    assert (after.binding.calendar_ref, after.day_status) == ("v2", "closed")
    assert after.binding.version_sha256 != results[0].binding.version_sha256
