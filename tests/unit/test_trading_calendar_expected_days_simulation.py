"""Offline structural range checks; none establish real venue calendars."""

import socket
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py.trading_calendar_expected_days_simulation import (
    MAX_RANGE_DAYS,
    query_expected_venue_days,
)
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_binding import (
    DAY1,
    DAY2,
    DAY3,
    binding,
    content,
    decision,
    publish,
)
from tests.unit.test_trading_calendar_review_bundle import VENUES
from tests.unit.test_trading_calendar_runtime_mixed_query import publish as publish_multi


@pytest.fixture(autouse=True)
def reject_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("network is forbidden in this offline test")

    monkeypatch.setattr(socket.socket, "connect", reject)


def query(bound, *, venue="SIM", start=DAY1, end=DAY3, mode="SIMULATION_ONLY"):
    return query_expected_venue_days(bound, mode=mode, venue=venue, start_day=start, end_day=end)


def test_six_venues_include_closed_days_without_contract_inference(tmp_path):
    publish_multi(tmp_path, mixed_version=False)
    bound = binding(TradingCalendarStore(str(tmp_path)))
    for venue in VENUES:
        result = query(bound, venue=venue, start=date(2027, 1, 1), end=date(2027, 1, 4))
        assert result.status == "COMPLETE"
        assert [item.trading_day for item in result.days] == [date(2027, 1, d) for d in range(1, 5)]
        assert result.days[1].day_status == "closed"
        assert result.days[-1].day_status == "open"
        assert all(item.binding == result.binding for item in result.days)
        assert result.binding.calendar_ref == "MOCK_ver"
        assert all(item.source_ids == ("MOCK.simulation_only",) for item in result.days)
        assert (result.mode, result.source_authentication, result.trade_authorization) == (
            "SIMULATION_ONLY",
            "NOT_PROVEN",
            "NONE",
        )


def test_contract_close_exception_does_not_make_venue_closed(tmp_path):
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
    assert (
        bound.query(
            venue="SIM", kind="future", product="x", contract="x2601", trading_day=DAY1
        ).result.windows
        == ()
    )
    assert query(bound, start=DAY1, end=DAY1).days[0].day_status == "open"
    # No contract input exists: neither futures nor options can change venue days.
    assert query(bound).status == "COMPLETE"


def test_missing_coverage_and_day_never_return_partial_results(tmp_path):
    data = content()
    data["trading_days"] = [r for r in data["trading_days"] if r["day"] != DAY2.isoformat()]
    data["trading_hours"][0]["night_mappings"] = [
        r
        for r in data["trading_hours"][0]["night_mappings"]
        if r["trading_day"] != DAY2.isoformat()
    ]
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", data).visibility == "COMMITTED"
    bound = binding(store)
    for result in (
        query(bound),
        query(bound, start=DAY1 - timedelta(days=1), end=DAY1),
        query(bound, start=DAY3, end=DAY3 + timedelta(days=1)),
        query(bound, venue="OTHER"),
    ):
        assert result.status == "UNKNOWN" and result.days == ()
    assert query(bound, start=DAY1, end=DAY1).status == "COMPLETE"
    assert query(bound, start=DAY3, end=DAY3).status == "COMPLETE"


@pytest.mark.parametrize(
    "changes",
    [
        {"calendar_ref": "wrong"},
        {"source_ids": ("other",)},
        {"source_ids": ()},
        {"sources_verified": True},
        {"day_status": "closed"},
        {"trading_day": DAY1},
    ],
)
def test_untrusted_provider_claims_fail_entire_range(tmp_path, changes):
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"

    class Broken:
        def __init__(self, data, *, calendar_ref):
            self.inner = CalendarSnapshot(data, calendar_ref=calendar_ref)

        def query(self, **kwargs):
            return self.inner.query(**kwargs)

        def query_venue_day(self, **kwargs):
            result = self.inner.query_venue_day(**kwargs)
            return replace(result, **changes) if kwargs["trading_day"] == DAY2 else result

    result = query(binding(store, factory=Broken))
    assert result.status == "UNKNOWN" and result.days == ()


