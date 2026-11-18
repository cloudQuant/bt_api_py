"""SIMULATION_ONLY calendar binding prototype, not a source of trading authority.

No provider event, real clock, route identity, or gate is authenticated here.
The simulation gate and snapshot factory are trust dependencies. Gateway may
select a version using a separately checked CTP session day, which does not
authenticate calendar data or confer trading authority. Manifest ranges are
unverified trading-day date declarations, not UTC coverage or venue evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from threading import Lock
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from bt_api_base.trading_calendar_owner_bounds import SimulationCalendarOwnerBounds

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
class OwnerBoundsQueryResult:
    """A scope observation and the exact binding captured alongside it."""

    binding: BindingStatus
    bounds: SimulationCalendarOwnerBounds | SimulationUnknown


@dataclass(frozen=True)
class VenueDayQueryResult:
    """One pinned simulated venue-day declaration, never trading authority."""

    status: str
    reason: str
    binding: BindingStatus
    venue: str
    trading_day: date
    day_status: str | None = None
    source_ids: tuple[str, ...] = ()
    mode: str = SIMULATION_ONLY
    source_authentication: str = "NOT_PROVEN"
    trade_authorization: str = "NONE"


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
    schema = manifest.get("schema")
    valid_manifest = (
        schema == "bt_api.calendar-simulation-coverage/1"
        and set(manifest) == {"schema", "snapshot_sha256", "coverage"}
    ) or (
        schema == "bt_api.calendar-simulation-coverage/2"
        and set(manifest) == {"schema", "snapshot_sha256", "coverage", "data_origin"}
        and type(manifest["data_origin"]) is str
        and manifest["data_origin"] == "SYNTHETIC_MOCK"
    )
    if (
        not valid_manifest
        or manifest["snapshot_sha256"] != source.content_sha256
        or type(manifest["coverage"]) is not list
        or not manifest["coverage"]
    ):
        raise ValueError("invalid manifest")
    if schema == "bt_api.calendar-simulation-coverage/2" and source.simulation_provenance is not None:
        raise ValueError("synthetic seed and mixed provenance conflict")
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
    elif source.snapshot_schema == "bt_api.calendar-store.snapshot/7":
        version["schema"] = source.snapshot_schema
    if source.snapshot_schema is not None and source.snapshot_schema != version["schema"]:
        raise ValueError("snapshot schema mismatch")
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


def _validated_snapshot_origin(source: StoreSnapshot, provenance: Any) -> str:
    """Classify only declarations in an already validated, digest-bound snapshot."""
    manifest = _object_bytes(source.coverage_manifest_bytes)
    synthetic = manifest.get("schema") == "bt_api.calendar-simulation-coverage/2"
    if synthetic and provenance is not None:
        raise ValueError("synthetic seed and mixed provenance conflict")
    if synthetic:
        return "SYNTHETIC_MOCK"
    return "AKSHARE_MOCK" if provenance is not None else "UNSPECIFIED"


def _mock_utc(value: Any) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("invalid mock UTC")
    result = datetime.fromisoformat(value[:-1] + "+00:00")
    if result.isoformat().replace("+00:00", "Z") != value:
        raise ValueError("noncanonical mock UTC")
    return result


def _mock_interruption_ids(
    scope: dict, name: str, start: datetime, end: datetime
) -> tuple[str, str]:
    values = (
        scope["venue"],
        scope["kind"],
        scope["product"],
        scope["contract"],
        scope["trading_day"].isoformat(),
        name,
        start.isoformat(),
        end.isoformat(),
    )
    if any(type(value) is not str or not _VENUE.fullmatch(value) for value in values[:4] + (name,)):
        raise ValueError("invalid mock identity")
    digest = hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()
    return f"interrupt.{digest}.pre", f"interrupt.{digest}.post"


def _mock_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _declared_mock_windows(
    content: dict, scope: dict, result: Any, binding: BindingStatus | None = None
) -> dict | None:
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
        interrupted = any(item["action"] == "interrupt_window" for item in exceptions)
        if interrupted:
            source_rows = [
                source
                for source in content["sources"]
                if type(source) is dict and source.get("id") == mock[0]
            ]
            if (
                len(source_rows) != 1
                or set(source_rows[0]) != {"id", "description"}
                or type(source_rows[0]["description"]) is not str
                or not source_rows[0]["description"].strip()
            ):
                return None
            for field in ("valid_from", "valid_to"):
                if (
                    type(row[field]) is not str
                    or date.fromisoformat(row[field]).isoformat() != row[field]
                ):
                    return None
            all_templates = row["day_windows"] + row["night_windows"]
            if len({template["id"] for template in all_templates}) != len(all_templates):
                return None
            for group, night in ((row["day_windows"], False), (row["night_windows"], True)):
                for template in group:
                    if (
                        set(template) != {"id", "phase", "start", "end", "end_day_offset"}
                        or type(template["id"]) is not str
                        or _VENUE.fullmatch(template["id"]) is None
                        or template["phase"] not in {"continuous", "auction"}
                        or type(template["end_day_offset"]) is not int
                        or template["end_day_offset"] not in (0, 1)
                        or (not night and template["end_day_offset"] != 0)
                    ):
                        return None
            if any(
                type(template.get(field)) is not str
                or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", template[field]) is None
                for template in all_templates
                for field in ("start", "end")
            ):
                return None
            if any(
                (template["end_day_offset"] == 0 and template["end"] <= template["start"])
                or (template["end_day_offset"] == 1 and template["end"] >= template["start"])
                for template in all_templates
            ):
                return None
        start_date = mappings[0]["start_date"]
        night_date = date.fromisoformat(start_date) if start_date is not None else None
        if (
            interrupted
            and night_date is not None
            and (
                night_date.isoformat() != start_date
                or night_date >= day
                or not row["night_windows"]
            )
        ):
            return None
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
                if interrupted and end <= start:
                    return None
                expected[name] = (
                    kind,
                    template,
                    start.astimezone(_UTC),
                    end.astimezone(_UTC),
                )
        original_names = set(expected)
        originals = sorted((item[2], item[3]) for item in expected.values())
        if interrupted and any(a[1] > b[0] for a, b in zip(originals, originals[1:])):  # noqa: B905
            return None
        reserved = (
            {
                template["id"]
                for hours in content["trading_hours"]
                for template in hours["day_windows"] + hours["night_windows"]
            }
            if interrupted
            else set()
        )
        details = {}
        used = set()
        closed = False
        for item in exceptions:
            if interrupted and (closed or (item["action"] == "close_day" and used)):
                return None
            if (
                interrupted
                and item["action"] != "close_day"
                and item["window_id"] not in original_names
            ):
                return None
            if item["action"] == "close_day":
                closed = True
                expected.clear()
            elif item["action"] == "cancel_window" and item["window_id"] in expected:
                used.add(item["window_id"])
                del expected[item["window_id"]]
            elif item["action"] == "interrupt_window" and item["window_id"] in expected:
                if (
                    set(item)
                    != {
                        "venue",
                        "kind",
                        "product",
                        "contract",
                        "trading_day",
                        "action",
                        "window_id",
                        "source_ids",
                        "exception_schema",
                        "start_utc",
                        "end_utc",
                    }
                    or item["exception_schema"] != "bt_api.calendar-window-interruption/1"
                    or item["source_ids"] != mock
                    or item["window_id"] in used
                ):
                    return None
                name = item["window_id"]
                used.add(name)
                kind, template, original_start, original_end = expected.pop(name)
                first, last = _mock_utc(item["start_utc"]), _mock_utc(item["end_utc"])
                if not original_start < first < last < original_end:
                    return None
                pre, post = _mock_interruption_ids(scope, name, first, last)
                if pre == post or pre in reserved or post in reserved:
                    return None
                reserved.update((pre, post))
                expected[pre] = (kind, template, original_start, first)
                expected[post] = (kind, template, last, original_end)
                interruption = {
                    "start_utc": _mock_z(first),
                    "end_utc": _mock_z(last),
                    "source_id": mock[0],
                    "source_authentication": "NOT_PROVEN",
                }
                for ident, side in ((pre, "PRE"), (post, "POST")):
                    details[ident] = {
                        "original_window_id": name,
                        "segment": side,
                        "parent_start_utc": _mock_z(original_start),
                        "parent_end_utc": _mock_z(original_end),
                        "interruption": interruption,
                    }
            else:
                return None
        if interrupted and (
            type(result.windows) is not tuple
            or result.source_ids != (mock[0],)
            or binding is None
            or not _digest(binding.version_sha256)
            or not _digest(binding.content_sha256)
        ):
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
                or (
                    interrupted
                    and (
                        type(window.trading_day) is not date
                        or type(window.phase) is not str
                        or type(window.start_utc) is not datetime
                        or type(window.end_utc) is not datetime
                        or window.start_utc.utcoffset() != timedelta(0)
                        or window.end_utc.utcoffset() != timedelta(0)
                        or type(window.start_local) is not datetime
                        or type(window.end_local) is not datetime
                        or window.start_local != start.astimezone(zone)
                        or window.end_local != end.astimezone(zone)
                        or window.start_local.utcoffset() != start.astimezone(zone).utcoffset()
                        or window.end_local.utcoffset() != end.astimezone(zone).utcoffset()
                    )
                )
            ):
                return None
            entry = {
                "scope": kind,
                "window_id": window.window_id,
                "source_type": "MOCK",
                "source_id": mock[0],
                "source_authentication": "NOT_PROVEN",
                "declaration": "PINNED_RULE_AND_OWNER_DAY",
            }
            if interrupted:
                entry.update(
                    details.get(
                        window.window_id,
                        {
                            "original_window_id": window.window_id,
                            "segment": "WHOLE",
                            "parent_start_utc": _mock_z(start),
                            "parent_end_utc": _mock_z(end),
                            "interruption": None,
                        },
                    )
                )
                entry.update(
                    {
                        "trading_day": day_text,
                        "phase": window.phase,
                        "start_utc": _mock_z(start),
                        "end_utc": _mock_z(end),
                    }
                )
            output.append(entry)
        if interrupted:
            return {
                "schema": "bt_api.calendar-declared-mock-windows/2",
                "environment": SIMULATION_ONLY,
                "binding": {
                    "calendar_ref": binding.calendar_ref,
                    "version_sha256": binding.version_sha256,
                    "content_sha256": binding.content_sha256,
                },
                "scope": {
                    **{key: scope[key] for key in ("venue", "kind", "product", "contract")},
                    "trading_day": day_text,
                },
                "windows": output,
            }
        return {
            "schema": "bt_api.calendar-declared-mock-windows/1",
            "environment": SIMULATION_ONLY,
            "windows": output,
        }
    except Exception:
        return None


@dataclass(frozen=True)
class _VenueDayView:
    """Internal fixed binding; never refreshes during a multi-day query."""

    _snapshot: Any
    binding: BindingStatus
    _content: Any

    def query_venue_day(self, *, mode: str, venue: str, trading_day: date) -> VenueDayQueryResult:
        snapshot, status, content = self._snapshot, self.binding, self._content

        def unknown(reason: str) -> VenueDayQueryResult:
            return VenueDayQueryResult("UNKNOWN", reason, status, venue, trading_day)

        if mode not in (SIMULATION_ONLY, "OFFICIAL") or type(mode) is not str:
            return unknown("SIMULATION_MODE_REQUIRED")
        if (
            type(venue) is not str
            or _VENUE.fullmatch(venue) is None
            or type(trading_day) is not date
        ):
            return unknown("INVALID_QUERY_SCOPE")
        if snapshot is None:
            return unknown("BINDING_UNAVAILABLE")
        coverage = next((row for row in status.declared_coverage if row.venue == venue), None)
        if coverage is None or not coverage.start_day <= trading_day <= coverage.end_day:
            return unknown("COVERAGE_UNAVAILABLE")
        try:
            query = getattr(snapshot, "query_venue_day", None)
            if not callable(query):
                return unknown("PROVIDER_METHOD_UNAVAILABLE")
            observed = query(venue=venue, trading_day=trading_day)
            rows = [
                row
                for row in content["trading_days"]
                if row["venue"] == venue and row["day"] == trading_day.isoformat()
            ]
            if len(rows) != 1:
                return unknown("VENUE_DAY_UNAVAILABLE")
            row = rows[0]
            sources = row["source_ids"]
            inventory = {entry["id"] for entry in content["sources"]}
            if (
                row["status"] not in {"open", "closed"}
                or type(sources) is not list
                or not sources
                or any(type(source) is not str or not _text(source) for source in sources)
                or len(set(sources)) != len(sources)
                or not set(sources) <= inventory
                or observed.status != "COMPLETE"
                or observed.calendar_ref != status.calendar_ref
                or observed.venue != venue
                or observed.trading_day != trading_day
                or observed.day_status != row["status"]
                or type(observed.source_ids) is not tuple
                or observed.source_ids != tuple(sources)
                or observed.sources_verified is not False
            ):
                return unknown("VENUE_DAY_UNAVAILABLE")
            return VenueDayQueryResult(
                "COMPLETE",
                "EXPLICIT_SIMULATION_DECLARATION",
                status,
                venue,
                trading_day,
                row["status"],
                tuple(sources),
            )
        except Exception:
            return unknown("VENUE_DAY_UNAVAILABLE")


def _has_interruption(content: Any) -> bool:
    if type(content) is not dict or type(content.get("exceptions")) is not list:
        return True
    return any(
        type(row) is not dict
        or row.get("action") == "interrupt_window"
        or any(key in row for key in ("exception_schema", "start_utc", "end_utc"))
        for row in content["exceptions"]
    )


def _query_bound(
    snapshot: Any,
    status: BindingStatus,
    provenance: Any,
    content: Any,
    kwargs: dict,
    has_interruptions: bool,
) -> QueryResult:
    """Run the original query guard on one captured binding tuple."""
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
    coverage = next((row for row in status.declared_coverage if row.venue == kwargs["venue"]), None)
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
        has_interruption = False
        if has_interruptions and result.status == "COMPLETE":
            interruptions = []
            for item in content["exceptions"]:
                if type(item) is not dict:
                    return unknown("SIMULATION_INTERRUPTION_SOURCE_UNAVAILABLE")
                if item.get("action") != "interrupt_window":
                    if any(key in item for key in ("exception_schema", "start_utc", "end_utc")):
                        return unknown("SIMULATION_INTERRUPTION_SOURCE_UNAVAILABLE")
                    continue
                if (
                    any(
                        type(item.get(key)) is not str or _VENUE.fullmatch(item[key]) is None
                        for key in ("venue", "product", "window_id")
                    )
                    or item.get("kind") not in {"future", "option"}
                    or (
                        item.get("contract") is not None
                        and (
                            type(item["contract"]) is not str
                            or _VENUE.fullmatch(item["contract"]) is None
                        )
                    )
                    or "contract" not in item
                    or type(item.get("trading_day")) is not str
                    or date.fromisoformat(item["trading_day"]).isoformat() != item["trading_day"]
                ):
                    return unknown("SIMULATION_INTERRUPTION_SOURCE_UNAVAILABLE")
                interruptions.append(item)
            has_interruption = any(
                item.get("action") == "interrupt_window"
                and (item.get("venue"), item.get("kind"), item.get("product"))
                == (kwargs["venue"], kwargs["kind"], kwargs["product"])
                and item.get("trading_day") == kwargs["trading_day"].isoformat()
                and item.get("contract") in (None, kwargs["contract"])
                for item in interruptions
            )
        if has_interruption and provenance is not None:
            return unknown("SIMULATION_INTERRUPTION_SOURCE_UNAVAILABLE")
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
            mock_attribution = _declared_mock_windows(content, kwargs, result, status)
        if has_interruption and mock_attribution is None:
            return unknown("SIMULATION_INTERRUPTION_SOURCE_UNAVAILABLE")
        return QueryResult(status, result, attribution, mock_attribution)
    except Exception:
        return unknown("PROVIDER_QUERY_FAILED")


@dataclass(frozen=True, slots=True, repr=False)
class SimulationCalendarReadView:
    """A fixed local simulation view; neither source nor identity is authenticated.

    The injected snapshot factory remains a trusted read-only dependency. This
    interface protects ordinary callers from accidental mutation, not reflection
    or malicious in-process code.
    """

    __snapshot: Any
    __status: BindingStatus
    __provenance: Any
    __content: Any
    __has_interruptions: bool = field(init=False, repr=False)

    def __post_init__(self) -> None:
        content = deepcopy(self.__content)
        provenance = deepcopy(self.__provenance)
        object.__setattr__(self, "_SimulationCalendarReadView__content", content)
        object.__setattr__(self, "_SimulationCalendarReadView__provenance", provenance)
        object.__setattr__(
            self,
            "_SimulationCalendarReadView__has_interruptions",
            _has_interruption(content),
        )

    def status(self) -> BindingStatus:
        return self.__status

    def query(self, **kwargs: Any) -> QueryResult:
        captured = _query_bound(
            self.__snapshot,
            self.__status,
            self.__provenance,
            self.__content,
            kwargs,
            self.__has_interruptions,
        )
        result = captured.result
        if isinstance(result, SimulationUnknown):
            return captured
        try:
            for name in ("venue", "kind", "product", "contract", "trading_day"):
                value = getattr(result, name, None)
                if type(value) is not type(kwargs[name]) or value != kwargs[name]:
                    raise ValueError("provider scope mismatch")
        except Exception:
            return QueryResult(
                self.__status,
                SimulationUnknown("UNKNOWN", "PROVIDER_SCOPE_MISMATCH", self.__status.calendar_ref),
            )
        return captured

    def query_venue_day(self, *, mode: str, venue: str, trading_day: date) -> VenueDayQueryResult:
        return _VenueDayView(self.__snapshot, self.__status, self.__content).query_venue_day(
            mode=mode, venue=venue, trading_day=trading_day
        )

    def owner_day_bounds(
        self, *, mode: str, venue: str, kind: str, product: str, contract: str
    ) -> OwnerBoundsQueryResult:
        """Read an observation from this view's captured simulation snapshot."""
        def unknown(reason: str) -> OwnerBoundsQueryResult:
            return OwnerBoundsQueryResult(
                self.__status, SimulationUnknown("UNKNOWN", reason, self.__status.calendar_ref)
            )

        scope = {"venue": venue, "kind": kind, "product": product, "contract": contract}
        if mode not in (SIMULATION_ONLY, "OFFICIAL") or type(mode) is not str:
            return unknown("INVALID_MODE")
        if any(not _text(v) for v in scope.values()) or kind not in ("future", "option"):
            return unknown("INVALID_QUERY_SCOPE")
        if self.__snapshot is None or not self.__status.calendar_ref:
            return unknown("BINDING_UNAVAILABLE")
        if not any(row.venue == venue for row in self.__status.declared_coverage):
            return unknown("VENUE_NOT_DECLARED")
        try:
            result = self.__snapshot.owner_day_bounds(**scope)
            if type(result) is not SimulationCalendarOwnerBounds:
                return unknown("PROVIDER_RESULT_INVALID")
            SimulationCalendarOwnerBounds.__post_init__(result)
            if (
                result.calendar_ref != self.__status.calendar_ref
                or any(getattr(result, key) != value for key, value in scope.items())
            ):
                return unknown("PROVIDER_RESULT_INVALID")
            if result.status != "COMPLETE":
                return unknown("SCOPE_BOUNDS_UNAVAILABLE")
            return OwnerBoundsQueryResult(self.__status, result)
        except Exception:
            return unknown("SCOPE_BOUNDS_UNAVAILABLE")


