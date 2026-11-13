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
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from threading import Lock
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from bt_api_py.trading_calendar_store import CalendarSnapshot as StoreSnapshot

SIMULATION_ONLY = "SIMULATION_ONLY"
_UTC = timezone.utc  # noqa: UP017 - Python 3.9
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
    data_origin: str = "UNSPECIFIED"


@dataclass(frozen=True)
class ActivationResult:
    outcome: str
    binding: BindingStatus


@dataclass(frozen=True)
class QueryResult:
    """Metadata and provider query result captured from the same binding."""

    binding: BindingStatus
    result: Any
    simulation_provenance: dict[str, Any] | None = None
    declared_mock_provenance: dict[str, Any] | None = None


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
    """Check the exact covered snapshot bytes and simulation attribution."""
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
    if source.simulation_provenance is not None:
        from bt_api_py.trading_calendar_store import _simulation_provenance

        _simulation_provenance(source.content, source.simulation_provenance)
        version["schema"] = (
            "bt_api.calendar-store.snapshot/6"
            if source.simulation_provenance.get("schema")
            == "bt_api.calendar-mixed-scopes-provenance/3"
            else "bt_api.calendar-store.snapshot/5"
            if source.simulation_provenance.get("schema")
            == "bt_api.calendar-mixed-scopes-provenance/2"
            else "bt_api.calendar-store.snapshot/4"
            if source.simulation_provenance.get("schema")
            == "bt_api.calendar-mixed-scopes-provenance/1"
            else "bt_api.calendar-store.snapshot/3"
        )
        version["simulation_provenance"] = source.simulation_provenance
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


