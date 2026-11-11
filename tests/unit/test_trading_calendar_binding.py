"""Offline simulation using a real temporary store and a fake day-switch gate."""

from dataclasses import replace
from datetime import date
from threading import Event, Thread

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py.trading_calendar_binding import (
    SIMULATION_ONLY,
    BindingUnavailableError,
    GateDecision,
    SimulationCalendarBinding,
)
from bt_api_py.trading_calendar_store import TradingCalendarStore

DAY1 = date(2026, 1, 1)
DAY2 = date(2026, 1, 2)
DAY3 = date(2026, 1, 3)


def content(start="09:00"):
    source = ["synthetic"]
    return {
        "sources": [{"id": "synthetic", "description": "fictional unit-test source"}],
        "trading_days": [
            {"venue": "SIM", "day": day, "status": "open", "source_ids": source}
            for day in ("2026-01-01", "2026-01-02", "2026-01-03")
        ],
        "trading_hours": [
            {
                "venue": "SIM",
                "kind": "future",
                "product": "x",
                "contracts": ["x2601"],
                "valid_from": "2026-01-01",
                "valid_to": "2026-01-03",
                "day_windows": [
                    {
                        "id": "day",
                        "phase": "continuous",
                        "start": start,
                        "end": "11:00",
                        "end_day_offset": 0,
                    }
                ],
                "night_windows": [],
                "night_mappings": [
                    {"trading_day": day, "start_date": None, "source_ids": source}
                    for day in ("2026-01-01", "2026-01-02", "2026-01-03")
                ],
                "windows_complete": True,
                "exceptions_complete": True,
                "source_ids": source,
            }
        ],
        "exceptions": [],
    }


def decision(day=DAY2, sequence=2, event_id="event2", route="route-a", generation="gen-1"):
    return GateDecision(True, route, generation, day, sequence, event_id)


class FakeGate:
    def __init__(self):
        self.contexts = []

    def verify(self, event, context):
        self.contexts.append(context)
        if isinstance(event, Exception):
            raise event
        return event


def binding(store, gate=None, factory=CalendarSnapshot, day=DAY1):
    return SimulationCalendarBinding(
        mode=SIMULATION_ONLY,
        store=store,
        snapshot_factory=factory,
        gate=gate or FakeGate(),
        route="route-a",
        source_generation="gen-1",
        trading_day=day,
        last_sequence=1,
    )


def publish(store, version, data):
    return store.publish(data, publication_id=f"pub-{version}", version_id=version)


def query(bound, day=DAY1, **kwargs):
    scope = {"venue": "SIM", "kind": "future", "product": "x", "contract": "x2601"}
    scope.update(kwargs)
    return bound.query(**scope, trading_day=day)


