"""SIMULATION_ONLY calendar binding prototype, not a source of trading authority.

No provider event, real clock, route identity, or gate is authenticated here.
A supplied simulation gate and snapshot factory are trust dependencies; this is
not wired into BtApi, Gateway, or Backtrader. Real-time state composition under
D67-08 and the D67-11 Feed API are outside this module. Manifest ranges are
unverified trading-day date declarations, not UTC coverage or venue evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from datetime import date
from threading import Lock
from typing import Any, Protocol

from bt_api_py.trading_calendar_store import CalendarSnapshot as StoreSnapshot

SIMULATION_ONLY = "SIMULATION_ONLY"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_VENUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


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
class DeclaredCoverage:
    venue: str
    start_day: date
    end_day: date


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
    coverage_status: str
    coverage_manifest_sha256: str | None
    binding_sha256: str | None
    version_sha256: str | None
    declared_coverage: tuple[DeclaredCoverage, ...]
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


@dataclass(frozen=True)
class SimulationUnknown:
    """A local unavailability result; it contains no venue or clock proof."""

    status: str
    reason: str
    calendar_ref: str
    sources_verified: bool = False


class BindingUnavailableError(RuntimeError):
    """No validated local snapshot is bound in this simulation."""


class _SnapshotLoadError(Exception):
    pass


def _text(value: Any) -> bool:
    return type(value) is str and bool(value.strip()) and value == value.strip()


def _integer(value: Any) -> bool:
    return type(value) is int and value >= 0


def _json_value(value: Any) -> None:
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON keys must be strings")
            _json_value(item)
        return
    raise ValueError("invalid JSON value")


def _canonical(value: Any) -> bytes:
    _json_value(value)
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _object_bytes(value: Any) -> dict[str, Any]:
    if type(value) is not bytes:
        raise ValueError("artifact must be bytes")
    obj = json.loads(value.decode("utf-8"), object_pairs_hook=_unique_pairs)
    if type(obj) is not dict or _canonical(obj) != value:
        raise ValueError("artifact must be canonical JSON object")
    return obj


def _digest(value: Any) -> bool:
    return type(value) is str and _HASH.fullmatch(value) is not None


def _covered_source(source: Any) -> tuple[tuple[DeclaredCoverage, ...], bytes]:
    """Independently check the store v2 bytes and references, not provenance."""
    if type(source) is not StoreSnapshot:
        raise ValueError("unexpected store snapshot type")
    if source.coverage_status == "NOT_PRESENT":
        raise _SnapshotLoadError("COVERAGE_UNAVAILABLE")
    if source.coverage_status != "DECLARED_UNVERIFIED":
        raise ValueError("invalid coverage status")
    if (
        not _text(source.version_id)
        or not _text(source.publication_id)
        or type(source.content) is not dict
        or not _digest(source.content_sha256)
        or not _digest(source.coverage_manifest_sha256)
        or not _digest(source.binding_sha256)
        or not _digest(source.version_sha256)
    ):
        raise ValueError("incomplete store snapshot")
    if set(source.content) != {"trading_days", "trading_hours", "exceptions", "sources"} or any(
        type(source.content[key]) not in (dict, list) for key in source.content
    ):
        raise ValueError("invalid content layers")
    content_bytes = _canonical(source.content)
    if _sha(content_bytes) != source.content_sha256:
        raise ValueError("content digest mismatch")
    manifest = _object_bytes(source.coverage_manifest_bytes)
    binding = _object_bytes(source.binding_bytes)
    if (
        _sha(source.coverage_manifest_bytes) != source.coverage_manifest_sha256
        or _sha(source.binding_bytes) != source.binding_sha256
    ):
        raise ValueError("artifact digest mismatch")
    if (
        set(manifest) != {"schema", "snapshot_sha256", "coverage"}
        or manifest["schema"] != "bt_api.calendar-simulation-coverage/1"
        or manifest["snapshot_sha256"] != source.content_sha256
        or type(manifest["coverage"]) is not list
        or not manifest["coverage"]
    ):
        raise ValueError("invalid manifest")
    if (
        set(binding) != {"schema", "snapshot_sha256", "manifest_sha256"}
        or binding["schema"] != "bt_api.calendar-simulation-binding/1"
        or binding["snapshot_sha256"] != source.content_sha256
        or binding["manifest_sha256"] != source.coverage_manifest_sha256
    ):
        raise ValueError("invalid binding")
    version = {
        "schema": "bt_api.calendar-store.snapshot/2",
        "content": source.content,
        "manifest": manifest,
        "manifest_sha256": source.coverage_manifest_sha256,
        "binding": binding,
        "binding_sha256": source.binding_sha256,
    }
    if _sha(_canonical(version)) != source.version_sha256:
        raise ValueError("version digest mismatch")
    coverage = []
    last_venue = None
    for row in manifest["coverage"]:
        if (
            type(row) is not dict
            or set(row) != {"venue", "coverage_start", "coverage_end", "semantics"}
            or type(row["venue"]) is not str
            or _VENUE.fullmatch(row["venue"]) is None
            or (last_venue is not None and row["venue"] <= last_venue)
            or row["semantics"] != "DECLARED_INCLUSIVE"
        ):
            raise ValueError("invalid coverage scope")
        start, end = (
            date.fromisoformat(row["coverage_start"]),
            date.fromisoformat(row["coverage_end"]),
        )
        if (
            start.isoformat() != row["coverage_start"]
            or end.isoformat() != row["coverage_end"]
            or start > end
        ):
            raise ValueError("invalid coverage interval")
        coverage.append(DeclaredCoverage(row["venue"], start, end))
        last_venue = row["venue"]
    return tuple(coverage), content_bytes


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
            mode=SIMULATION_ONLY,
            route=route,
            source_generation=source_generation,
            bound_trading_day=trading_day,
            last_sequence=last_sequence,
            calendar_ref=None,
            publication_id=None,
            content_sha256=None,
            coverage_status="UNAVAILABLE",
            coverage_manifest_sha256=None,
            binding_sha256=None,
            version_sha256=None,
            declared_coverage=(),
            binding_reason="PROCESS_START_OR_RESTART",
            load_error=None,
        )
        try:
            snapshot, source, coverage = self._load()
        except _SnapshotLoadError as exc:
            self._status = replace(
                self._status,
                load_error=str(exc),
                coverage_status="NOT_PRESENT"
                if str(exc) == "COVERAGE_UNAVAILABLE"
                else "UNAVAILABLE",
            )
        else:
            self._snapshot = snapshot
            self._status = replace(
                self._status,
                calendar_ref=source.version_id,
                publication_id=source.publication_id,
                content_sha256=source.content_sha256,
                coverage_status=source.coverage_status,
                coverage_manifest_sha256=source.coverage_manifest_sha256,
                binding_sha256=source.binding_sha256,
                version_sha256=source.version_sha256,
                declared_coverage=coverage,
            )

    def _load(self) -> tuple[Any, StoreSnapshot, tuple[DeclaredCoverage, ...]]:
        # Store validates the pointer and full immutable chain from one read.
        try:
            source = self._store.read_current()
        except Exception as exc:
            raise _SnapshotLoadError("STORE_READ_FAILED") from exc
        try:
            coverage, content_bytes = _covered_source(source)
        except _SnapshotLoadError:
            raise
        except Exception as exc:
            raise _SnapshotLoadError("COVERAGE_INVALID") from exc
        try:
            snapshot = self._factory(source.content, calendar_ref=source.version_id)
            if snapshot is None or not callable(getattr(snapshot, "query", None)):
                raise ValueError("snapshot factory did not validate a queryable snapshot")
            if _canonical(source.content) != content_bytes:
                raise ValueError("snapshot factory mutated source content")
        except Exception as exc:
            raise _SnapshotLoadError("SNAPSHOT_VALIDATION_FAILED") from exc
        return snapshot, source, coverage

    def status(self) -> BindingStatus:
        with self._lock:
            return self._status

    def query(self, **kwargs: Any) -> QueryResult:
        """Guard a trading-day query using the same pinned unverified manifest."""
        with self._lock:
            snapshot, status = self._snapshot, self._status
        if snapshot is None:
            raise BindingUnavailableError("no validated simulation snapshot is bound")

        def unknown(reason: str) -> QueryResult:
            return QueryResult(status, SimulationUnknown("UNKNOWN", reason, status.calendar_ref))

        if (
            set(kwargs) != {"venue", "kind", "product", "contract", "trading_day"}
            or any(not _text(kwargs.get(name)) for name in ("venue", "kind", "product", "contract"))
            or type(kwargs.get("trading_day")) is not date
        ):
            return unknown("INVALID_QUERY_SCOPE")
        coverage = next(
            (row for row in status.declared_coverage if row.venue == kwargs["venue"]), None
        )
        if coverage is None:
            return unknown("VENUE_NOT_DECLARED")
        if not coverage.start_day <= kwargs["trading_day"] <= coverage.end_day:
            return unknown("OUTSIDE_DECLARED_TRADING_DAY_COVERAGE")
        try:
            result = snapshot.query(**kwargs)
            if (
                getattr(result, "calendar_ref", None) != status.calendar_ref
                or getattr(result, "sources_verified", None) is not False
                or getattr(result, "status", None) not in ("COMPLETE", "UNKNOWN", "OUT_OF_SCOPE")
            ):
                return unknown("PROVIDER_RESULT_INVALID")
            return QueryResult(status, result)
        except Exception:
            return unknown("PROVIDER_QUERY_FAILED")

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
            snapshot, source, coverage = self._load()
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
                coverage_status=source.coverage_status,
                coverage_manifest_sha256=source.coverage_manifest_sha256,
                binding_sha256=source.binding_sha256,
                version_sha256=source.version_sha256,
                declared_coverage=coverage,
                binding_reason="GATED_DAY_SWITCH",
                load_error=None,
            )
            self._revision += 1
            return ActivationResult("ACTIVATED", self._status)
