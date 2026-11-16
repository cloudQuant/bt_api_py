"""Range closure tests use declared synthetic calendars, never venue evidence."""
from datetime import date, datetime, timedelta, timezone
from dataclasses import replace
from zoneinfo import ZoneInfo

from bt_api_py.gateway_calendar_service import ReceiptCheckedCalendarStore
from bt_api_py.trading_calendar_range_simulation import CalendarRangePolicy, query_simulation_range
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_binding import binding, content, publish

UTC = timezone.utc


def setup_long_holiday(tmp_path, *, incomplete=False, missing=False, owner_day=12):
    first, last, owner = date(2026, 1, 2), date(2026, 1, 14), date(2026, 1, owner_day)
    data = content()
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    if missing:
        days.remove(date(2026, 1, 5))
    data["trading_days"] = [{"venue": "SIM", "day": d.isoformat(), "status": "open" if d in {first, owner} else "closed", "source_ids": ["synthetic"]} for d in days]
    row = data["trading_hours"][0]
    row.update(valid_from=first.isoformat(), valid_to=last.isoformat(),
        windows_complete=not incomplete,
        night_windows=[{"id": "night", "phase": "continuous", "start": "21:00", "end": "23:00", "end_day_offset": 0}],
        night_mappings=[{"trading_day": first.isoformat(), "start_date": None, "source_ids": ["synthetic"]},
                        {"trading_day": owner.isoformat(), "start_date": first.isoformat(), "source_ids": ["synthetic"]}])
    option = dict(row, kind="option", product="y", contracts=["y2601"], day_windows=[dict(row["day_windows"][0], id="optionday")],
                  night_windows=[dict(row["night_windows"][0], id="optionnight")])
    data["trading_hours"].append(option)
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "holiday", data, start=first, end=last).visibility == "COMMITTED"
    bound = binding(ReceiptCheckedCalendarStore(TradingCalendarStore(tmp_path)), day=owner)
    return bound.capture_read_view(mode="SIMULATION_ONLY")


def point(day, hour):
    return datetime(2026, 1, day, hour, tzinfo=UTC)


def test_long_holiday_night_and_policy_contradiction(tmp_path):
    view = setup_long_holiday(tmp_path)
    policy = CalendarRangePolicy("holiday-policy", "route-a", "SIM", "future", "x", "x2601", 10, 0)
    result = query_simulation_range(view, policy, point(2, 13), point(2, 14))
    assert result.status == "COMPLETE"
    assert len(result.windows) == 1 and result.windows[0].trading_day == date(2026, 1, 12)
    assert result.windows[0].start_utc == point(2, 13) and result.windows[0].end_utc == point(2, 15)
    shorter = CalendarRangePolicy("short", "route-a", "SIM", "future", "x", "x2601", 2, 0)
    # The far owner is outside the candidates for this request; global bounds
    # still reject the declared lead before a false COMPLETE can be returned.
    rejected = query_simulation_range(view, shorter, point(2, 13), point(2, 14))
    assert rejected.status == "UNKNOWN"
    # A query within permitted day horizon also exposes the contradicted policy.
    rejected = query_simulation_range(view, shorter, point(11, 13), point(11, 14))
    assert rejected.reason == "POLICY_CONTRADICTED"
    option = CalendarRangePolicy("option", "route-a", "SIM", "option", "y", "y2601", 10, 0)
    assert query_simulation_range(view, option, point(2, 13), point(2, 14)).windows[0].window_id == "optionnight"


def test_friday_night_belongs_to_monday(tmp_path):
    view = setup_long_holiday(tmp_path, owner_day=5)
    policy = CalendarRangePolicy("weekend", "route-a", "SIM", "future", "x", "x2601", 3, 0)
    result = query_simulation_range(view, policy, point(2, 13), point(2, 14))
    assert result.status == "COMPLETE" and result.windows[0].trading_day == date(2026, 1, 5)


def test_future_closed_days_and_missing_data(tmp_path):
    view = setup_long_holiday(tmp_path)
    policy = CalendarRangePolicy("holiday", "route-a", "SIM", "future", "x", "x2601", 10, 0)
    # Last local date plus lead must stay in declared coverage; use lead=0 only
    # on a separate data set without any night leads in the broader snapshot.
    future = query_simulation_range(view, policy, point(12, 1), point(13, 1))
    assert future.status == "UNKNOWN" and future.reason == "COVERAGE_UNAVAILABLE"
    other = tmp_path / "missing"; other.mkdir()
    missing_view = setup_long_holiday(other, missing=True)
    missing = query_simulation_range(missing_view, policy, point(2, 13), point(2, 14))
    assert missing.status == "UNKNOWN" and not missing.windows
    assert missing.gaps == ((date(2026, 1, 5), "OWNER_DATA_UNAVAILABLE"),)


def test_explicit_closed_empty_and_future_multi_day(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    data = content()
    data["trading_days"][1]["status"] = "closed"
    data["trading_hours"][0]["night_mappings"] = [m for m in data["trading_hours"][0]["night_mappings"] if m["trading_day"] != "2026-01-02"]
    assert publish(store, "closed", data).visibility == "COMMITTED"
    view = binding(ReceiptCheckedCalendarStore(TradingCalendarStore(tmp_path))).capture_read_view(mode="SIMULATION_ONLY")
    policy = CalendarRangePolicy("closed", "route-a", "SIM", "future", "x", "x2601", 0, 0)
    closed = query_simulation_range(view, policy, point(2, 1), point(2, 3))
    assert closed.status == "COMPLETE" and closed.windows == ()
    multiple = query_simulation_range(view, policy, point(1, 1), point(3, 3))
    assert multiple.status == "COMPLETE" and [w.trading_day for w in multiple.windows] == [date(2026, 1, 1), date(2026, 1, 3)]


def test_simulated_cross_midnight_spill_uses_previous_owner(tmp_path, monkeypatch):
    from bt_api_ctp.calendar_snapshot import CalendarWindow
    from bt_api_py.trading_calendar_binding import SimulationCalendarReadView
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "base", content()).visibility == "COMMITTED"
    view = binding(ReceiptCheckedCalendarStore(TradingCalendarStore(tmp_path))).capture_read_view(mode="SIMULATION_ONLY")
    original_query, original_bounds = SimulationCalendarReadView.query, SimulationCalendarReadView.owner_day_bounds
    start = datetime(2026, 1, 1, 15, 30, tzinfo=UTC)
    end = datetime(2026, 1, 1, 16, 30, tzinfo=UTC)
    window = CalendarWindow("spill", "continuous", date(2026, 1, 1), start, end,
                            start.astimezone(ZoneInfo("Asia/Shanghai")), end.astimezone(ZoneInfo("Asia/Shanghai")))
    def mocked_query(self, **kwargs):
        result = original_query(self, **kwargs)
        return replace(result, result=replace(result.result, windows=(window,))) if kwargs["trading_day"] == date(2026, 1, 1) else result
    def mocked_bounds(self, **kwargs):
        result = original_bounds(self, **kwargs)
        return replace(result, bounds=replace(result.bounds, max_spill_days=1))
    monkeypatch.setattr(SimulationCalendarReadView, "query", mocked_query)
    monkeypatch.setattr(SimulationCalendarReadView, "owner_day_bounds", mocked_bounds)
    policy = CalendarRangePolicy("spill", "route-a", "SIM", "future", "x", "x2601", 0, 1)
    result = query_simulation_range(view, policy, datetime(2026, 1, 1, 16, tzinfo=UTC), datetime(2026, 1, 1, 17, tzinfo=UTC))
    assert result.status == "COMPLETE" and result.windows == (window,)