def test_pins_until_gated_day_change_and_passes_provider_results(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    gate = FakeGate()
    bound = binding(store, gate)
    before = query(bound)
    assert before.binding.calendar_ref == before.result.calendar_ref == "v1"
    assert before.result.windows[0].start_local.hour == 9
    assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
    assert query(bound).result.windows[0].start_local.hour == 9
    assert bound.status().calendar_ref == "v1"
    changed = bound.activate(decision())
    assert (changed.outcome, changed.binding.calendar_ref, changed.binding.bound_trading_day) == (
        "ACTIVATED",
        "v2",
        DAY2,
    )
    assert changed.binding.binding_reason == "GATED_DAY_SWITCH"
    assert changed.binding.publication_id == "pub-v2"
    assert query(bound, day=DAY2).result.windows[0].start_local.hour == 10
    assert gate.contexts[0].route == "route-a"
    assert gate.contexts[0].source_generation == "gen-1"
    assert gate.contexts[0].bound_trading_day == DAY1
    assert bound.activate(decision()).outcome == "DUPLICATE"
    assert (
        bound.activate(decision(day=DAY2, sequence=3, event_id="other")).outcome == "GATE_REJECTED"
    )
    assert query(bound, day=date(2027, 1, 1)).result.status == "UNKNOWN"
    assert query(bound, kind="option", contract="x2601-C-100").result.status == "OUT_OF_SCOPE"


@pytest.mark.parametrize(
    "event",
    [
        decision(route="other"),
        decision(generation="old"),
        decision(day=DAY1),
        decision(day=date(2025, 12, 31)),
        decision(sequence=1),
        replace(decision(), accepted=False),
        replace(decision(), accepted=1),
        replace(decision(), event_id=""),
        None,
        RuntimeError("gate failed"),
    ],
)
def test_unproven_gate_never_switches(tmp_path, event):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    bound = binding(store)
    assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
    initial = bound.status()
    result = bound.activate(event)
    assert result.outcome == ("GATE_ERROR" if isinstance(event, Exception) else "GATE_REJECTED")
    assert result.binding == initial
    assert query(bound).result.windows[0].start_local.hour == 9


def test_broken_current_keeps_old_and_same_event_can_retry(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    bound = binding(store)
    assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
    current = tmp_path / "CURRENT.json"
    valid = current.read_bytes()
    current.write_bytes(b"{}")
    result = bound.activate(decision())
    assert result.outcome == "LOAD_FAILED"
    assert (
        result.binding.calendar_ref,
        result.binding.bound_trading_day,
        result.binding.load_error,
    ) == (
        "v1",
        DAY1,
        "STORE_READ_FAILED",
    )
    assert query(bound).result.windows[0].start_local.hour == 9
    current.write_bytes(valid)
    result = bound.activate(decision())
    assert result.outcome == "ACTIVATED" and result.binding.calendar_ref == "v2"
    assert result.binding.load_error is None


def test_empty_store_and_empty_or_invalid_provider_snapshot_are_not_bound(tmp_path):
    store = TradingCalendarStore(tmp_path)
    empty = binding(store)
    assert empty.status().load_error == "STORE_READ_FAILED"
    with pytest.raises(BindingUnavailableError):
        query(empty)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    bound = binding(store)
    empty_layers = {"trading_days": [], "trading_hours": [], "exceptions": [], "sources": []}
    assert publish(store, "v2", empty_layers).visibility == "COMMITTED"
    result = bound.activate(decision())
    assert result.outcome == "LOAD_FAILED"
    assert result.binding.load_error == "SNAPSHOT_VALIDATION_FAILED"
    assert result.binding.calendar_ref == "v1"
    assert query(bound).result.status == "COMPLETE"


def test_factory_exception_or_none_does_not_replace_valid_binding(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    bound = binding(store)
    assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
    for factory in (
        lambda *a, **kw: None,
        lambda *a, **kw: (_ for _ in ()).throw(ValueError("bad")),
    ):
        bound._factory = factory
        result = bound.activate(decision())
        assert (result.outcome, result.binding.calendar_ref, result.binding.load_error) == (
            "LOAD_FAILED",
            "v1",
            "SNAPSHOT_VALIDATION_FAILED",
        )
    bound._factory = CalendarSnapshot
    assert bound.activate(decision()).binding.calendar_ref == "v2"


def test_same_day_new_process_rebinds_latest(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    original = binding(store)
    assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
    restarted = binding(store, day=DAY1)
    assert original.status().calendar_ref == "v1"
    assert restarted.status().calendar_ref == "v2"
    assert restarted.status().binding_reason == "PROCESS_START_OR_RESTART"
    assert restarted.status().bound_trading_day == DAY1


def test_only_one_store_read_and_no_mixing_if_publication_changes(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"

    class SwitchingStore:
        reads = 0

        def read_current(self):
            self.reads += 1
            snapshot = store.read_current()
            assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
            return snapshot

    switching = SwitchingStore()
    bound = binding(switching)
    got = query(bound)
    assert switching.reads == 1
    assert (
        got.binding.calendar_ref,
        got.result.calendar_ref,
        got.result.windows[0].start_local.hour,
    ) == ("v1", "v1", 9)
    assert store.read_current().version_id == "v2"


def test_racing_older_gate_cannot_overwrite_newer_binding(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    ready, release = Event(), Event()

    class RacingGate:
        def verify(self, event, context):
            if event.event_id == "slow":
                ready.set()
                assert release.wait(10)
            return event

    bound = binding(store, RacingGate())
    assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
    results = []
    worker = Thread(target=lambda: results.append(bound.activate(decision(event_id="slow"))))
    worker.start()
    try:
        assert ready.wait(10)
        assert (
            bound.activate(decision(day=DAY3, sequence=3, event_id="fast")).outcome == "ACTIVATED"
        )
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert results[0].outcome == "STALE"
    assert (bound.status().bound_trading_day, bound.status().last_sequence) == (DAY3, 3)


def test_query_snapshot_and_binding_remain_matched_during_switch(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    ready, release = Event(), Event()

    class SlowSnapshot:
        def __init__(self, data, *, calendar_ref):
            self.inner = CalendarSnapshot(data, calendar_ref=calendar_ref)

        def query(self, **kwargs):
            ready.set()
            assert release.wait(10)
            return self.inner.query(**kwargs)

    bound = binding(store, factory=SlowSnapshot)
    assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
    results = []
    worker = Thread(target=lambda: results.append(query(bound)))
    worker.start()
    try:
        assert ready.wait(10)
        assert bound.activate(decision()).binding.calendar_ref == "v2"
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    old = results[0]
    assert old.binding.calendar_ref == old.result.calendar_ref == "v1"
    assert old.result.windows[0].start_local.hour == 9
    assert bound.status().calendar_ref == "v2"


def test_requires_explicit_simulation_only_mode(tmp_path):
    with pytest.raises(ValueError):
        SimulationCalendarBinding(
            mode="production",
            store=TradingCalendarStore(tmp_path),
            snapshot_factory=CalendarSnapshot,
            gate=FakeGate(),
            route="route-a",
            source_generation="gen-1",
            trading_day=DAY1,
            last_sequence=1,
        )
