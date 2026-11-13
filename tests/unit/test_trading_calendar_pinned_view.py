"""Local simulation views; a fixed binding never establishes venue authority."""

from dataclasses import FrozenInstanceError, replace
from datetime import date
from threading import Event, Thread

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py.trading_calendar_binding import BindingUnavailableError, GateDecision
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
from tests.unit.test_trading_calendar_bound_session_simulation import bind, publish_changed
from tests.unit.test_trading_calendar_runtime_mixed_query import DAY, SCOPE
from tests.unit.test_trading_calendar_runtime_mixed_query import publish as publish_mixed


def venue_day(view, venue="SIM", day=DAY1, mode="SIMULATION_ONLY"):
    return view.query_venue_day(mode=mode, venue=venue, trading_day=day)


def test_real_store_view_keeps_both_queries_on_one_version_after_activation(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    bound = binding(store)
    old = bound.capture_read_view(mode="SIMULATION_ONLY")
    assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
    assert query(old).result.windows[0].start_local.hour == 9
    assert bound.activate(decision()).outcome == "ACTIVATED"
    new = bound.capture_read_view(mode="SIMULATION_ONLY")
    for view, version, hour in ((old, "v1", 9), (new, "v2", 10)):
        product = query(view)
        day = venue_day(view)
        assert (
            view.status().calendar_ref
            == product.binding.calendar_ref
            == day.binding.calendar_ref
            == version
        )
        assert product.result.calendar_ref == version and day.day_status == "open"
        assert product.result.windows[0].start_local.hour == hour
        assert day.source_authentication == "NOT_PROVEN" and day.trade_authorization == "NONE"
    assert old.status().version_sha256 != new.status().version_sha256
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        old.status = lambda: None
    with pytest.raises(FrozenInstanceError):
        old.status().calendar_ref = "changed"
    with pytest.raises(ValueError):
        bound.capture_read_view(mode="LIVE")
    with pytest.raises(TypeError):
        bound.capture_read_view()


def test_mixed_and_mock_attribution_is_detached_and_keeps_original_version(tmp_path):
    publish_mixed(tmp_path, mixed_version=True)
    store = TradingCalendarStore(str(tmp_path))
    bound = bind(store)
    old = bound.capture_read_view(mode="SIMULATION_ONLY")
    mixed = old.query(**SCOPE)
    original = mixed.simulation_provenance["evidence_locator"]["candidate_id"]
    mixed.simulation_provenance["evidence_locator"]["candidate_id"] = "tampered"
    mixed.simulation_provenance["windows"].clear()
    assert old.query(**SCOPE).simulation_provenance["evidence_locator"]["candidate_id"] == original
    option_scope = {
        **SCOPE,
        "kind": "option",
        "product": "poption",
        "contract": "mock_shfe_option_contract",
    }
    mock = old.query(**option_scope)
    assert mock.declared_mock_provenance["windows"]
    mock.declared_mock_provenance["windows"].clear()
    assert old.query(**option_scope).declared_mock_provenance["windows"]
    publish_changed(tmp_path, store)
    assert (
        bound.activate(GateDecision(True, "route-a", "gen-1", DAY, 2, "switch")).outcome
        == "ACTIVATED"
    )
    new = bound.capture_read_view(mode="SIMULATION_ONLY")
    for view, version in ((old, "MOCK_ver"), (new, "MOCK_ver2")):
        q = view.query(**SCOPE)
        day = venue_day(view, "SHFE", DAY)
        assert (
            q.binding.calendar_ref
            == day.binding.calendar_ref
            == view.status().calendar_ref
            == version
        )
        assert q.result.status == day.status == "COMPLETE"
        assert q.simulation_provenance["evidence_locator"]["candidate_id"]


def test_empty_invalid_coverage_and_exception_semantics(tmp_path):
    empty = binding(FixtureCalendarStore(tmp_path)).capture_read_view(mode="SIMULATION_ONLY")
    with pytest.raises(BindingUnavailableError):
        query(empty)
    assert venue_day(empty).reason == "BINDING_UNAVAILABLE"

    root = tmp_path / "data"
    root.mkdir()
    store = FixtureCalendarStore(root)
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
    assert publish(store, "v1", data).visibility == "COMMITTED"
    view = binding(store).capture_read_view(mode="SIMULATION_ONLY")
    assert query(view).result.status == "COMPLETE" and query(view).result.windows == ()
    assert venue_day(view).day_status == "open"
    assert query(view, venue="OTHER").result.status == "UNKNOWN"
    assert query(view, day=date(2027, 1, 1)).result.status == "UNKNOWN"
    assert query(view, contract="missing").result.status == "OUT_OF_SCOPE"
    assert venue_day(view, day=date(2027, 1, 1)).status == "UNKNOWN"
    assert venue_day(view, mode="LIVE").status == "UNKNOWN"
    assert view.query(venue="SIM").result.reason == "INVALID_QUERY_SCOPE"


def test_fake_provider_failure_preserves_unknown_contract(tmp_path):
    class Broken:
        def __init__(self, data, *, calendar_ref):
            self.inner = CalendarSnapshot(data, calendar_ref=calendar_ref)

        def query(self, **kwargs):
            raise RuntimeError("fake")

        def query_venue_day(self, **kwargs):
            raise RuntimeError("fake")

    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    view = binding(store, factory=Broken).capture_read_view(mode="SIMULATION_ONLY")
    assert query(view).result.reason == "PROVIDER_QUERY_FAILED"
    assert venue_day(view).reason == "VENUE_DAY_UNAVAILABLE"


@pytest.mark.parametrize(
    "field,wrong",
    [
        ("venue", "OTHER"),
        ("kind", "option"),
        ("product", "other"),
        ("contract", "x9999"),
        ("trading_day", DAY2),
    ],
)
def test_view_rejects_fake_provider_scope_mismatch_without_changing_binding_query(
    tmp_path, field, wrong
):
    class WrongScope:
        def __init__(self, data, *, calendar_ref):
            self.inner = CalendarSnapshot(data, calendar_ref=calendar_ref)

        def query(self, **kwargs):
            return replace(self.inner.query(**kwargs), **{field: wrong})

        def query_venue_day(self, **kwargs):
            return self.inner.query_venue_day(**kwargs)

    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    bound = binding(store, factory=WrongScope)
    assert query(bound).result.status == "COMPLETE"
    view = bound.capture_read_view(mode="SIMULATION_ONLY")
    result = query(view)
    assert (result.result.status, result.result.reason) == ("UNKNOWN", "PROVIDER_SCOPE_MISMATCH")
    assert result.binding == view.status()
    assert result.simulation_provenance is result.declared_mock_provenance is None
    assert venue_day(view).status == "COMPLETE"


def test_missing_declared_day_remains_unknown_in_both_views(tmp_path):
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
    assert query(view, day=DAY2).result.status == "UNKNOWN"
    assert venue_day(view, day=DAY2).status == "UNKNOWN"


def test_capture_and_activation_are_atomic(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    entered, release = Event(), Event()

    class PausedGate:
        def verify(self, event, context):
            entered.set()
            assert release.wait(5)
            return event

    bound = binding(store, gate=PausedGate())
    assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
    results = []
    thread = Thread(target=lambda: results.append(bound.activate(decision())))
    thread.start()
    try:
        assert entered.wait(5)
        views = [bound.capture_read_view(mode="SIMULATION_ONLY") for _ in range(20)]
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and results[0].outcome == "ACTIVATED"
    views.append(bound.capture_read_view(mode="SIMULATION_ONLY"))
    for view in views:
        version = view.status().calendar_ref
        assert version in {"v1", "v2"}
        assert query(view).result.calendar_ref == venue_day(view).binding.calendar_ref == version
        assert query(view).result.windows[0].start_local.hour == (9 if version == "v1" else 10)
