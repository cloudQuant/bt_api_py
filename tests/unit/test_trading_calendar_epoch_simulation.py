"""Atomic simulation epochs over real local stores and prepared CTP ports."""

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from threading import Event, Thread

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot
from bt_api_ctp.simulation_calendar_ports import (
    SimulationMapping,
    create_prepared_simulation_calendar_ports,
)

from bt_api_py.trading_calendar_binding import GateDecision
from bt_api_py.trading_calendar_composite import compose_simulated
from bt_api_py.trading_calendar_epoch_simulation import (
    SimulationCalendarEpochCoordinator,
    SimulationEpochClosure,
    SimulationEpochEvent,
    SimulationEpochPlan,
    SimulationEpochUnavailableError,
)
from bt_api_py.trading_calendar_update_simulation import VenueContent
from tests.calendar_test_store import FixtureCalendarStore

FIRST = date(2026, 1, 1)
DAY = date(2026, 1, 2)
LAST = date(2026, 12, 31)
START = datetime(2026, 1, 2, 0, tzinfo=UTC)
END = datetime(2026, 1, 2, 4, tzinfo=UTC)
MAPPING = SimulationMapping("sim.rb", "SHFE", "future", "pfuture", "mock_shfe_future_contract")
CLOSURE = SimulationEpochClosure(START, END, DAY, DAY, 0, 0)
EVENT = SimulationEpochEvent("event2", "route", "generation", DAY, 2)


def make_data(start="09:00", end="10:00", missing=None):
    days = [FIRST + timedelta(days=offset) for offset in range(365)]
    source = ["MOCK.simulation_only"]
    return {
        "sources": [{"id": source[0], "description": "fictional"}],
        "trading_days": [
            {
                "venue": "SHFE",
                "day": d.isoformat(),
                "status": "open" if d == DAY else "closed",
                "source_ids": source,
            }
            for d in days
            if d != missing
        ],
        "trading_hours": [
            {
                "venue": "SHFE",
                "kind": "future",
                "product": "pfuture",
                "contracts": ["mock_shfe_future_contract"],
                "valid_from": FIRST.isoformat(),
                "valid_to": LAST.isoformat(),
                "day_windows": [
                    {
                        "id": "day",
                        "phase": "continuous",
                        "start": start,
                        "end": end,
                        "end_day_offset": 0,
                    }
                ],
                "night_windows": [],
                "night_mappings": [
                    {"trading_day": DAY.isoformat(), "start_date": None, "source_ids": source}
                ],
                "windows_complete": True,
                "exceptions_complete": True,
                "source_ids": source,
            }
        ],
        "exceptions": [],
    }


def publish(store, version, data):
    group = VenueContent.from_layers("SHFE", FIRST, LAST, data)
    bundle = compose_simulated(
        mode="SIMULATION_ONLY", venues=(group,), snapshot_factory=CalendarSnapshot
    )
    assert bundle.status == "READY_SIMULATED", bundle
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


class Gate:
    def __init__(self, mode="accept"):
        self.mode = mode
        self.calls = []

    def verify(self, event, context):
        self.calls.append((event, context))
        if self.mode == "throw":
            raise RuntimeError("secret-gate-value")
        if self.mode == "bad":
            return None
        return GateDecision(
            self.mode != "reject",
            event.route,
            event.source_generation,
            event.trading_day,
            event.sequence,
            event.event_id,
        )


def coordinator(store, gate, *, initial="DECLARED_NO_PRIOR_PENDING", prepared_factory=None):
    from contextlib import nullcontext
    from unittest.mock import patch

    seam = (
        nullcontext()
        if prepared_factory is None
        else patch(
            "bt_api_ctp.simulation_calendar_ports.create_prepared_simulation_calendar_ports",
            prepared_factory,
        )
    )
    with seam:
        return SimulationCalendarEpochCoordinator(
            mode="SIMULATION_ONLY",
            store=store,
            snapshot_factory=CalendarSnapshot,
            gate=gate,
            route="route",
            source_generation="generation",
            trading_day=FIRST,
            last_sequence=1,
            mappings=(MAPPING,),
            closure=CLOSURE,
            required_instruments=("sim.rb",),
            required_years=(("SHFE", 2026),),
            initial_pending=initial,
        )


def hours(epoch):
    return epoch.schedule.windows("sim.rb", START, END)[0].start_utc.hour