def _load_simulation_snapshot(
    store: Any, factory: Any
) -> tuple[Any, StoreSnapshot, tuple[DeclaredCoverage, ...], Any, dict]:
    """Load the validated detached binding tuple without changing a binding."""
    try:
        source = store.read_current()
    except Exception as exc:
        raise _SnapshotLoadError("STORE_READ_FAILED") from exc
    try:
        coverage, content_bytes = _covered_source(source)
    except _SnapshotLoadError:
        raise
    except Exception as exc:
        raise _SnapshotLoadError("COVERAGE_INVALID") from exc
    try:
        snapshot = factory(source.content, calendar_ref=source.version_id)
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


class SimulationCalendarBinding:
    """One process-local fixed binding; constructing anew may load today's latest.

    ``store.read_current`` must validate and return one complete SDK snapshot;
    ``snapshot_factory(content, calendar_ref=...)`` must validate and return a
    detached read-only provider snapshot with ``query``. Injected ports are not
    verified here and MUST NOT be used to grant production trading permission.
    An event, including a replay, is checked by the gate before any activation.
    On load failure the bound day remains the old snapshot's day; it does not
    assert that the simulated source failed to advance. A new source generation
    requires constructing a new instance.
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
        if mode not in (SIMULATION_ONLY, "OFFICIAL") or type(mode) is not str:
            raise ValueError("explicit SIMULATION_ONLY or OFFICIAL mode is required")
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
        self._has_interruptions = False
        self._last_event: GateDecision | None = None
        self._prepared = None
        self._prepare_epoch = 0
        self._status = BindingStatus(
            mode=mode,
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
            self._has_interruptions = _has_interruption(content)
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
                data_origin=_validated_snapshot_origin(source, provenance),
            )

    def _load(self) -> tuple[Any, StoreSnapshot, tuple[DeclaredCoverage, ...], Any, dict]:
        # Store validates the pointer and full immutable chain from one read.
        return _load_simulation_snapshot(self._store, self._factory)

    def status(self) -> BindingStatus:
        with self._lock:
            return self._status

    def capture_read_view(self, *, mode: str) -> SimulationCalendarReadView:
        """Capture both simulation query paths from the same bound version."""
        if type(mode) is not str or mode not in (SIMULATION_ONLY, "OFFICIAL"):
            raise ValueError("explicit SIMULATION_ONLY mode is required")
        with self._lock:
            return SimulationCalendarReadView(
                self._snapshot, self._status, self._provenance, self._content
            )

    def _venue_day_view(self) -> _VenueDayView:
        """Capture status and detached provider snapshot atomically for SDK use."""
        with self._lock:
            return _VenueDayView(self._snapshot, self._status, self._content)

    def query_venue_day(self, *, mode: str, venue: str, trading_day: date) -> VenueDayQueryResult:
        """Query an explicit day from one pinned, unverified simulation snapshot."""
        return self._venue_day_view().query_venue_day(
            mode=mode, venue=venue, trading_day=trading_day
        )

    def query(self, **kwargs: Any) -> QueryResult:
        """Guard a trading-day query using the same pinned unverified manifest."""
        with self._lock:
            snapshot, status, provenance, content, has_interruptions = (
                self._snapshot,
                self._status,
                self._provenance,
                self._content,
                self._has_interruptions,
            )
        return _query_bound(snapshot, status, provenance, content, kwargs, has_interruptions)

    def prepare_update(self) -> object:
        """Load a single opaque candidate outside the owner thread."""
        with self._lock:
            revision = self._revision
            self._prepare_epoch += 1
            epoch = self._prepare_epoch
            self._prepared = None
        loaded = self._load()
        token = object()
        with self._lock:
            if revision != self._revision or epoch != self._prepare_epoch:
                raise BindingUnavailableError("calendar binding changed during preparation")
            self._prepared = (token, revision, loaded)
        return token

    def activate_prepared(self, event: Any, prepared: object) -> ActivationResult:
        """Apply only this instance's current prepared candidate after gate verification."""
        with self._lock:
            current = self._prepared
            state, last_event, revision = self._status, self._last_event, self._revision
            if current is None or current[0] is not prepared or current[1] != revision:
                return ActivationResult("STALE", state)
        context = GateContext(state.route, state.source_generation, state.bound_trading_day, state.last_sequence)
        try:
            decision = self._gate.verify(event, context)
        except Exception:
            return ActivationResult("GATE_ERROR", self.status())
        if (type(decision) is not GateDecision or decision.accepted is not True
                or decision.route != state.route or decision.source_generation != state.source_generation
                or type(decision.trading_day) is not date or not _integer(decision.sequence)
                or not _text(decision.event_id) or decision.trading_day <= state.bound_trading_day
                or decision.sequence <= state.last_sequence
                or (last_event is not None and decision.event_id == last_event.event_id)):
            return ActivationResult("GATE_REJECTED", self.status())
        with self._lock:
            if self._revision != revision or self._prepared is not current:
                return ActivationResult("STALE", self._status)
            snapshot, source, coverage, provenance, content = current[2]
            self._snapshot, self._provenance, self._content = snapshot, provenance, content
            self._has_interruptions = _has_interruption(content)
            self._last_event = decision
            self._status = replace(
                state, bound_trading_day=decision.trading_day, last_sequence=decision.sequence,
                calendar_ref=source.version_id, publication_id=source.publication_id,
                content_sha256=source.content_sha256, coverage_status=source.coverage_status,
                coverage_manifest_sha256=source.coverage_manifest_sha256,
                binding_sha256=source.binding_sha256, version_sha256=source.version_sha256,
                declared_coverage=coverage, binding_reason="GATED_DAY_SWITCH", load_error=None,
                data_origin=_validated_snapshot_origin(source, provenance),
            )
            self._prepared = None
            self._revision += 1
            return ActivationResult("ACTIVATED", self._status)

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
            self._has_interruptions = _has_interruption(content)
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
                data_origin=_validated_snapshot_origin(source, provenance),
            )
            self._revision += 1
            return ActivationResult("ACTIVATED", self._status)