@pytest.mark.parametrize("sources", [[], ["absent"], ["synthetic", "synthetic"]])
def test_missing_or_invalid_declared_sources_are_unknown(tmp_path, sources, monkeypatch):
    data = content()

    class Permissive:
        def __init__(self, rows, *, calendar_ref):
            self.rows, self.ref = rows["trading_days"], calendar_ref

        def query(self, **kwargs):
            return None

        def query_venue_day(self, *, venue, trading_day):
            row = next(r for r in self.rows if r["day"] == trading_day.isoformat())
            return SimpleNamespace(
                status="COMPLETE",
                calendar_ref=self.ref,
                venue=venue,
                trading_day=trading_day,
                day_status=row["status"],
                source_ids=tuple(row["source_ids"]),
                sources_verified=False,
            )

    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", data, factory=Permissive).visibility == "COMMITTED"
    bound = binding(store, factory=Permissive)
    view = bound._venue_day_view()
    corrupted = deepcopy(view._content)
    target = next(row for row in corrupted["trading_days"] if row["day"] == DAY2.isoformat())
    target["source_ids"] = sources
    monkeypatch.setattr(bound, "_venue_day_view", lambda: replace(view, _content=corrupted))
    result = query(bound)
    assert result.status == "UNKNOWN" and result.days == ()


def test_legacy_provider_and_input_bounds(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"

    class Legacy:
        def __init__(self, data, *, calendar_ref):
            self.inner = CalendarSnapshot(data, calendar_ref=calendar_ref)

        def query(self, **kwargs):
            return self.inner.query(**kwargs)

    bound = binding(store, factory=Legacy)
    assert query(bound).reason == "PROVIDER_METHOD_UNAVAILABLE"
    assert query(bound, mode="LIVE").reason == "SIMULATION_MODE_REQUIRED"
    assert query(bound, start=DAY2, end=DAY1).reason == "INVALID_QUERY_SCOPE"
    assert query(bound, start=datetime(2026, 1, 1)).reason == "INVALID_QUERY_SCOPE"
    assert (
        query(bound, start=DAY1, end=DAY1 + timedelta(days=MAX_RANGE_DAYS)).reason
        == "RANGE_TOO_LARGE"
    )
    assert query(bound, start=date.max, end=date.max).reason == "COVERAGE_UNAVAILABLE"
    assert query(bound, start=date.min, end=date.min).reason == "COVERAGE_UNAVAILABLE"
    assert query(None).reason == "BINDING_UNAVAILABLE"


def test_switch_between_days_keeps_whole_range_on_old_snapshot(tmp_path):
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
            if self.ref == "v1" and kwargs["trading_day"] == DAY2:
                entered.set()
                assert release.wait(5)
            return self.inner.query_venue_day(**kwargs)

    bound = binding(store, factory=Paused)
    newer = content()
    newer["trading_days"][1]["status"] = "closed"
    newer["trading_hours"][0]["night_mappings"] = [
        r
        for r in newer["trading_hours"][0]["night_mappings"]
        if r["trading_day"] != DAY2.isoformat()
    ]
    assert publish(store, "v2", newer).visibility == "COMMITTED"
    results = []
    thread = Thread(target=lambda: results.append(query(bound)))
    thread.start()
    try:
        assert entered.wait(5)
        assert bound.activate(decision()).outcome == "ACTIVATED"
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    old = results[0]
    new = query(bound)
    assert old.status == new.status == "COMPLETE"
    assert (old.binding.calendar_ref, new.binding.calendar_ref) == ("v1", "v2")
    assert [d.day_status for d in old.days] == ["open", "open", "open"]
    assert [d.day_status for d in new.days] == ["open", "closed", "open"]
    assert all(d.binding == old.binding for d in old.days)
    assert all(d.binding == new.binding for d in new.days)
    assert old.binding.version_sha256 != new.binding.version_sha256