def test_atomic_real_store_old_epoch_and_new_ports_are_version_aligned(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    gate = Gate()
    coord = coordinator(store, gate)
    old = coord.current_epoch()
    assert (
        old.reason == "PROCESS_START_OR_RESTART"
        and old.initial_pending == "DECLARED_NO_PRIOR_PENDING"
    )
    assert old.prior_pending_authentication == old.source_authentication == "NOT_PROVEN"
    assert (
        old.pending_durability == "PROCESS_LOCAL_NOT_PERSISTED"
        and old.trade_authorization == "NONE"
    )
    assert old.expected_days.expected_trading_days("SHFE", 2026) == (DAY,)
    assert hours(old) == 1
    publish(store, "v2", make_data("10:00", "11:00"))
    result = coord.switch(EVENT)
    assert result.outcome == "COMMITTED"
    new = coord.current_epoch()
    assert (old.calendar_ref, new.calendar_ref) == ("v1", "v2")
    assert (hours(old), hours(new)) == (1, 2)
    assert (
        old.expected_days.expected_trading_days("SHFE", 2026)
        == new.expected_days.expected_trading_days("SHFE", 2026)
        == (DAY,)
    )
    assert new.version_sha256 == new.view.status().version_sha256 == result.candidate_version_sha256
    assert new.limits.required_instruments == ("sim.rb",)
    assert new.event == EVENT and new.trading_day == DAY and new.sequence == 2
    assert not hasattr(coord, "activate") and not hasattr(new, "binding")
    with pytest.raises(FrozenInstanceError):
        new.sequence = 3
    assert coord.switch(EVENT).outcome == "DUPLICATE"
    assert coord.switch(replace(EVENT, sequence=3)).outcome == "CONFLICT"
    assert coord.inspect(EVENT.event_id).event == EVENT
    assert len(gate.calls) == 1


def test_restart_defaults_block_and_declaration_does_not_prove_recovery(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    gate = Gate()
    coord = coordinator(store, gate, initial="PRIOR_PENDING_UNKNOWN")
    epoch = coord.current_epoch()
    assert (
        epoch.reason == "PROCESS_START_OR_RESTART"
        and epoch.initial_pending == "PRIOR_PENDING_UNKNOWN"
    )
    assert coord.switch(EVENT).reason == "PRIOR_PENDING_UNKNOWN" and not gate.calls
    # A new coordinator has no durable record and remains blocked by default.
    restarted = coordinator(store, gate, initial="PRIOR_PENDING_UNKNOWN")
    assert restarted.inspect(EVENT.event_id).outcome == "NOT_OBSERVED"
    assert restarted.switch(EVENT).outcome == "BLOCKED" and not gate.calls


@pytest.mark.parametrize("mode", ["throw", "bad"])
def test_uncertain_gate_blocks_replay_and_preserves_epoch(tmp_path, mode):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    gate = Gate(mode)
    coord = coordinator(store, gate)
    old = coord.current_epoch()
    result = coord.switch(EVENT)
    assert (result.outcome, result.reason) == ("PENDING_UNKNOWN", "GATE_UNKNOWN")
    assert coord.current_epoch() is old and coord.inspect(EVENT.event_id).event == EVENT
    assert coord.switch(EVENT).outcome == "PENDING_UNKNOWN"
    assert coord.switch(replace(EVENT, sequence=3)).outcome == "CONFLICT"
    assert coord.switch(replace(EVENT, event_id="another")).outcome == "PENDING_UNKNOWN"
    assert len(gate.calls) == 1
    assert "secret" not in repr(result) + repr(coord.inspect(EVENT.event_id))


def test_explicit_gate_rejection_releases_reservation(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    gate = Gate("reject")
    coord = coordinator(store, gate)
    old = coord.current_epoch()
    assert coord.switch(EVENT).outcome == "REJECTED"
    assert coord.current_epoch() is old
    assert coord.inspect(EVENT.event_id).outcome == "REJECTED"
    assert coord.switch(replace(EVENT, sequence=3)).outcome == "CONFLICT"
    assert len(gate.calls) == 1
    gate.mode = "accept"
    publish(store, "v2", make_data("10:00", "11:00"))
    assert coord.switch(EVENT).outcome == "COMMITTED" and len(gate.calls) == 2


@pytest.mark.parametrize("failure", ["missing", "corrupt", "prep", "year_gap", "scope_gap"])
def test_accepted_gate_then_failed_preparation_is_pending_and_old_epoch_remains(tmp_path, failure):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    gate = Gate()
    calls = []

    def prepared(**kwargs):
        calls.append(kwargs["closure"].version_sha256)
        if failure == "prep" and len(calls) > 1:
            raise RuntimeError("secret-prep-value")
        return create_prepared_simulation_calendar_ports(**kwargs)

    coord = coordinator(store, gate, prepared_factory=prepared)
    old = coord.current_epoch()
    if failure == "missing":
        (tmp_path / "CURRENT.json").unlink()
    elif failure == "corrupt":
        (tmp_path / "CURRENT.json").write_bytes(b"secret-invalid-current")
    elif failure == "year_gap":
        publish(store, "v2", make_data("10:00", "11:00", missing=date(2026, 3, 1)))
    elif failure == "scope_gap":
        data = make_data("10:00", "11:00")
        data["trading_hours"][0]["contracts"] = ["mock_other"]
        publish(store, "v2", data)
    else:
        publish(store, "v2", make_data("10:00", "11:00"))
    result = coord.switch(EVENT)
    assert (result.outcome, result.reason) == ("PENDING_UNKNOWN", "PREPARATION_UNAVAILABLE")
    assert coord.current_epoch() is old and hours(old) == 1
    assert coord.inspect(EVENT.event_id).event == EVENT
    assert coord.switch(EVENT).outcome == "PENDING_UNKNOWN" and len(gate.calls) == 1
    assert "secret" not in repr(result) + repr(coord.inspect(EVENT.event_id))
    if failure in {"prep", "year_gap", "scope_gap"}:
        assert len(calls) == 2 and calls[0] == old.version_sha256 and calls[1] != old.version_sha256
        assert coord.inspect(EVENT.event_id).candidate_version_sha256 == calls[1]


def test_readers_do_not_wait_for_gate_or_prepared_factory(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    entered_gate, release_gate, entered_prepare, release_prepare = (
        Event(),
        Event(),
        Event(),
        Event(),
    )

    class SlowGate(Gate):
        def verify(self, event, context):
            entered_gate.set()
            assert release_gate.wait(5)
            return super().verify(event, context)

    calls = []

    def slow_factory(**kwargs):
        calls.append(kwargs["closure"].version_sha256)
        if len(calls) > 1:
            entered_prepare.set()
            assert release_prepare.wait(5)
        return create_prepared_simulation_calendar_ports(**kwargs)

    gate = SlowGate()
    coord = coordinator(store, gate, prepared_factory=slow_factory)
    old = coord.current_epoch()
    publish(store, "v2", make_data("10:00", "11:00"))
    outcomes = []
    worker = Thread(target=lambda: outcomes.append(coord.switch(EVENT)))
    worker.start()
    try:
        assert entered_gate.wait(5)
        assert coord.current_epoch() is old and hours(old) == 1
        assert coord.switch(EVENT).outcome == "BUSY"
        assert coord.switch(replace(EVENT, event_id="other")).outcome == "BUSY"
        release_gate.set()
        assert entered_prepare.wait(5)
        assert coord.current_epoch() is old and hours(old) == 1
        assert coord.inspect(EVENT.event_id).outcome == "IN_FLIGHT"
    finally:
        release_gate.set()
        release_prepare.set()
        worker.join(5)
    assert not worker.is_alive() and outcomes[0].outcome == "COMMITTED"
    assert calls[0] == old.version_sha256 and calls[1] == coord.current_epoch().version_sha256
    assert calls[0] != calls[1] and len(gate.calls) == 1


def test_initial_preparation_failure_is_value_free(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    with pytest.raises(SimulationEpochUnavailableError) as info:
        coordinator(store, Gate())
    assert str(info.value) == "SIMULATION_INITIALIZATION_UNAVAILABLE"
    assert str(tmp_path) not in str(info.value)


def test_readers_do_not_wait_for_slow_store_load(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    entered, release = Event(), Event()

    class SlowStore:
        reads = 0

        def read_current(self):
            self.reads += 1
            if self.reads > 1:
                entered.set()
                assert release.wait(5)
            return store.read_current()

    slow = SlowStore()
    coord = coordinator(slow, Gate())
    old = coord.current_epoch()
    publish(store, "v2", make_data("10:00", "11:00"))
    outcomes = []
    worker = Thread(target=lambda: outcomes.append(coord.switch(EVENT)))
    worker.start()
    try:
        assert entered.wait(5)
        assert coord.current_epoch() is old and hours(old) == 1
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and outcomes[0].outcome == "COMMITTED"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("event_id", "wrong"),
        ("route", "wrong"),
        ("source_generation", "wrong"),
        ("trading_day", date(2026, 1, 3)),
        ("sequence", 3),
        ("accepted", 1),
    ],
)
def test_gate_must_bind_every_event_field_or_remain_pending(tmp_path, field, bad):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())

    class WrongGate(Gate):
        def verify(self, event, context):
            return replace(super().verify(event, context), **{field: bad})

    gate = WrongGate()
    coord = coordinator(store, gate)
    old = coord.current_epoch()
    assert coord.switch(EVENT).outcome == "PENDING_UNKNOWN"
    assert coord.current_epoch() is old
    assert coord.inspect(EVENT.event_id).event == EVENT
    assert coord.switch(EVENT).outcome == "PENDING_UNKNOWN" and len(gate.calls) == 1


def test_gate_mutation_does_not_change_recorded_event(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())

    class MutatingGate(Gate):
        def verify(self, event, context):
            object.__setattr__(event, "sequence", 99)
            return super().verify(event, context)

    gate = MutatingGate()
    coord = coordinator(store, gate)
    assert coord.switch(EVENT).outcome == "PENDING_UNKNOWN"
    assert EVENT.sequence == coord.inspect(EVENT.event_id).event.sequence == 2


def test_real_store_41_scopes_and_twelve_years_are_eagerly_prepared(tmp_path):
    # Synthetic data: 41 instruments each have one open owner day, not a
    # 41-instrument real or full-year trading-session baseline.
    venues = ("SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX")
    last = date(2027, 12, 31)
    days = [FIRST + timedelta(days=offset) for offset in range((last - FIRST).days + 1)]
    source = ["MOCK.simulation_only"]
    mappings = tuple(
        SimulationMapping(
            f"sim.{i}", venues[i % 6], "future" if i % 2 == 0 else "option", f"p{i}", f"mock_{i}"
        )
        for i in range(41)
    )
    groups = []
    for venue in venues:
        rules = []
        for mapping in mappings:
            if mapping.venue != venue:
                continue
            rules.append(
                {
                    "venue": venue,
                    "kind": mapping.kind,
                    "product": mapping.product,
                    "contracts": [mapping.mock_contract],
                    "valid_from": FIRST.isoformat(),
                    "valid_to": last.isoformat(),
                    "day_windows": [
                        {
                            "id": "day",
                            "phase": "continuous",
                            "start": "09:00",
                            "end": "10:00",
                            "end_day_offset": 0,
                        }
                    ],
                    "night_windows": [],
                    "night_mappings": [
                        {"trading_day": DAY.isoformat(), "start_date": None, "source_ids": source}
                    ],
                    "windows_complete": True,
                    "exceptions_complete": True,
                    "source_ids": source,
                }
            )
        data = {
            "sources": [{"id": source[0], "description": "fictional"}],
            "trading_days": [
                {
                    "venue": venue,
                    "day": d.isoformat(),
                    "status": "open" if d == DAY else "closed",
                    "source_ids": source,
                }
                for d in days
            ],
            "trading_hours": rules,
            "exceptions": [],
        }
        groups.append(VenueContent.from_layers(venue, FIRST, last, data))
    bundle = compose_simulated(
        mode="SIMULATION_ONLY", venues=tuple(groups), snapshot_factory=CalendarSnapshot
    )
    assert bundle.status == "READY_SIMULATED", bundle
    store = FixtureCalendarStore(tmp_path)
    published = store.publish(
        json.loads(bundle.snapshot_bytes),
        publication_id="pub-many",
        version_id="many-v1",
        coverage_manifest_bytes=bundle.manifest_bytes,
        coverage_manifest_sha256=bundle.manifest_sha256,
        binding_bytes=bundle.binding_bytes,
        binding_sha256=bundle.binding_sha256,
    )
    assert published.visibility == "COMMITTED"
    years = tuple((venue, year) for venue in venues for year in (2026, 2027))
    coord = SimulationCalendarEpochCoordinator(
        mode="SIMULATION_ONLY",
        store=store,
        snapshot_factory=CalendarSnapshot,
        gate=Gate(),
        route="route",
        source_generation="generation",
        trading_day=FIRST,
        last_sequence=1,
        mappings=mappings,
        closure=CLOSURE,
        required_instruments=tuple(m.instrument_id for m in mappings),
        required_years=years,
        initial_pending="DECLARED_NO_PRIOR_PENDING",
    )
    epoch = coord.current_epoch()
    assert epoch.limits.cached_windows == 41 and len(epoch.limits.required_years) == 12
    for mapping in mappings:
        assert len(epoch.schedule.windows(mapping.instrument_id, START, END)) == 1
    for venue, year in years:
        assert epoch.expected_days.expected_trading_days(venue, year) == (
            (DAY,) if year == 2026 else ()
        )


def test_invalid_or_wrong_scope_events_do_not_call_gate(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    gate = Gate()
    coord = coordinator(store, gate)
    invalid = [None, replace(EVENT, event_id="bad/secret"), replace(EVENT, sequence=True)]
    for item in invalid:
        assert coord.switch(item).outcome == "INVALID"
    rejected = [
        replace(EVENT, route="wrong"),
        replace(EVENT, source_generation="other"),
        replace(EVENT, trading_day=FIRST),
        replace(EVENT, sequence=1),
    ]
    for item in rejected:
        assert coord.switch(item).outcome == "REJECTED"
    assert gate.calls == [] and coord.current_epoch().calendar_ref == "v1"


@pytest.mark.parametrize("point", ["gate", "store", "prepared"])
@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
def test_interrupt_after_reservation_is_inspectable_unknown(tmp_path, point, exception_type):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())

    class InterruptGate(Gate):
        def verify(self, event, context):
            if point == "gate":
                self.calls.append((event, context))
                raise exception_type()
            return super().verify(event, context)

    class InterruptStore:
        calls = 0

        def read_current(self):
            self.calls += 1
            if point == "store" and self.calls > 1:
                raise exception_type()
            return store.read_current()

    factory_calls = []

    def prepared(**kwargs):
        factory_calls.append(1)
        if point == "prepared" and len(factory_calls) > 1:
            raise exception_type()
        return create_prepared_simulation_calendar_ports(**kwargs)

    gate = InterruptGate()
    coord = coordinator(InterruptStore(), gate, prepared_factory=prepared)
    old = coord.current_epoch()
    publish(store, "v2", make_data("10:00", "11:00"))
    with pytest.raises(exception_type):
        coord.switch(EVENT)
    assert coord.current_epoch() is old
    assert coord.inspect(EVENT.event_id).outcome == "PENDING_UNKNOWN"
    assert coord.inspect(EVENT.event_id).event == EVENT
    assert coord.switch(EVENT).outcome == "PENDING_UNKNOWN"
    assert len(gate.calls) == 1


@pytest.mark.parametrize("when", ["before", "after"])
def test_trace_interrupt_at_atomic_commit_is_consistent(tmp_path, when):
    import inspect
    import sys

    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    gate = Gate()
    coord = coordinator(store, gate)
    old = coord.current_epoch()
    publish(store, "v2", make_data("10:00", "11:00"))
    lines, start_line = inspect.getsourcelines(SimulationCalendarEpochCoordinator.switch)
    marker = next(
        i
        for i, line in enumerate(lines)
        if 'records=_record(state.records, event, requested_plan, "COMMITTED")' in line
    )
    assign = next(
        i for i in range(marker + 1, len(lines)) if "self.__state = next_state" in lines[i]
    )
    after = next(
        i for i in range(assign + 1, len(lines)) if "return SimulationEpochOutcome(" in lines[i]
    )
    target_line = start_line + (assign if when == "before" else after)

    def trace(frame, event, arg):
        if (
            frame.f_code is SimulationCalendarEpochCoordinator.switch.__code__
            and event == "line"
            and frame.f_lineno == target_line
        ):
            raise KeyboardInterrupt()
        return trace

    sys.settrace(trace)
    try:
        with pytest.raises(KeyboardInterrupt):
            coord.switch(EVENT)
    finally:
        sys.settrace(None)
    observed = coord.inspect(EVENT.event_id)
    if when == "before":
        assert coord.current_epoch() is old and observed.outcome == "PENDING_UNKNOWN"
        assert coord.switch(EVENT).outcome == "PENDING_UNKNOWN"
    else:
        assert coord.current_epoch().calendar_ref == "v2" and observed.outcome == "COMMITTED"
        assert coord.switch(EVENT).outcome == "DUPLICATE"
    assert len(gate.calls) == 1


def test_event_day_scope_bounds_rejected_before_gate(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    gate = Gate()
    closure = SimulationEpochClosure(START, END, DAY, date(2026, 1, 3), 0, 0)
    coord = SimulationCalendarEpochCoordinator(
        mode="SIMULATION_ONLY",
        store=store,
        snapshot_factory=CalendarSnapshot,
        gate=gate,
        route="route",
        source_generation="generation",
        trading_day=FIRST,
        last_sequence=1,
        mappings=(MAPPING,),
        closure=closure,
        required_instruments=("sim.rb",),
        required_years=(("SHFE", 2026),),
        initial_pending="DECLARED_NO_PRIOR_PENDING",
    )
    for outside in (date(2026, 1, 4), date(2027, 1, 1)):
        assert (
            coord.switch(replace(EVENT, trading_day=outside)).reason
            == "EVENT_OUTSIDE_PREPARED_SCOPE"
        )
    assert gate.calls == []
    assert coord.current_epoch().scope_rollover == "CALLER_DECLARED_PLAN_SWITCH_ONLY"
    # Upper bound is allowed by the declared closure; the day remains a simulation declaration.
    assert coord.switch(replace(EVENT, trading_day=closure.owner_end)).outcome == "COMMITTED"
    assert len(gate.calls) == 1


def test_reentrant_gate_observes_busy_without_deadlock_or_second_verify(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())

    class ReentrantGate(Gate):
        coord = None

        def verify(self, event, context):
            assert self.coord.current_epoch().calendar_ref == "v1"
            assert self.coord.switch(event).outcome == "BUSY"
            return super().verify(event, context)

    gate = ReentrantGate()
    coord = coordinator(store, gate)
    gate.coord = coord
    assert coord.switch(EVENT).outcome == "COMMITTED"
    assert len(gate.calls) == 1


def test_public_constructor_rejects_arbitrary_prepared_factory(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    with pytest.raises(TypeError, match="prepared_factory"):
        SimulationCalendarEpochCoordinator(
            mode="SIMULATION_ONLY",
            store=store,
            snapshot_factory=CalendarSnapshot,
            gate=Gate(),
            route="route",
            source_generation="generation",
            trading_day=FIRST,
            last_sequence=1,
            mappings=(MAPPING,),
            closure=CLOSURE,
            required_instruments=("sim.rb",),
            required_years=(("SHFE", 2026),),
            prepared_factory=lambda **kwargs: None,
        )


def publish_two_simulated_years(store, version, first_year, open_day, window_start, window_end):
    first, last = date(first_year, 1, 1), date(first_year + 1, 12, 31)
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    source = ["MOCK.simulation_only"]
    data = {
        "sources": [{"id": source[0], "description": "fictional"}],
        "trading_days": [
            {
                "venue": "SHFE",
                "day": day.isoformat(),
                "status": "open" if day == open_day else "closed",
                "source_ids": source,
            }
            for day in days
        ],
        "trading_hours": [
            {
                "venue": "SHFE",
                "kind": "future",
                "product": "pfuture",
                "contracts": ["mock_shfe_future_contract"],
                "valid_from": first.isoformat(),
                "valid_to": last.isoformat(),
                "day_windows": [
                    {
                        "id": "day",
                        "phase": "continuous",
                        "start": window_start,
                        "end": window_end,
                        "end_day_offset": 0,
                    }
                ],
                "night_windows": [],
                "night_mappings": [
                    {"trading_day": open_day.isoformat(), "start_date": None, "source_ids": source}
                ],
                "windows_complete": True,
                "exceptions_complete": True,
                "source_ids": source,
            }
        ],
        "exceptions": [],
    }
    group = VenueContent.from_layers("SHFE", first, last, data)
    bundle = compose_simulated(
        mode="SIMULATION_ONLY", venues=(group,), snapshot_factory=CalendarSnapshot
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


def test_real_simulation_plan_switch_crosses_year_and_keeps_history(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    old_day, new_day = date(2026, 12, 31), date(2027, 1, 1)
    publish_two_simulated_years(store, "v1", 2026, old_day, "09:00", "10:00")
    old_start, new_start = (
        datetime(2026, 12, 31, 0, tzinfo=UTC),
        datetime(2027, 1, 1, 0, tzinfo=UTC),
    )
    gate = Gate()
    coord = SimulationCalendarEpochCoordinator(
        mode="SIMULATION_ONLY",
        store=store,
        snapshot_factory=CalendarSnapshot,
        gate=gate,
        route="route",
        source_generation="generation",
        trading_day=date(2026, 12, 30),
        last_sequence=1,
        mappings=(MAPPING,),
        closure=SimulationEpochClosure(
            old_start, old_start + timedelta(hours=4), old_day, old_day, 0, 0
        ),
        required_instruments=("sim.rb",),
        required_years=(("SHFE", 2026), ("SHFE", 2027)),
        initial_pending="DECLARED_NO_PRIOR_PENDING",
    )
    event1 = SimulationEpochEvent("old-day", "route", "generation", old_day, 2)
    assert coord.switch(event1).outcome == "COMMITTED"
    old = coord.current_epoch()
    publish_two_simulated_years(store, "v2", 2027, new_day, "10:00", "11:00")
    new_plan = SimulationEpochPlan(
        (MAPPING,),
        SimulationEpochClosure(new_start, new_start + timedelta(hours=4), new_day, new_day, 0, 0),
        ("sim.rb",),
        (("SHFE", 2027), ("SHFE", 2028)),
    )
    event2 = SimulationEpochEvent("new-year", "route", "generation", new_day, 3)
    assert coord.switch(event2, plan=new_plan).outcome == "COMMITTED"
    current = coord.current_epoch()
    assert (old.calendar_ref, current.calendar_ref) == ("v1", "v2")
    assert old.plan.required_years == (("SHFE", 2026), ("SHFE", 2027))
    assert (
        current.plan == new_plan
        and current.plan_binding == "LOCAL_EVENT_PLAN_DECLARATION_GATE_NOT_AUTHENTICATED"
    )
    assert (
        old.schedule.windows("sim.rb", old_start, old_start + timedelta(hours=4))[0].start_utc.hour
        == 1
    )
    assert (
        current.schedule.windows("sim.rb", new_start, new_start + timedelta(hours=4))[
            0
        ].start_utc.hour
        == 2
    )
    assert old.expected_days.expected_trading_days("SHFE", 2026) == (old_day,)
    assert current.expected_days.expected_trading_days("SHFE", 2027) == (new_day,)
    assert current.expected_days.expected_trading_days("SHFE", 2028) == ()
    assert coord.switch(event1).outcome == "DUPLICATE"
    assert coord.switch(event1, plan=new_plan).outcome == "CONFLICT"
    assert coord.switch(event1, plan=old.plan).outcome == "DUPLICATE"
    assert coord.switch(event2).outcome == "DUPLICATE"
    assert coord.inspect(event1.event_id).plan == old.plan
    assert len(gate.calls) == 2


@pytest.mark.parametrize(
    "gate_mode,first_outcome",
    [("accept", "COMMITTED"), ("reject", "REJECTED"), ("throw", "PENDING_UNKNOWN")],
)
def test_event_id_is_bound_to_plan_in_all_outcomes(tmp_path, gate_mode, first_outcome):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    gate = Gate(gate_mode)
    coord = coordinator(store, gate)
    initial_plan = coord.current_epoch().plan
    different = replace(initial_plan, max_cached_windows=initial_plan.max_cached_windows - 1)
    assert coord.switch(EVENT, plan=initial_plan).outcome == first_outcome
    assert coord.inspect(EVENT.event_id).plan == initial_plan
    assert coord.switch(EVENT, plan=different).outcome == "CONFLICT"
    assert coord.switch(EVENT, plan=replace(initial_plan, required_years=())).outcome == "CONFLICT"
    if first_outcome == "COMMITTED":
        assert coord.switch(EVENT).outcome == "DUPLICATE"
    elif first_outcome == "PENDING_UNKNOWN":
        assert coord.switch(EVENT).outcome == "PENDING_UNKNOWN"
    else:
        assert coord.switch(EVENT).outcome == "REJECTED"
    assert len(gate.calls) == (2 if first_outcome == "REJECTED" else 1)


@pytest.mark.parametrize(
    "case",
    ["missing_year", "budget", "bad_horizon", "missing_instrument", "bad_mapping", "duplicate"],
)
def test_invalid_new_plan_never_reaches_gate(tmp_path, case):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    gate = Gate()
    coord = coordinator(store, gate)
    initial = coord.current_epoch().plan
    if case == "missing_year":
        invalid = replace(initial, required_years=())
    elif case == "budget":
        invalid = replace(initial, max_cached_windows=True)
    elif case == "bad_horizon":
        invalid = replace(initial, closure=replace(initial.closure, owner_start=date(2026, 1, 3)))
    elif case == "missing_instrument":
        invalid = replace(initial, required_instruments=("missing",))
    elif case == "bad_mapping":
        invalid = replace(initial, mappings=(replace(MAPPING, mock_contract="real_contract"),))
    else:
        invalid = replace(initial, required_instruments=("sim.rb", "sim.rb"))
    assert coord.switch(EVENT, plan=invalid).outcome == "INVALID"
    assert coord.inspect(EVENT.event_id).outcome == "NOT_OBSERVED"
    assert not gate.calls


def test_caller_plan_mutation_while_gate_waits_does_not_change_snapshot(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    entered, release = Event(), Event()

    class SlowGate(Gate):
        def verify(self, event, context):
            entered.set()
            assert release.wait(5)
            return super().verify(event, context)

    gate = SlowGate()
    coord = coordinator(store, gate)
    old = coord.current_epoch()
    copied_mapping = replace(MAPPING)
    proposed = replace(old.plan, mappings=(copied_mapping,), max_cached_windows=20000)
    outcomes = []
    worker = Thread(target=lambda: outcomes.append(coord.switch(EVENT, plan=proposed)))
    worker.start()
    try:
        assert entered.wait(5)
        object.__setattr__(proposed, "max_cached_windows", 1)
        object.__setattr__(copied_mapping, "mock_contract", "mock_tampered")
        assert coord.current_epoch() is old
        pending = coord.inspect(EVENT.event_id)
        assert pending.plan.max_cached_windows == 20000
        assert pending.plan.mappings[0].mock_contract == "mock_shfe_future_contract"
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and outcomes[0].outcome == "COMMITTED"
    assert coord.current_epoch().plan.max_cached_windows == 20000
    assert coord.current_epoch().plan.mappings[0].mock_contract == "mock_shfe_future_contract"


def test_event_history_bound_never_discards_old_ids(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    gate = Gate("reject")
    coord = coordinator(store, gate)
    assert coord.current_epoch().event_history_limit == 1024
    for index in range(1024):
        assert coord.switch(replace(EVENT, event_id=f"event-{index}")).outcome == "REJECTED"
    assert len(gate.calls) == 1024
    assert coord.switch(replace(EVENT, event_id="overflow")).reason == "EVENT_HISTORY_LIMIT"
    assert len(gate.calls) == 1024
    first = replace(EVENT, event_id="event-0")
    assert coord.inspect(first.event_id).outcome == "REJECTED"
    assert coord.switch(replace(first, sequence=3)).outcome == "CONFLICT"
    assert len(gate.calls) == 1024


def test_accepted_new_plan_with_missing_scope_remains_pending(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    gate = Gate()
    coord = coordinator(store, gate)
    old = coord.current_epoch()
    missing = replace(old.plan, mappings=(replace(MAPPING, mock_contract="mock_missing"),))
    assert coord.switch(EVENT, plan=missing).outcome == "PENDING_UNKNOWN"
    pending = coord.inspect(EVENT.event_id)
    assert pending.plan == missing and pending.event == EVENT
    assert coord.current_epoch() is old
    assert coord.switch(EVENT, plan=old.plan).outcome == "CONFLICT"
    assert coord.switch(EVENT).outcome == "PENDING_UNKNOWN" and len(gate.calls) == 1


@pytest.mark.parametrize("when", ["before", "after"])
def test_plan_and_epoch_remain_atomic_at_commit_interrupt(tmp_path, when):
    import inspect
    import sys

    store = FixtureCalendarStore(tmp_path)
    publish(store, "v1", make_data())
    gate = Gate()
    coord = coordinator(store, gate)
    old = coord.current_epoch()
    proposed = replace(old.plan, max_cached_windows=20000)
    lines, start_line = inspect.getsourcelines(SimulationCalendarEpochCoordinator.switch)
    marker = next(
        i
        for i, line in enumerate(lines)
        if 'records=_record(state.records, event, requested_plan, "COMMITTED")' in line
    )
    assign = next(
        i for i in range(marker + 1, len(lines)) if "self.__state = next_state" in lines[i]
    )
    after = next(
        i for i in range(assign + 1, len(lines)) if "return SimulationEpochOutcome(" in lines[i]
    )
    target = start_line + (assign if when == "before" else after)

    def trace(frame, event, arg):
        if (
            frame.f_code is SimulationCalendarEpochCoordinator.switch.__code__
            and event == "line"
            and frame.f_lineno == target
        ):
            raise KeyboardInterrupt()
        return trace

    sys.settrace(trace)
    try:
        with pytest.raises(KeyboardInterrupt):
            coord.switch(EVENT, plan=proposed)
    finally:
        sys.settrace(None)
    item = coord.inspect(EVENT.event_id)
    assert item.plan == proposed
    if when == "before":
        assert coord.current_epoch() is old and item.outcome == "PENDING_UNKNOWN"
    else:
        assert coord.current_epoch().plan == proposed and item.outcome == "COMMITTED"
    assert len(gate.calls) == 1
