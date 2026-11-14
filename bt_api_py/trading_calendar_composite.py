"""SIMULATION_ONLY four-layer composition; not a publication or authority.

Each VenueContent carries caller-declared inclusive dates. They are preserved in
an immutable manifest bound to the snapshot digest; an additional binding
record commits to both digests. These do not prove UTC or instrument coverage.
The experimental SDK store stores only the four-layer snapshot, so consumers
must retain and verify the separate manifest and binding record themselves.
Source names and injected provider validation establish no official facts,
trading authorization, actual venue coverage, or verified provenance.
The caller must supply a local, side-effect-free factory; arbitrary injected
Python code cannot be sandboxed or made pure by this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bt_api_py.trading_calendar_update_simulation import VenueContent

SIMULATION_ONLY = "SIMULATION_ONLY"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_RECORD_LAYERS = ("trading_days", "trading_hours", "exceptions")
_IDENTITY_FIELDS = {
    "trading_days": ("venue", "day"),
    "trading_hours": ("venue", "kind", "product", "contracts", "valid_from", "valid_to"),
    "exceptions": ("venue", "kind", "product", "contract", "trading_day", "action", "window_id"),
}
_LEGACY_EXCEPTION_FIELDS = frozenset(
    {"venue", "kind", "product", "contract", "trading_day", "action", "window_id", "source_ids"}
)


def _require_legacy_exceptions(content: Any) -> None:
    """Reject exception formats the legacy simulation paths cannot compare safely."""
    if type(content) is not dict or type(content.get("exceptions")) is not list:
        raise ValueError("unsupported legacy exception layer")
    for row in content["exceptions"]:
        if (
            type(row) is not dict
            or set(row) != _LEGACY_EXCEPTION_FIELDS
            or type(row.get("action")) is not str
            or row["action"] not in ("close_day", "cancel_window")
        ):
            raise ValueError("unsupported legacy exception declaration")


@dataclass(frozen=True)
class CompositeResult:
    mode: str
    status: str
    source_evidence: str
    coverage_evidence: str
    snapshot_bytes: bytes | None = None
    snapshot_sha256: str | None = None
    manifest_bytes: bytes | None = None
    manifest_sha256: str | None = None
    binding_bytes: bytes | None = None
    binding_sha256: str | None = None


def _result(
    status: str, *, snapshot: bytes | None = None, manifest: bytes | None = None
) -> CompositeResult:
    snapshot_sha = hashlib.sha256(snapshot).hexdigest() if snapshot is not None else None
    manifest_sha = hashlib.sha256(manifest).hexdigest() if manifest is not None else None
    binding = None
    if snapshot_sha is not None and manifest_sha is not None:
        binding = _canonical(
            {
                "schema": "bt_api.calendar-simulation-binding/1",
                "snapshot_sha256": snapshot_sha,
                "manifest_sha256": manifest_sha,
            }
        )
    return CompositeResult(
        SIMULATION_ONLY,
        status,
        "UNVERIFIED",
        "DECLARED_ONLY",
        snapshot,
        snapshot_sha,
        manifest,
        manifest_sha,
        binding,
        hashlib.sha256(binding).hexdigest() if binding is not None else None,
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _id(value: Any) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def _has_valid_refs(value: Any, local_sources: set[str]) -> bool:
    if type(value) is dict:
        for key, item in value.items():
            if key == "source_ids" and (
                type(item) is not list
                or not item
                or any(not _id(v) for v in item)
                or len(set(item)) != len(item)
                or not set(item) <= local_sources
            ):
                return False
            if not _has_valid_refs(item, local_sources):
                return False
    elif type(value) is list:
        return all(_has_valid_refs(item, local_sources) for item in value)
    return True


def compose_simulated(
    *, mode: str, venues: tuple[VenueContent, ...], snapshot_factory: Callable[..., Any]
) -> CompositeResult:
    """Compose distinct venue inputs and run an injected, untrusted factory.

    The manifest is ordered by venue and each layer by canonical record bytes.
    Nested record arrays retain their original order. Duplicate records are
    rejected, never silently dropped; identical source IDs may be deduplicated
    across venues.
    All outcomes are hypothetical and include no production trust assertion.
    """
    if type(mode) is not str or mode != SIMULATION_ONLY:
        raise ValueError("explicit SIMULATION_ONLY mode is required")
    if (
        type(venues) is not tuple
        or not venues
        or any(type(v) is not VenueContent for v in venues)
        or not callable(snapshot_factory)
    ):
        return _result("INVALID_INPUT")
    if len({item.venue for item in venues}) != len(venues):
        return _result("DUPLICATE_VENUE")
    records: dict[str, list[tuple[bytes, dict[str, Any]]]] = {name: [] for name in _RECORD_LAYERS}
    sources: dict[str, tuple[bytes, dict[str, Any]]] = {}
    seen: dict[str, set[bytes]] = {name: set() for name in _RECORD_LAYERS}
    seen_identity: dict[str, set[bytes]] = {name: set() for name in _RECORD_LAYERS}
    coverage = []
    try:
        for venue in sorted(venues, key=lambda item: item.venue):
            layers = json.loads(venue.canonical_layers)
            if (
                type(layers) is not dict
                or set(layers) != {*_RECORD_LAYERS, "sources"}
                or any(type(layers[name]) is not list for name in (*_RECORD_LAYERS, "sources"))
            ):
                return _result("INVALID_INPUT")
            if not layers["sources"] or not layers["trading_days"] or not layers["trading_hours"]:
                return _result("INVALID_INPUT")
            local_sources: set[str] = set()
            for source in layers["sources"]:
                if (
                    type(source) is not dict
                    or set(source) != {"id", "description"}
                    or not _id(source["id"])
                    or type(source["description"]) is not str
                    or not source["description"].strip()
                ):
                    return _result("INVALID_INPUT")
                source_id = source["id"]
                if source_id in local_sources:
                    return _result("DUPLICATE_RECORD")
                local_sources.add(source_id)
                source_bytes = _canonical(source)
                if source_id in sources and sources[source_id][0] != source_bytes:
                    return _result("SOURCE_CONFLICT")
                sources.setdefault(source_id, (source_bytes, source))
            for layer in _RECORD_LAYERS:
                for row in layers[layer]:
                    if type(row) is not dict:
                        return _result("INVALID_INPUT")
                    if row.get("venue") != venue.venue:
                        return _result("VENUE_MISMATCH")
                    if "source_ids" not in row or not _has_valid_refs(row, local_sources):
                        return _result("SOURCE_REFERENCE_MISSING")
                    if any(key not in row for key in _IDENTITY_FIELDS[layer]):
                        return _result("INVALID_INPUT")
                    if layer == "exceptions":
                        _require_legacy_exceptions({"exceptions": [row]})
                    if layer == "trading_hours":
                        mappings = row.get("night_mappings")
                        if type(mappings) is not list or any(
                            type(m) is not dict or "source_ids" not in m for m in mappings
                        ):
                            return _result("SOURCE_REFERENCE_MISSING")
                    row_bytes = _canonical(row)
                    identity = _canonical([row[key] for key in _IDENTITY_FIELDS[layer]])
                    if row_bytes in seen[layer] or identity in seen_identity[layer]:
                        return _result("DUPLICATE_RECORD")
                    seen[layer].add(row_bytes)
                    seen_identity[layer].add(identity)
                    records[layer].append((row_bytes, row))
            coverage.append(
                {
                    "venue": venue.venue,
                    "coverage_start": venue.coverage_start.isoformat(),
                    "coverage_end": venue.coverage_end.isoformat(),
                    "semantics": "DECLARED_INCLUSIVE",
                }
            )
        content = {
            name: [row for _, row in sorted(records[name], key=lambda item: item[0])]
            for name in _RECORD_LAYERS
        }
        content["sources"] = [sources[key][1] for key in sorted(sources)]
        snapshot = _canonical(content)
        snapshot_sha = hashlib.sha256(snapshot).hexdigest()
        # Injected code may inspect a detached copy, never the output bytes.
        validation_input = json.loads(snapshot)
        try:
            provider = snapshot_factory(
                validation_input, calendar_ref=f"simulation-{snapshot_sha[:20]}"
            )
        except Exception:
            return _result("PROVIDER_ERROR")
        try:
            if _canonical(validation_input) != snapshot:
                return _result("PROVIDER_MUTATED_INPUT")
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return _result("PROVIDER_MUTATED_INPUT")
        try:
            queryable = provider is not None and callable(getattr(provider, "query", None))
        except Exception:
            return _result("PROVIDER_ERROR")
        if not queryable:
            return _result("PROVIDER_REJECTED")
        manifest = _canonical(
            {
                "schema": "bt_api.calendar-simulation-coverage/1",
                "snapshot_sha256": snapshot_sha,
                "coverage": coverage,
            }
        )
        return _result("READY_SIMULATED", snapshot=snapshot, manifest=manifest)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return _result("INVALID_INPUT")
