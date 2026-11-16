"""Atomic, process-local simulation epochs for a fixed pair of CTP-shaped ports.

No source, identity, clock, owner closure or trading authority is authenticated.
A pending event is not durable and no restart reconciliation is implemented.
The caller-injected gate and provider snapshot factory, and the fixed CTP
prepared factory, are trust dependencies. In-process monkeypatching is not a
security boundary. No production route, broker or gateway is configured here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

from bt_api_py.trading_calendar_binding import (
    BindingStatus,
    GateContext,
    GateDecision,
    SimulationCalendarReadView,
    _load_simulation_snapshot,
    _validated_snapshot_origin,
)

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_MAX_EVENTS = 1024
_VENUES = frozenset({"SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX"})
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _identifier(value: Any) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def _utc(value: Any) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("SIMULATION_INPUT_UNAVAILABLE")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class SimulationEpochEvent:
    event_id: str
    route: str
    source_generation: str
    trading_day: date
    sequence: int


@dataclass(frozen=True, slots=True)
class SimulationEpochClosure:
    start_utc: datetime
    end_utc: datetime
    owner_start: date
    owner_end: date
    max_advance_days: int
    max_spill_days: int


@dataclass(frozen=True, slots=True)
class SimulationEpochPlan:
    """Caller-declared simulated scope; not proof of facts or authority."""

    mappings: tuple[Any, ...]
    closure: SimulationEpochClosure
    required_instruments: tuple[str, ...]
    required_years: tuple[tuple[str, int], ...]
    max_cached_windows: int = 131072


@dataclass(frozen=True, slots=True)
class SimulationCalendarEpoch:
    view: SimulationCalendarReadView
    expected_days: Any
    schedule: Any
    calendar_ref: str
    version_sha256: str
    trading_day: date
    sequence: int
    event: SimulationEpochEvent | None
    plan: SimulationEpochPlan
    reason: str
    initial_pending: str
    limits: Any
    mode: str = "SIMULATION_ONLY"
    source_authentication: str = "NOT_PROVEN"
    identity_binding: str = "CALLER_DECLARED_NOT_PROVEN"
    owner_closure: str = "CALLER_DECLARED_NOT_PROVEN"
    prior_pending_authentication: str = "NOT_PROVEN"
    trade_authorization: str = "NONE"
    pending_durability: str = "PROCESS_LOCAL_NOT_PERSISTED"
    scope_rollover: str = "CALLER_DECLARED_PLAN_SWITCH_ONLY"
    plan_binding: str = "LOCAL_EVENT_PLAN_DECLARATION_GATE_NOT_AUTHENTICATED"
    event_history_limit: int = _MAX_EVENTS


@dataclass(frozen=True, slots=True)
class SimulationEpochOutcome:
    outcome: str
    reason: str
    event_id: str | None
    candidate_version_sha256: str | None = None
    event: SimulationEpochEvent | None = None
    plan: SimulationEpochPlan | None = None


@dataclass(frozen=True, slots=True)
class _Reservation:
    event: SimulationEpochEvent
    plan: SimulationEpochPlan
    revision: int
    stage: str
    candidate_version_sha256: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _EventRecord:
    event: SimulationEpochEvent
    plan: SimulationEpochPlan
    outcome: str


@dataclass(frozen=True, slots=True)
class _CoordinatorState:
    epoch: SimulationCalendarEpoch
    revision: int
    reservation: _Reservation | None
    records: tuple[_EventRecord, ...]


def _record(
    records: tuple[_EventRecord, ...],
    event: SimulationEpochEvent,
    plan: SimulationEpochPlan,
    outcome: str,
):
    return tuple(item for item in records if item.event.event_id != event.event_id) + (
        _EventRecord(event, plan, outcome),
    )


class SimulationEpochUnavailableError(RuntimeError):
    """Finite, value-free simulation setup failure."""


def _copy_event(event: Any) -> SimulationEpochEvent:
    if (
        type(event) is not SimulationEpochEvent
        or not _identifier(event.event_id)
        or not _identifier(event.route)
        or not _identifier(event.source_generation)
        or type(event.trading_day) is not date
        or type(event.sequence) is not int
        or event.sequence < 0
    ):
        raise ValueError("SIMULATION_EVENT_INVALID")
    return SimulationEpochEvent(
        event.event_id, event.route, event.source_generation, event.trading_day, event.sequence
    )


def _freeze_plan(plan: Any, mapping_type: Any) -> SimulationEpochPlan:
    """Copy and validate the entire bounded declaration before gate use."""
    if type(plan) is not SimulationEpochPlan or type(plan.closure) is not SimulationEpochClosure:
        raise ValueError("SIMULATION_PLAN_INVALID")
    mappings, instruments, years, closure = (
        plan.mappings,
        plan.required_instruments,
        plan.required_years,
        plan.closure,
    )
    if (
        type(mappings) is not tuple
        or not 1 <= len(mappings) <= 128
        or any(type(m) is not mapping_type for m in mappings)
        or type(instruments) is not tuple
        or not 1 <= len(instruments) <= 128
        or any(not _identifier(item) for item in instruments)
        or len(set(instruments)) != len(instruments)
        or type(years) is not tuple
        or not 1 <= len(years) <= 12
        or any(
            type(row) is not tuple
            or len(row) != 2
            or type(row[0]) is not str
            or row[0] not in _VENUES
            or type(row[1]) is not int
            or not 1 <= row[1] <= 9999
            for row in years
        )
        or len(set(years)) != len(years)
        or type(plan.max_cached_windows) is not int
        or not 1 <= plan.max_cached_windows <= 262144
        or type(closure.owner_start) is not date
        or type(closure.owner_end) is not date
        or not closure.owner_start <= closure.owner_end
        or (closure.owner_end - closure.owner_start).days + 1 > 800
        or type(closure.max_advance_days) is not int
        or not 0 <= closure.max_advance_days <= 7
        or type(closure.max_spill_days) is not int
        or not 0 <= closure.max_spill_days <= 1
        or closure.owner_start < date.min + timedelta(days=closure.max_advance_days)
        or closure.owner_end > date.max - timedelta(days=closure.max_spill_days + 1)
    ):
        raise ValueError("SIMULATION_PLAN_INVALID")
    copied = []
    for m in mappings:
        if (
            not _identifier(m.instrument_id)
            or type(m.venue) is not str
            or m.venue not in _VENUES
            or type(m.kind) is not str
            or m.kind not in {"future", "option"}
            or not _identifier(m.product)
            or not _identifier(m.mock_contract)
            or not m.mock_contract.startswith("mock_")
        ):
            raise ValueError("SIMULATION_PLAN_INVALID")
        copied.append(mapping_type(m.instrument_id, m.venue, m.kind, m.product, m.mock_contract))
    by_name = {m.instrument_id: m for m in copied}
    if len(by_name) != len(copied) or any(name not in by_name for name in instruments):
        raise ValueError("SIMULATION_PLAN_INVALID")
    needed = {
        (by_name[name].venue, year)
        for name in instruments
        for year in range(closure.owner_start.year, closure.owner_end.year + 1)
    }
    if not needed <= set(years):
        raise ValueError("SIMULATION_PLAN_INVALID")
    start, end = _utc(closure.start_utc), _utc(closure.end_utc)
    if start >= end:
        raise ValueError("SIMULATION_PLAN_INVALID")
    earliest = start.astimezone(_SHANGHAI).date() - timedelta(days=closure.max_spill_days)
    latest = (end - timedelta(microseconds=1)).astimezone(_SHANGHAI).date() + timedelta(
        days=closure.max_advance_days
    )
    if not closure.owner_start <= earliest <= latest <= closure.owner_end:
        raise ValueError("SIMULATION_PLAN_INVALID")
    frozen_closure = SimulationEpochClosure(
        start,
        end,
        closure.owner_start,
        closure.owner_end,
        closure.max_advance_days,
        closure.max_spill_days,
    )
    return SimulationEpochPlan(
        tuple(copied),
        frozen_closure,
        tuple(instruments),
        tuple((venue, year) for venue, year in years),
        plan.max_cached_windows,
    )


class SimulationCalendarEpochCoordinator:
    """One simulation-only owner of a process-local immutable epoch pointer.

    The default restart state blocks gate calls. A caller can explicitly declare
    no prior pending event; that declaration is unauthenticated and is not crash
    recovery. Accepted-but-uncommitted or uncertain events cannot be retried or
    cleared here. Readers keep the previous epoch during slow preparation.
    Scope can change only by an explicit caller-declared plan on a gated switch;
    automatic plan rollover and durable event deduplication are not implemented.
    At most 1024 process-local event IDs are retained; none are discarded.
    """

    def __init__(
        self,
        *,
        mode: str,
        store: Any,
        snapshot_factory: Any,
        gate: Any,
        route: str,
        source_generation: str,
        trading_day: date,
        last_sequence: int,
        mappings: tuple[Any, ...],
        closure: SimulationEpochClosure,
        required_instruments: tuple[str, ...],
        required_years: tuple[tuple[str, int], ...],
        max_cached_windows: int = 131072,
        initial_pending: str = "PRIOR_PENDING_UNKNOWN",
    ):
        try:
            # Deliberately lazy and limited to this explicitly selected adapter.
            from bt_api_ctp.simulation_calendar_ports import (
                SimulationMapping,
                SimulationOwnerClosure,
                SimulationPreparedCalendarPorts,
                create_prepared_simulation_calendar_ports,
            )

            if (
                type(mode) is not str
                or mode != "SIMULATION_ONLY"
                or not _identifier(route)
                or not _identifier(source_generation)
                or type(trading_day) is not date
                or type(last_sequence) is not int
                or last_sequence < 0
                or initial_pending not in {"PRIOR_PENDING_UNKNOWN", "DECLARED_NO_PRIOR_PENDING"}
                or type(initial_pending) is not str
                or not callable(getattr(store, "read_current", None))
                or not callable(snapshot_factory)
                or not callable(getattr(gate, "verify", None))
                or type(mappings) is not tuple
                or not 1 <= len(mappings) <= 128
                or any(type(m) is not SimulationMapping for m in mappings)
                or type(closure) is not SimulationEpochClosure
                or type(required_instruments) is not tuple
                or type(required_years) is not tuple
                or type(max_cached_windows) is not int
                or not 1 <= max_cached_windows <= 262144
            ):
                raise ValueError("SIMULATION_INPUT_UNAVAILABLE")
            # CTP factory validates all scope fields; exact primitive copies make
            # the declaration independent of subsequent caller references.
            if any(
                any(
                    type(getattr(m, key)) is not str
                    for key in ("instrument_id", "venue", "kind", "product", "mock_contract")
                )
                for m in mappings
            ):
                raise ValueError("SIMULATION_INPUT_UNAVAILABLE")
            frozen_mappings = tuple(
                SimulationMapping(m.instrument_id, m.venue, m.kind, m.product, m.mock_contract)
                for m in mappings
            )
            if (
                any(not _identifier(item) for item in required_instruments)
                or any(
                    type(item) is not tuple
                    or len(item) != 2
                    or type(item[0]) is not str
                    or type(item[1]) is not int
                    for item in required_years
                )
                or type(closure.owner_start) is not date
                or type(closure.owner_end) is not date
                or type(closure.max_advance_days) is not int
                or type(closure.max_spill_days) is not int
            ):
                raise ValueError("SIMULATION_INPUT_UNAVAILABLE")
            frozen_closure = SimulationEpochClosure(
                _utc(closure.start_utc),
                _utc(closure.end_utc),
                closure.owner_start,
                closure.owner_end,
                closure.max_advance_days,
                closure.max_spill_days,
            )
            self.__store, self.__snapshot_factory, self.__gate = store, snapshot_factory, gate
            self.__prepared_factory = create_prepared_simulation_calendar_ports
            self.__prepared_type = SimulationPreparedCalendarPorts
            self.__closure_type = SimulationOwnerClosure
            self.__mapping_type = SimulationMapping
            initial_plan = _freeze_plan(
                SimulationEpochPlan(
                    frozen_mappings,
                    frozen_closure,
                    required_instruments,
                    required_years,
                    max_cached_windows,
                ),
                SimulationMapping,
            )
            self.__initial_pending = initial_pending
            self.__lock = Lock()
            epoch = self.__prepare(
                route,
                source_generation,
                trading_day,
                last_sequence,
                None,
                "PROCESS_START_OR_RESTART",
                initial_plan,
            )
            self.__state = _CoordinatorState(epoch, 0, None, ())
        except Exception:
            raise SimulationEpochUnavailableError("SIMULATION_INITIALIZATION_UNAVAILABLE") from None

    def __prepare(self, route, generation, day, sequence, event, reason, plan, on_loaded=None):
        snapshot, source, coverage, provenance, content = _load_simulation_snapshot(
            self.__store, self.__snapshot_factory
        )
        if on_loaded is not None:
            on_loaded(source.version_sha256)
        status = BindingStatus(
            mode="SIMULATION_ONLY",
            route=route,
            source_generation=generation,
            bound_trading_day=day,
            last_sequence=sequence,
            calendar_ref=source.version_id,
            publication_id=source.publication_id,
            content_sha256=source.content_sha256,
            coverage_status=source.coverage_status,
            coverage_manifest_sha256=source.coverage_manifest_sha256,
            binding_sha256=source.binding_sha256,
            version_sha256=source.version_sha256,
            declared_coverage=coverage,
            binding_reason=reason,
            load_error=None,
            data_origin=_validated_snapshot_origin(source, provenance),
        )
        view = SimulationCalendarReadView(snapshot, status, provenance, content)
        c = plan.closure
        closure = self.__closure_type(
            source.version_sha256,
            c.start_utc,
            c.end_utc,
            c.owner_start,
            c.owner_end,
            c.max_advance_days,
            c.max_spill_days,
        )
        ports = self.__prepared_factory(
            mode="SIMULATION_ONLY",
            view=view,
            mappings=plan.mappings,
            closure=closure,
            required_instruments=plan.required_instruments,
            required_years=plan.required_years,
            max_cached_windows=plan.max_cached_windows,
        )
        if type(ports) is not self.__prepared_type:
            raise ValueError("SIMULATION_PREPARED_UNAVAILABLE")
        identity = ports.identity
        by_name = {item.instrument_id: item for item in plan.mappings}
        expected_mappings = tuple(by_name[name] for name in plan.required_instruments)
        names = (
            "mode",
            "route",
            "source_generation",
            "calendar_ref",
            "publication_id",
            "content_sha256",
            "coverage_manifest_sha256",
            "binding_sha256",
            "version_sha256",
            "bound_trading_day",
            "last_sequence",
            "data_origin",
        )
        if (
            any(getattr(identity, key, None) != getattr(status, key) for key in names)
            or identity.declared_coverage
            != tuple((x.venue, x.start_day, x.end_day) for x in coverage)
            or ports.closure != closure
            or ports.limits.required_instruments != plan.required_instruments
            or ports.limits.required_years != plan.required_years
            or ports.limits.max_cached_windows != plan.max_cached_windows
            or ports.mappings != expected_mappings
            or identity.source_authentication != "NOT_PROVEN"
            or identity.identity_binding != "CALLER_DECLARED_NOT_PROVEN"
            or identity.owner_closure != "CALLER_DECLARED_NOT_PROVEN"
            or identity.trade_authorization != "NONE"
            or not callable(getattr(ports.expected_days, "expected_trading_days", None))
            or not callable(getattr(ports.schedule, "windows", None))
            or not callable(getattr(ports.schedule, "session_state", None))
        ):
            raise ValueError("SIMULATION_PREPARED_UNAVAILABLE")
        return SimulationCalendarEpoch(
            view,
            ports.expected_days,
            ports.schedule,
            source.version_id,
            source.version_sha256,
            day,
            sequence,
            event,
            plan,
            reason,
            self.__initial_pending,
            ports.limits,
        )

    def current_epoch(self) -> SimulationCalendarEpoch:
        with self.__lock:
            return self.__state.epoch

    def inspect(self, event_id: str) -> SimulationEpochOutcome:
        if not _identifier(event_id):
            return SimulationEpochOutcome("INVALID", "SIMULATION_EVENT_INVALID", None)
        with self.__lock:
            state = self.__state
            pending = state.reservation
            if pending is not None and pending.event.event_id == event_id:
                return SimulationEpochOutcome(
                    "PENDING_UNKNOWN" if pending.stage == "UNKNOWN" else "IN_FLIGHT",
                    pending.reason or pending.stage,
                    event_id,
                    pending.candidate_version_sha256,
                    pending.event,
                    pending.plan,
                )
            record = next((item for item in state.records if item.event.event_id == event_id), None)
            if record is not None and record.outcome in {"COMMITTED", "REJECTED"}:
                return SimulationEpochOutcome(
                    record.outcome,
                    "PROCESS_LOCAL_COMMIT" if record.outcome == "COMMITTED" else "GATE_REJECTED",
                    event_id,
                    event=record.event,
                    plan=record.plan,
                )
            return SimulationEpochOutcome("NOT_OBSERVED", "PROCESS_LOCAL_ONLY", event_id)

    def switch(
        self, event: SimulationEpochEvent, *, plan: SimulationEpochPlan | None = None
    ) -> SimulationEpochOutcome:
        try:
            event = _copy_event(event)
        except Exception:
            return SimulationEpochOutcome("INVALID", "SIMULATION_EVENT_INVALID", None)
        try:
            supplied_plan = _freeze_plan(plan, self.__mapping_type) if plan is not None else None
        except Exception:
            # A known ID may never be rebound, even to a malformed declaration.
            with self.__lock:
                if any(item.event.event_id == event.event_id for item in self.__state.records):
                    return SimulationEpochOutcome("CONFLICT", "EVENT_ID_REUSED", event.event_id)
            return SimulationEpochOutcome("INVALID", "SIMULATION_PLAN_INVALID", event.event_id)
        try:
            with self.__lock:
                state = self.__state
                if self.__initial_pending != "DECLARED_NO_PRIOR_PENDING":
                    return SimulationEpochOutcome(
                        "BLOCKED", "PRIOR_PENDING_UNKNOWN", event.event_id
                    )
                record = next(
                    (item for item in state.records if item.event.event_id == event.event_id), None
                )
                # An omitted plan on a known ID means its historical plan; a
                # new ID defaults to the current epoch's immutable plan.
                requested_plan = (
                    supplied_plan
                    if supplied_plan is not None
                    else (record.plan if record is not None else state.epoch.plan)
                )
                if record is not None and (record.event != event or record.plan != requested_plan):
                    return SimulationEpochOutcome("CONFLICT", "EVENT_ID_REUSED", event.event_id)
                if record is not None and record.outcome == "COMMITTED":
                    return SimulationEpochOutcome("DUPLICATE", "COMMITTED_EVENT_ID", event.event_id)
                pending = state.reservation
                if pending is not None:
                    return SimulationEpochOutcome(
                        "PENDING_UNKNOWN" if pending.stage == "UNKNOWN" else "BUSY",
                        pending.reason or pending.stage,
                        pending.event.event_id,
                    )
                status = state.epoch.view.status()
                if (
                    event.route != status.route
                    or event.source_generation != status.source_generation
                    or event.trading_day <= status.bound_trading_day
                    or event.sequence <= status.last_sequence
                ):
                    return SimulationEpochOutcome(
                        "REJECTED", "EVENT_SCOPE_OR_ORDER", event.event_id
                    )
                required = set(requested_plan.required_instruments)
                venues = {
                    mapping.venue
                    for mapping in requested_plan.mappings
                    if mapping.instrument_id in required
                }
                if (
                    not requested_plan.closure.owner_start
                    <= event.trading_day
                    <= requested_plan.closure.owner_end
                    or any(
                        (venue, event.trading_day.year) not in requested_plan.required_years
                        for venue in venues
                    )
                ):
                    return SimulationEpochOutcome(
                        "REJECTED", "EVENT_OUTSIDE_PREPARED_SCOPE", event.event_id
                    )
                if record is None and len(state.records) >= _MAX_EVENTS:
                    return SimulationEpochOutcome("BLOCKED", "EVENT_HISTORY_LIMIT", event.event_id)
                revision = state.revision
                reservation = _Reservation(event, requested_plan, revision, "GATE_IN_FLIGHT")
                next_state = replace(
                    state,
                    reservation=reservation,
                    records=_record(state.records, event, requested_plan, "IN_FLIGHT"),
                )
                self.__state = next_state
            context = GateContext(
                status.route,
                status.source_generation,
                status.bound_trading_day,
                status.last_sequence,
            )
            gate_event = _copy_event(event)
            decision = self.__gate.verify(gate_event, context)
            valid = (
                type(decision) is GateDecision
                and type(decision.accepted) is bool
                and type(decision.route) is str
                and decision.route == event.route
                and type(decision.source_generation) is str
                and decision.source_generation == event.source_generation
                and type(decision.trading_day) is date
                and decision.trading_day == event.trading_day
                and type(decision.sequence) is int
                and decision.sequence == event.sequence
                and type(decision.event_id) is str
                and decision.event_id == event.event_id
            )
            if not valid:
                raise ValueError("SIMULATION_GATE_UNKNOWN")
            if not decision.accepted:
                with self.__lock:
                    state = self.__state
                    if state.reservation != _Reservation(
                        event, requested_plan, revision, "GATE_IN_FLIGHT"
                    ):
                        raise ValueError("SIMULATION_RESERVATION_UNAVAILABLE")
                    next_state = replace(
                        state,
                        reservation=None,
                        records=_record(state.records, event, requested_plan, "REJECTED"),
                    )
                    self.__state = next_state
                return SimulationEpochOutcome("REJECTED", "GATE_REJECTED", event.event_id)
            with self.__lock:
                state = self.__state
                if state.reservation != _Reservation(
                    event, requested_plan, revision, "GATE_IN_FLIGHT"
                ):
                    raise ValueError("SIMULATION_RESERVATION_UNAVAILABLE")
                next_state = replace(
                    state, reservation=_Reservation(event, requested_plan, revision, "PREPARING")
                )
                self.__state = next_state

            def loaded(version):
                with self.__lock:
                    current = self.__state
                    if current.reservation != _Reservation(
                        event, requested_plan, revision, "PREPARING"
                    ):
                        raise ValueError("SIMULATION_RESERVATION_UNAVAILABLE")
                    next_state = replace(
                        current,
                        reservation=_Reservation(
                            event, requested_plan, revision, "PREPARING", version
                        ),
                    )
                    self.__state = next_state

            candidate = self.__prepare(
                event.route,
                event.source_generation,
                event.trading_day,
                event.sequence,
                event,
                "GATED_DAY_SWITCH",
                requested_plan,
                loaded,
            )
            with self.__lock:
                state = self.__state
                if state.revision != revision or state.reservation != _Reservation(
                    event, requested_plan, revision, "PREPARING", candidate.version_sha256
                ):
                    raise ValueError("SIMULATION_COMMIT_CONFLICT")
                next_state = replace(
                    state,
                    epoch=candidate,
                    revision=revision + 1,
                    reservation=None,
                    records=_record(state.records, event, requested_plan, "COMMITTED"),
                )
                self.__state = next_state
            return SimulationEpochOutcome(
                "COMMITTED", "SIMULATION_EPOCH_PUBLISHED", event.event_id, candidate.version_sha256
            )
        except BaseException as exc:
            with self.__lock:
                state = self.__state
                pending = state.reservation
                if pending is not None and pending.event == event:
                    reason = (
                        "GATE_UNKNOWN"
                        if pending.stage == "GATE_IN_FLIGHT"
                        else "PREPARATION_UNAVAILABLE"
                    )
                    unknown = _Reservation(
                        event,
                        pending.plan,
                        pending.revision,
                        "UNKNOWN",
                        pending.candidate_version_sha256,
                        reason,
                    )
                    next_state = replace(state, reservation=unknown)
                    self.__state = next_state
                    outcome = SimulationEpochOutcome(
                        "PENDING_UNKNOWN", reason, event.event_id, pending.candidate_version_sha256
                    )
                else:
                    record = next((item for item in state.records if item.event == event), None)
                    if record is not None and record.outcome == "COMMITTED":
                        outcome = SimulationEpochOutcome(
                            "COMMITTED",
                            "SIMULATION_EPOCH_PUBLISHED",
                            event.event_id,
                            state.epoch.version_sha256,
                        )
                    elif record is not None and record.outcome == "REJECTED":
                        outcome = SimulationEpochOutcome(
                            "REJECTED", "GATE_REJECTED", event.event_id
                        )
                    else:
                        outcome = SimulationEpochOutcome(
                            "PENDING_UNKNOWN", "RESERVATION_UNAVAILABLE", event.event_id
                        )
            if not isinstance(exc, Exception):
                raise
            return outcome
