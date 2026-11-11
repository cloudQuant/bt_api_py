"""Offline simulation using a real temporary store and a fake day-switch gate."""

import json
from dataclasses import replace
from datetime import date, datetime
from threading import Event, Thread

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py.trading_calendar_binding import (
    SIMULATION_ONLY,
    BindingUnavailableError,
    GateDecision,
    SimulationCalendarBinding,
)
from bt_api_py.trading_calendar_composite import compose_simulated
from bt_api_py.trading_calendar_update_simulation import VenueContent
from tests.calendar_test_store import FixtureCalendarStore as TradingCalendarStore

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


def publish(store, version, data, *, start=DAY1, end=DAY3, factory=CalendarSnapshot):
    grouped = VenueContent.from_layers("SIM", start, end, data)
    bundle = compose_simulated(mode=SIMULATION_ONLY, venues=(grouped,), snapshot_factory=factory)
    assert bundle.status == "READY_SIMULATED"
    return store.publish(
        json.loads(bundle.snapshot_bytes),
        publication_id=f"pub-{version}",
        version_id=version,
        coverage_manifest_bytes=bundle.manifest_bytes,
        coverage_manifest_sha256=bundle.manifest_sha256,
        binding_bytes=bundle.binding_bytes,
        binding_sha256=bundle.binding_sha256,
    )


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
    invalid = content()
    invalid["trading_hours"][0]["day_windows"][0]["start"] = "12:00"

    class FakeAcceptingProvider:
        def query(self, **kwargs):
            return None

    assert (
        publish(store, "v2", invalid, factory=lambda *a, **kw: FakeAcceptingProvider()).visibility
        == "COMMITTED"
    )
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


def test_coverage_only_version_is_pinned_then_switches_on_gated_day(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, "v1", content(), end=DAY2).visibility == "COMMITTED"
    bound = binding(store)
    before = bound.status()
    assert before.coverage_status == "DECLARED_UNVERIFIED"
    assert before.coverage_manifest_sha256 and before.binding_sha256 and before.version_sha256
    assert [(x.venue, x.start_day, x.end_day) for x in before.declared_coverage] == [
        ("SIM", DAY1, DAY2)
    ]
    assert query(bound, day=DAY3).result.reason == "OUTSIDE_DECLARED_TRADING_DAY_COVERAGE"
    assert publish(store, "v2", content(), end=DAY3).visibility == "COMMITTED"
    assert bound.status().content_sha256 == store.read_current().content_sha256
    assert query(bound, day=DAY3).result.status == "UNKNOWN"
    restarted = binding(store, day=DAY1)
    assert query(restarted, day=DAY3).result.status == "COMPLETE"
    assert restarted.status().binding_reason == "PROCESS_START_OR_RESTART"
    result = bound.activate(decision())
    assert result.outcome == "ACTIVATED"
    assert result.binding.coverage_manifest_sha256 != before.coverage_manifest_sha256
    assert result.binding.binding_sha256 != before.binding_sha256
    assert result.binding.version_sha256 != before.version_sha256
    assert query(bound, day=DAY3).result.status == "COMPLETE"
    assert query(bound, day=DAY3).result.sources_verified is False


