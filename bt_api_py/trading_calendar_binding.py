"""SIMULATION_ONLY calendar binding prototype, not a source of trading authority.

No provider event, real clock, route identity, or gate is authenticated here.
A supplied simulation gate and snapshot factory are trust dependencies; this is
not wired into BtApi, Gateway, or Backtrader. Real-time state composition under
D67-08 and the D67-11 Feed API are outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from threading import Lock
from typing import Any, Protocol

SIMULATION_ONLY = "SIMULATION_ONLY"


@dataclass(frozen=True)
class GateContext:
    """Only simulation inputs; the gate must independently check the event."""

    route: str
    source_generation: str
    bound_trading_day: date
    last_sequence: int


@dataclass(frozen=True)
class GateDecision:
    """A simulation gate decision, not a CTP event or signed attestation."""

    accepted: bool
    route: str
    source_generation: str
    trading_day: date
    sequence: int
    event_id: str


class DaySwitchGate(Protocol):
    def verify(self, event: Any, context: GateContext) -> GateDecision:
        """Check all event applicability and ordering in the simulated source."""
        ...


@dataclass(frozen=True)
class BindingStatus:
    mode: str
    route: str
    source_generation: str
    bound_trading_day: date
    last_sequence: int
    calendar_ref: str | None
    publication_id: str | None
    content_sha256: str | None
    binding_reason: str
    load_error: str | None


@dataclass(frozen=True)
class ActivationResult:
    outcome: str
    binding: BindingStatus


@dataclass(frozen=True)
class QueryResult:
    """Metadata and provider query result captured from the same binding."""

    binding: BindingStatus
    result: Any


class BindingUnavailableError(RuntimeError):
    """No validated local snapshot is bound in this simulation."""


class _SnapshotLoadError(Exception):
    pass


def _text(value: Any) -> bool:
    return type(value) is str and bool(value.strip()) and value == value.strip()


def _integer(value: Any) -> bool:
    return type(value) is int and value >= 0


class SimulationCalendarBinding:
    """One process-local fixed binding; constructing anew may load today's latest.

    ``store.read_current`` must validate and return one complete SDK snapshot;
    ``snapshot_factory(content, calendar_ref=...)`` must validate and return a
    detached read-only provider snapshot with ``query``. Injected ports are not
    verified here and MUST NOT be used to grant production trading permission.
    An event, including a replay, is checked by the gate before any activation.
    On load failure the bound day remains the old snapshot's day; it does not
    assert that the simulated source failed to advance. A new source generation
    requires constructing a new instance; reconnect handling is not implemented.
    """

    def __init__(
        self,
        *,
        mode: str,
        store: Any,
        snapshot_factory: Any,
        gate: DaySwitchGate,
        route: str,
        source_generation: str,
        trading_day: date,
        last_sequence: int,
    ):
        if mode != SIMULATION_ONLY or type(mode) is not str:
            raise ValueError("explicit SIMULATION_ONLY mode is required")
        if (
            not _text(route)
            or not _text(source_generation)
            or type(trading_day) is not date
            or not _integer(last_sequence)
            or not callable(getattr(store, "read_current", None))
            or not callable(snapshot_factory)
            or not callable(getattr(gate, "verify", None))
        ):
            raise ValueError("invalid simulation binding inputs")
        self._store = store
        self._factory = snapshot_factory
        self._gate = gate
        self._lock = Lock()
        self._revision = 0
        self._snapshot = None
        self._last_event: GateDecision | None = None
        self._status = BindingStatus(
            SIMULATION_ONLY,
            route,
            source_generation,
            trading_day,
            last_sequence,
            None,
            None,
            None,
            "PROCESS_START_OR_RESTART",
            None,
        )
        try:
            snapshot, source = self._load()
        except _SnapshotLoadError as exc:
            self._status = replace(self._status, load_error=str(exc))
        else:
            self._snapshot = snapshot
            self._status = replace(
                self._status,
                calendar_ref=source.version_id,
                publication_id=source.publication_id,
                content_sha256=source.content_sha256,
            )

    def _load(self) -> tuple[Any, Any]:
        # Store validates the pointer and full immutable chain from one read.
        try:
            source = self._store.read_current()
        except Exception as exc:
            raise _SnapshotLoadError("STORE_READ_FAILED") from exc
        try:
            if (
                source is None
                or not _text(getattr(source, "version_id", None))
                or not _text(getattr(source, "publication_id", None))
                or not _text(getattr(source, "content_sha256", None))
                or type(getattr(source, "content", None)) is not dict
            ):
                raise ValueError("invalid SDK snapshot")
            snapshot = self._factory(source.content, calendar_ref=source.version_id)
            if snapshot is None or not callable(getattr(snapshot, "query", None)):
                raise ValueError("snapshot factory did not validate a queryable snapshot")
        except Exception as exc:
            raise _SnapshotLoadError("SNAPSHOT_VALIDATION_FAILED") from exc
        return snapshot, source

    def status(self) -> BindingStatus:
        with self._lock:
            return self._status

    def query(self, **kwargs: Any) -> QueryResult:
        """Pass provider result through with the matching pinned binding metadata."""
        with self._lock:
            snapshot, status = self._snapshot, self._status
        if snapshot is None:
            raise BindingUnavailableError("no validated simulation snapshot is bound")
        return QueryResult(status, snapshot.query(**kwargs))

    def activate(self, event: Any) -> ActivationResult:
        """Attempt one externally gated day switch; no gate means no activation."""
        with self._lock:
            state = self._status
            last_event = self._last_event
            revision = self._revision
        context = GateContext(
            state.route, state.source_generation, state.bound_trading_day, state.last_sequence
        )
        try:
            decision = self._gate.verify(event, context)
        except Exception:
            return ActivationResult("GATE_ERROR", self.status())
        if (
            type(decision) is not GateDecision
            or type(decision.accepted) is not bool
            or not decision.accepted
            or decision.route != state.route
            or decision.source_generation != state.source_generation
            or type(decision.trading_day) is not date
            or not _integer(decision.sequence)
            or not _text(decision.event_id)
        ):
            return ActivationResult("GATE_REJECTED", self.status())
        if last_event is not None and decision == last_event:
            with self._lock:
                return ActivationResult(
                    "DUPLICATE" if revision == self._revision else "STALE", self._status
                )
        if (
            decision.trading_day <= state.bound_trading_day
            or decision.sequence <= state.last_sequence
            or (last_event is not None and decision.event_id == last_event.event_id)
        ):
            return ActivationResult("GATE_REJECTED", self.status())
        try:
            snapshot, source = self._load()
        except _SnapshotLoadError as exc:
            with self._lock:
                if revision != self._revision:
                    return ActivationResult("STALE", self._status)
                self._status = replace(self._status, load_error=str(exc))
                self._revision += 1
                return ActivationResult("LOAD_FAILED", self._status)
        with self._lock:
            if revision != self._revision:
                return ActivationResult("STALE", self._status)
            self._snapshot = snapshot
            self._last_event = decision
            self._status = replace(
                self._status,
                bound_trading_day=decision.trading_day,
                last_sequence=decision.sequence,
                calendar_ref=source.version_id,
                publication_id=source.publication_id,
                content_sha256=source.content_sha256,
                binding_reason="GATED_DAY_SWITCH",
                load_error=None,
            )
            self._revision += 1
            return ActivationResult("ACTIVATED", self._status)