def _declared_mock_windows(content: dict, scope: dict, result: Any) -> dict | None:
    """Project only exact MOCK rule declarations, never query-wide source IDs.

    Reconstruct the provider's windows from the pinned rows and the explicit
    owner-day mapping. All applicable supporting rows must declare MOCK alone.
    The label is a local declaration, not source authentication.
    """
    try:
        mock = ["MOCK.simulation_only"]
        day = scope["trading_day"]
        day_text = day.isoformat()
        key = (scope["venue"], scope["kind"], scope["product"])
        rows = [
            row
            for row in content["trading_hours"]
            if (row["venue"], row["kind"], row["product"]) == key
            and scope["contract"] in row["contracts"]
            and row["valid_from"] <= day_text <= row["valid_to"]
        ]
        days = [
            row
            for row in content["trading_days"]
            if row["venue"] == scope["venue"] and row["day"] == day_text
        ]
        if len(rows) != 1 or len(days) != 1:
            return None
        row, day_row = rows[0], days[0]
        mappings = [item for item in row["night_mappings"] if item["trading_day"] == day_text]
        if (
            row["source_ids"] != mock
            or day_row["source_ids"] != mock
            or day_row["status"] != "open"
            or len(mappings) != 1
            or mappings[0]["source_ids"] != mock
            or row["windows_complete"] is not True
            or row["exceptions_complete"] is not True
        ):
            return None
        exceptions = [
            item
            for item in content["exceptions"]
            if (item["venue"], item["kind"], item["product"]) == key
            and item["trading_day"] == day_text
            and item["contract"] in (None, scope["contract"])
        ]
        if any(item["source_ids"] != mock for item in exceptions):
            return None
        start_date = mappings[0]["start_date"]
        night_date = date.fromisoformat(start_date) if start_date is not None else None
        zone = ZoneInfo("Asia/Shanghai")
        expected = {}
        for kind, templates, local_day in (
            ("DAY", row["day_windows"], day),
            ("NIGHT", row["night_windows"], night_date),
        ):
            if local_day is None:
                continue
            for template in templates:
                name = template["id"]
                if name in expected:
                    return None
                start = datetime.combine(local_day, time.fromisoformat(template["start"]), zone)
                end = datetime.combine(
                    local_day + timedelta(days=template["end_day_offset"]),
                    time.fromisoformat(template["end"]),
                    zone,
                )
                expected[name] = (
                    kind,
                    template,
                    start.astimezone(_UTC),
                    end.astimezone(_UTC),
                )
        for item in exceptions:
            if item["action"] == "close_day":
                expected.clear()
            elif item["action"] == "cancel_window" and item["window_id"] in expected:
                del expected[item["window_id"]]
            else:
                return None
        if len(result.windows) != len(expected):
            return None
        output = []
        seen = set()
        for window in result.windows:
            if window.window_id in seen or window.window_id not in expected:
                return None
            seen.add(window.window_id)
            kind, template, start, end = expected[window.window_id]
            if (
                window.trading_day != day
                or window.phase != template["phase"]
                or window.start_utc != start
                or window.end_utc != end
            ):
                return None
            output.append(
                {
                    "scope": kind,
                    "window_id": window.window_id,
                    "source_type": "MOCK",
                    "source_id": mock[0],
                    "source_authentication": "NOT_PROVEN",
                    "declaration": "PINNED_RULE_AND_OWNER_DAY",
                }
            )
        return {
            "schema": "bt_api.calendar-declared-mock-windows/1",
            "environment": SIMULATION_ONLY,
            "windows": output,
        }
    except Exception:
        return None


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
        self._provenance = None
        self._content = None
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
            snapshot, source, coverage, provenance, content = self._load()
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
            self._provenance = provenance
            self._content = content
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
                data_origin="AKSHARE_MOCK" if provenance is not None else "UNSPECIFIED",
            )

    def _load(self) -> tuple[Any, StoreSnapshot, tuple[DeclaredCoverage, ...], Any, dict]:
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
        return (
            snapshot,
            source,
            coverage,
            deepcopy(source.simulation_provenance),
            json.loads(content_bytes),
        )

    def status(self) -> BindingStatus:
        with self._lock:
            return self._status

    def query(self, **kwargs: Any) -> QueryResult:
        """Guard a trading-day query using the same pinned unverified manifest."""
        with self._lock:
            snapshot, status, provenance, content = (
                self._snapshot,
                self._status,
                self._provenance,
                self._content,
            )
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
            attribution = None
            if provenance is not None and result.status == "COMPLETE":
                scopes = (
                    provenance["scopes"]
                    if provenance["schema"]
                    in {
                        "bt_api.calendar-mixed-scopes-provenance/1",
                        "bt_api.calendar-mixed-scopes-provenance/2",
                        "bt_api.calendar-mixed-scopes-provenance/3",
                    }
                    else [provenance]
                )
                matches = [
                    item
                    for item in scopes
                    if (kwargs["venue"], kwargs["kind"], kwargs["product"], kwargs["contract"])
                    == (
                        item["target"]["venue"],
                        item["target"]["kind"],
                        item["target"]["product"],
                        item["target"]["mock_contract"],
                    )
                    and item["target"]["valid_from"]
                    <= kwargs["trading_day"].isoformat()
                    <= item["target"]["valid_to"]
                ]
                if len(matches) > 1:
                    return unknown("SIMULATION_ATTRIBUTION_AMBIGUOUS")
                selected = matches[0] if matches else None
                if selected is not None:
                    names = {window.window_id for window in result.windows}
                    attribution = deepcopy(
                        {
                            key: selected[key]
                            for key in (
                                "schema",
                                "environment",
                                "alignment",
                                "target",
                                "evidence_locator",
                                "semantic_sha256",
                                "evidence_sha256",
                            )
                        }
                    )
                    attribution["windows"] = deepcopy(
                        [window for window in selected["windows"] if window["window_id"] in names]
                    )
                    if {window["window_id"] for window in attribution["windows"]} != names:
                        return unknown("SIMULATION_ATTRIBUTION_INCOMPLETE")
            mock_attribution = None
            if attribution is None and result.status == "COMPLETE":
                mock_attribution = _declared_mock_windows(content, kwargs, result)
            return QueryResult(status, result, attribution, mock_attribution)
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
            snapshot, source, coverage, provenance, content = self._load()
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
            self._provenance = provenance
            self._content = content
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
                data_origin="AKSHARE_MOCK" if provenance is not None else "UNSPECIFIED",
            )
            self._revision += 1
            return ActivationResult("ACTIVATED", self._status)