def test_missing_venue_invalid_day_and_outside_range_never_call_provider(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, "v1", content(), end=DAY2).visibility == "COMMITTED"
    calls = []

    class Spy:
        def __init__(self, data, *, calendar_ref):
            self.inner = CalendarSnapshot(data, calendar_ref=calendar_ref)

        def query(self, **kwargs):
            calls.append(kwargs)
            return self.inner.query(**kwargs)

    bound = binding(store, factory=Spy)
    for kwargs, reason in (
        (
            {
                "venue": "OTHER",
                "kind": "future",
                "product": "x",
                "contract": "x2601",
                "trading_day": DAY1,
            },
            "VENUE_NOT_DECLARED",
        ),
        (
            {
                "venue": "SIM",
                "kind": "future",
                "product": "x",
                "contract": "x2601",
                "trading_day": DAY3,
            },
            "OUTSIDE_DECLARED_TRADING_DAY_COVERAGE",
        ),
        (
            {
                "venue": "SIM",
                "kind": "future",
                "product": "x",
                "contract": "x2601",
                "trading_day": "2026-01-01",
            },
            "INVALID_QUERY_SCOPE",
        ),
        (
            {
                "venue": "SIM",
                "kind": "future",
                "product": "x",
                "contract": "x2601",
                "trading_day": datetime(2026, 1, 1),
            },
            "INVALID_QUERY_SCOPE",
        ),
        (
            {"venue": "SIM", "kind": "future", "product": "x", "trading_day": DAY1},
            "INVALID_QUERY_SCOPE",
        ),
    ):
        result = bound.query(**kwargs)
        assert (result.result.status, result.result.reason, result.result.sources_verified) == (
            "UNKNOWN",
            reason,
            False,
        )
    assert calls == []
    assert query(bound, day=DAY2).result.status == "COMPLETE"
    assert len(calls) == 1


def test_legacy_v1_snapshot_cannot_supply_coverage(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert (
        store.publish(content(), publication_id="legacy", version_id="v1").visibility == "COMMITTED"
    )
    calls = []

    def factory(data, **kwargs):
        calls.append(True)
        return CalendarSnapshot(data, **kwargs)

    bound = binding(store, factory=factory)
    assert (bound.status().coverage_status, bound.status().load_error) == (
        "NOT_PRESENT",
        "COVERAGE_UNAVAILABLE",
    )
    assert calls == []
    with pytest.raises(BindingUnavailableError):
        query(bound)


@pytest.mark.parametrize(
    "field,value",
    [
        ("coverage_manifest_bytes", None),
        ("coverage_manifest_bytes", b"{}"),
        ("coverage_manifest_sha256", "0" * 64),
        ("binding_bytes", b"{}"),
        ("binding_sha256", "0" * 64),
        ("version_sha256", "0" * 64),
        ("content_sha256", "0" * 64),
    ],
)
def test_corrupt_or_missing_artifact_never_binds_or_replaces(tmp_path, field, value):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    valid = store.read_current()
    bound = binding(store)

    class BadStore:
        def read_current(self):
            return replace(valid, **{field: value})

    failed = binding(BadStore())
    assert failed.status().load_error == "COVERAGE_INVALID"
    with pytest.raises(BindingUnavailableError):
        query(failed)
    bound._store = BadStore()
    result = bound.activate(decision())
    assert (result.outcome, result.binding.calendar_ref, result.binding.load_error) == (
        "LOAD_FAILED",
        "v1",
        "COVERAGE_INVALID",
    )
    assert query(bound).result.status == "COMPLETE"


def test_on_disk_manifest_tamper_retains_old_snapshot_and_restart_fails(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    original = binding(store)
    assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
    path = tmp_path / "versions" / "v2.json"
    value = json.loads(path.read_bytes())
    value["manifest"]["coverage"][0]["coverage_end"] = "2026-01-04"
    path.write_text(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    changed = original.activate(decision())
    assert (changed.outcome, changed.binding.calendar_ref, changed.binding.load_error) == (
        "LOAD_FAILED",
        "v1",
        "STORE_READ_FAILED",
    )
    assert query(original).result.windows[0].start_local.hour == 9
    restarted = binding(store)
    assert restarted.status().load_error == "STORE_READ_FAILED"
    with pytest.raises(BindingUnavailableError):
        query(restarted)


def test_provider_result_cannot_claim_verified_sources(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"

    class UntrustedSnapshot:
        def __init__(self, data, *, calendar_ref):
            self.inner = CalendarSnapshot(data, calendar_ref=calendar_ref)

        def query(self, **kwargs):
            return replace(self.inner.query(**kwargs), sources_verified=True)

    bound = binding(store, factory=UntrustedSnapshot)
    got = query(bound)
    assert (got.result.status, got.result.reason, got.result.sources_verified) == (
        "UNKNOWN",
        "PROVIDER_RESULT_INVALID",
        False,
    )


def test_mutating_factory_cannot_replace_existing_binding(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, "v1", content()).visibility == "COMMITTED"
    bound = binding(store)
    assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"

    def mutate(data, *, calendar_ref):
        snapshot = CalendarSnapshot(data, calendar_ref=calendar_ref)
        data["trading_days"].clear()
        return snapshot

    bound._factory = mutate
    result = bound.activate(decision())
    assert (result.outcome, result.binding.calendar_ref, result.binding.load_error) == (
        "LOAD_FAILED",
        "v1",
        "SNAPSHOT_VALIDATION_FAILED",
    )
    assert query(bound).result.windows[0].start_local.hour == 9


def test_explicit_legacy_rollback_cannot_replace_a_covered_binding(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert (
        store.publish(content(), publication_id="legacy", version_id="legacy").visibility
        == "COMMITTED"
    )
    assert publish(store, "v2", content("10:00")).visibility == "COMMITTED"
    bound = binding(store)
    assert bound.status().coverage_status == "DECLARED_UNVERIFIED"
    assert (
        store.publish(
            content(), publication_id="rollback", version_id="legacy", republish=True
        ).visibility
        == "COMMITTED"
    )
    failure = bound.activate(decision())
    assert (
        failure.outcome,
        failure.binding.calendar_ref,
        failure.binding.coverage_status,
        failure.binding.load_error,
    ) == ("LOAD_FAILED", "v2", "DECLARED_UNVERIFIED", "COVERAGE_UNAVAILABLE")
    assert query(bound).result.windows[0].start_local.hour == 10
    restarted = binding(store)
    assert (restarted.status().coverage_status, restarted.status().load_error) == (
        "NOT_PRESENT",
        "COVERAGE_UNAVAILABLE",
    )


def test_multiple_venues_use_their_own_declared_date_ranges(tmp_path):
    store = TradingCalendarStore(tmp_path)
    a = content()
    b = json.loads(json.dumps(content("10:00")))
    for row in b["trading_days"]:
        row["venue"] = "SIM2"
    b["trading_hours"][0]["venue"] = "SIM2"
    b["trading_hours"][0]["product"] = "y"
    b["trading_hours"][0]["contracts"] = ["y2601"]
    venues = (
        VenueContent.from_layers("SIM", DAY1, DAY2, a),
        VenueContent.from_layers("SIM2", DAY2, DAY3, b),
    )
    bundle = compose_simulated(
        mode=SIMULATION_ONLY, venues=venues, snapshot_factory=CalendarSnapshot
    )
    assert bundle.status == "READY_SIMULATED"
    assert (
        store.publish(
            json.loads(bundle.snapshot_bytes),
            publication_id="p",
            version_id="v",
            coverage_manifest_bytes=bundle.manifest_bytes,
            coverage_manifest_sha256=bundle.manifest_sha256,
            binding_bytes=bundle.binding_bytes,
            binding_sha256=bundle.binding_sha256,
        ).visibility
        == "COMMITTED"
    )
    bound = binding(store)
    assert query(bound, day=DAY1).result.status == "COMPLETE"
    assert query(bound, day=DAY3).result.reason == "OUTSIDE_DECLARED_TRADING_DAY_COVERAGE"
    assert (
        query(bound, day=DAY1, venue="SIM2", product="y", contract="y2601").result.reason
        == "OUTSIDE_DECLARED_TRADING_DAY_COVERAGE"
    )
    assert (
        query(bound, day=DAY3, venue="SIM2", product="y", contract="y2601").result.status
        == "COMPLETE"
    )
