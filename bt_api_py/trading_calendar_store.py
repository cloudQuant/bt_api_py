"""Local, neutral calendar snapshot storage; not a calendar validator.

INTERNAL EXPERIMENTAL SLICE: do not integrate into a production publication path.
The process-crash intent fence is not proof of power-loss durability or 67.02 acceptance.
The caller must validate venue semantics, source coverage and release authority.
This provisional envelope is independent of the as-yet-unfrozen 67.01 schema.
Only cooperating writers on a trusted local POSIX filesystem are supported; external
deletion or replacement of lock, intent, or directory entries violates that model.
History requires a caller-attested UTC error bound at every observation; the
default is untrusted. It checks a system boot ID and requires a shared monotonic
clock across processes in that boot. Wall/monotonic deltas must agree within
one second, and each uncertainty interval is widened by its attested bounds.
These checks cannot establish UTC accuracy or detect excursions between samples.
Reboots, network filesystems, hostile writers and power loss
are outside this process-crash model. Recovery of lost CURRENT is unavailable.
Hashes are not authentication.
Publication /6 stores larger complete-difference declarations. Upgrade every
reader of a shared directory before any writer emits /6: older readers reject it.
The /6 event has a finite size limit. Callers may request explicit read budgets;
budget exhaustion rejects the observation without truncating history.

On Linux, CLOCK_MONOTONIC normally excludes suspend time. A suspend can cause
wall/monotonic deltas to disagree beyond tolerance, yielding HISTORY_UNCERTAIN.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows is explicitly unsupported
    fcntl = None

_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_CANDIDATE_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_SNAPSHOT = "bt_api.calendar-store.snapshot/1"
_SNAPSHOT_COVERED = "bt_api.calendar-store.snapshot/2"
_SNAPSHOT_MIXED = "bt_api.calendar-store.snapshot/3"
_SNAPSHOT_MULTIMIX = "bt_api.calendar-store.snapshot/4"
_SNAPSHOT_BATCH = "bt_api.calendar-store.snapshot/5"
_SNAPSHOT_PARTIAL = "bt_api.calendar-store.snapshot/6"
_EVENT = "bt_api.calendar-store.publication/1"
_EVENT_TIMED = "bt_api.calendar-store.publication/2"
_EVENT_COVERED = "bt_api.calendar-store.publication/3"
_EVENT_POLICY = "bt_api.calendar-store.publication/4"
_EVENT_POLICY_COVERED = "bt_api.calendar-store.publication/5"
_EVENT_POLICY_FULL = "bt_api.calendar-store.publication/6"
_LEGACY_DIFFERENCE_BYTES = 1024 * 1024
_FULL_DIFFERENCE_BYTES = 16 * 1024 * 1024
_PUBLICATION_EVENT_BYTES = 17 * 1024 * 1024
_POLICY = "bt_api.calendar-store.review-policy/1"
_POLICY_AUDITED = "bt_api.calendar-store.review-policy/2"
_POLICY_AUDIT_MARKER = "bt_api.calendar-store.policy-audit-marker/1"
_POLICY_CHANGE = "bt_api.calendar-store.policy-change/1"
_POLICY_CHANGE_V2 = "bt_api.calendar-store.policy-change/2"
_POLICY_CHANGE_INTENT = "bt_api.calendar-store.policy-change-intent/1"
_POLICY_MAX_RECORDS = 4096
_POLICY_MAX_RECORD_BYTES = 16 * 1024
_POLICY_MAX_TOTAL_BYTES = 8 * 1024 * 1024
_POLICY_MAX_INTENT_BYTES = 64 * 1024
_POLICY_MARKER = "bt_api.calendar-store.review-policy-marker/1"
_POLICY_EVENTS = (_EVENT_POLICY, _EVENT_POLICY_COVERED, _EVENT_POLICY_FULL)
_COVERED_EVENTS = (_EVENT_COVERED, _EVENT_POLICY_COVERED, _EVENT_POLICY_FULL)
_TIMED_EVENTS = (_EVENT_TIMED, _EVENT_COVERED, *_POLICY_EVENTS)
_MANIFEST = "bt_api.calendar-simulation-coverage/1"
_BINDING = "bt_api.calendar-simulation-binding/1"
_VENUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_POINTER = "bt_api.calendar-store.current/1"
_INTENT = "bt_api.calendar-store.intent/1"
_RECEIPT = "bt_api.calendar-store.receipt/1"
_RECEIPT_TIMED = "bt_api.calendar-store.receipt/2"
_CLOCK_TOLERANCE_NS = 1_000_000_000


class CalendarStoreError(Exception):
    """Unavailable, invalid, unsupported, or untrusted local store state."""


class CalendarPolicyChangeUnknownError(CalendarStoreError):
    """Policy replace was attempted; observed_policy is only a later readback."""

    def __init__(self, observed_policy: ReviewPolicy | None):
        super().__init__("policy change outcome unknown; re-read current policy before any retry")
        self.observed_policy = observed_policy


@dataclass(frozen=True)
class CalendarSnapshot:
    publication_id: str
    sequence: int
    version_id: str
    content_sha256: str
    content: dict[str, Any]
    coverage_status: str = "NOT_PRESENT"
    coverage_manifest_bytes: bytes | None = None
    coverage_manifest_sha256: str | None = None
    binding_bytes: bytes | None = None
    binding_sha256: str | None = None
    version_sha256: str | None = None
    # Set by read_current after validating the pointer and its complete chain.
    publication_sha256: str | None = None
    simulation_provenance: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReceiptConsistentCurrent:
    """Structurally consistent local receipts; their origin is unauthenticated."""

    snapshot: CalendarSnapshot
    publication_proof: str = "NOT_PROVEN"
    receipt_consistency: str = "LOCAL_RECEIPTS_CONSISTENT_UNAUTHENTICATED"
    receipt_authentication: str = "NOT_PROVEN"
    durability: str = "UNCONFIRMED"
    source_authentication: str = "NOT_PROVEN"
    identity_authentication: str = "NOT_PROVEN"
    runtime_binding: str = "NOT_PROVEN"


@dataclass(frozen=True)
class CalendarReadBudget:
    """Optional limits for one consistent CURRENT read, not time guarantees."""

    max_file_bytes: int
    max_total_bytes: int
    max_publications: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (self.max_file_bytes, self.max_total_bytes, self.max_publications)
        ):
            raise ValueError("calendar read limits must be positive integers")


@dataclass(frozen=True)
class CurrentPrecondition:
    """Experimental CAS condition; this does not verify review or source authority."""

    mode: str
    publication_id: str | None = None
    publication_sha256: str | None = None

    @classmethod
    def absent(cls) -> CurrentPrecondition:
        return cls("ABSENT")

    @classmethod
    def matching(cls, publication_id: str, publication_sha256: str) -> CurrentPrecondition:
        return cls("MATCH", publication_id, publication_sha256)


@dataclass(frozen=True)
class ReviewPolicy:
    """Directory policy identity; it does not authenticate its source."""

    directory_id: str
    revision: str
    mode: str
    mode_source: str


@dataclass(frozen=True)
class PolicyChangeResult:
    """Visibility of the original change, not clock or identity authentication."""

    status: str
    operation_id: str
    policy: ReviewPolicy | None = None
    detail: str = ""


@dataclass(frozen=True)
class PolicyPublication:
    """Candidate and difference are CALLER_DECLARED_UNVERIFIED, not authorization.

    Later confirmation requires rereading and recomputing under the writer lock.
    """

    directory_id: str
    policy_revision: str
    mode: str
    snapshot_sha256: str
    difference: Any
    difference_sha256: str
    reviewer: str | None = None
    confirmation_snapshot_sha256: str | None = None
    confirmation_difference_sha256: str | None = None
    confirmation_policy_revision: str | None = None
    confirmation_current: CurrentPrecondition | None = None
    confirmation_utc: str | None = None


@dataclass(frozen=True)
class PublicationResult:
    visibility: str
    durability: str
    publication_id: str
    version_id: str
    detail: str = ""
    fence: str = "UNKNOWN"
    post_commit: str = "NOT_APPLICABLE"


@dataclass(frozen=True)
class HistoryResult:
    status: str
    snapshot: CalendarSnapshot | None = None
    reason: str = ""


def _parse_utc(value: Any) -> datetime:
    if type(value) is not str:
        raise CalendarStoreError("missing UTC boundary")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise CalendarStoreError("invalid UTC boundary") from exc
    if parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
        raise CalendarStoreError("noncanonical UTC boundary")
    return parsed


def _boot_id_valid(value: Any) -> bool:
    if type(value) is not str:
        return False
    try:
        platform, value_uuid = value.split(":", 1)
        return platform in {"darwin", "linux"} and str(uuid.UUID(value_uuid)) == value_uuid
    except (ValueError, AttributeError):
        return False


def _system_boot_identity() -> str | None:
    try:
        if sys.platform == "darwin":
            result = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
                capture_output=True,
                text=True,
                check=True,
                timeout=3,
            )
            return f"darwin:{uuid.UUID(result.stdout.strip())}"
        if sys.platform.startswith("linux"):
            raw = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
            return f"linux:{uuid.UUID(raw)}"
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def _clock_pair_valid(first: tuple[Any, Any, Any, Any], second: tuple[Any, Any, Any, Any]) -> bool:
    try:
        a, b = _parse_utc(first[0]), _parse_utc(second[0])
        m1, m2 = first[1], second[1]
        if (
            type(m1) is not int
            or type(m2) is not int
            or m1 < 0
            or m2 < m1
            or b < a
            or not _boot_id_valid(first[2])
            or first[2] != second[2]
            or type(first[3]) is not int
            or type(second[3]) is not int
            or first[3] < 0
            or second[3] < 0
        ):
            return False
        delta = b - a
        wall_ns = ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000
        return abs(wall_ns - (m2 - m1)) <= _CLOCK_TOLERANCE_NS
    except (CalendarStoreError, OverflowError):
        return False


def _validate_optional_sample(wall: Any, monotonic: Any, boot_id: Any, bound_us: Any) -> None:
    if boot_id is not None and not _boot_id_valid(boot_id):
        raise CalendarStoreError("invalid boot identity")
    if bound_us is not None and (type(bound_us) is not int or not 0 <= bound_us <= 60_000_000):
        raise CalendarStoreError("invalid UTC error bound")
    if wall is None and monotonic is None:
        return
    _parse_utc(wall)
    if type(monotonic) is not int or monotonic < 0:
        raise CalendarStoreError("invalid monotonic boundary")


def _identifier(value: Any) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def _digest(value: Any) -> bool:
    return type(value) is str and _HASH.fullmatch(value) is not None


def _actor(value: Any) -> bool:
    return _identifier(value) and value.casefold() not in {
        "anonymous",
        "anon",
        "unknown",
        "none",
        "null",
        "unidentified",
    }


def _policy_value(value: Any) -> ReviewPolicy:
    if type(value) is not dict:
        raise CalendarStoreError("invalid review policy")
    common = {"schema", "directory_id", "revision", "mode", "mode_source"}
    if value.get("schema") == _POLICY_AUDITED:
        _fields(value, common | {"last_change"}, _POLICY_AUDITED)
        ref = value["last_change"]
        if (
            type(ref) is not dict
            or set(ref) != {"operation_id", "sha256"}
            or not _identifier(ref["operation_id"])
            or not _digest(ref["sha256"])
        ):
            raise CalendarStoreError("invalid policy audit reference")
    else:
        _fields(value, common, _POLICY)
    if (
        not _identifier(value["directory_id"])
        or not _identifier(value["revision"])
        or value["mode"] not in ("auto", "manual")
        or value["mode_source"] not in ("default", "explicit")
        or (value["mode_source"] == "default" and value["mode"] != "auto")
    ):
        raise CalendarStoreError("invalid review policy")
    return ReviewPolicy(
        value["directory_id"], value["revision"], value["mode"], value["mode_source"]
    )


def _change_value(value: Any) -> None:
    keys = {
        "schema",
        "operation_id",
        "directory_id",
        "old_policy",
        "new_revision",
        "new_mode",
        "actor",
        "at_utc",
        "identity_status",
        "clock_status",
        "prior_history",
    }
    if type(value) is not dict:
        raise CalendarStoreError("invalid policy change")
    if value.get("schema") == _POLICY_CHANGE_V2:
        _fields(value, keys | {"new_mode_source"}, _POLICY_CHANGE_V2)
        if value["new_mode_source"] not in ("default", "explicit") or (
            value["new_mode_source"] == "default" and value["new_mode"] != "auto"
        ):
            raise CalendarStoreError("invalid policy change source")
    else:
        _fields(value, keys, _POLICY_CHANGE)
    old = _policy_value(value["old_policy"])
    if (
        not _identifier(value["operation_id"])
        or value["directory_id"] != old.directory_id
        or not _identifier(value["new_revision"])
        or value["new_revision"] == old.revision
        or value["new_mode"] not in ("auto", "manual")
        or not _actor(value["actor"])
        or value["identity_status"] != "UNAUTHENTICATED"
        or value["clock_status"] != "UNVERIFIED"
        or value["prior_history"]
        != ("UNKNOWN" if value["old_policy"]["schema"] == _POLICY else "CHAINED")
    ):
        raise CalendarStoreError("invalid policy change")
    _parse_utc(value["at_utc"])


def _changed_policy(record: dict[str, Any]) -> dict[str, Any]:
    _change_value(record)
    return {
        "schema": _POLICY_AUDITED,
        "directory_id": record["directory_id"],
        "revision": record["new_revision"],
        "mode": record["new_mode"],
        "mode_source": record.get("new_mode_source", "explicit"),
        "last_change": {"operation_id": record["operation_id"], "sha256": _sha(_canonical(record))},
    }


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
                raise CalendarStoreError("JSON object keys must be strings")
            _json_value(item)
        return
    raise CalendarStoreError("invalid JSON value")


def _canonical(value: Any) -> bytes:
    try:
        _json_value(value)
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (RecursionError, UnicodeError, ValueError, TypeError) as exc:
        raise CalendarStoreError("invalid JSON value") from exc


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CalendarStoreError("duplicate JSON key")
        result[key] = value
    return result


def _decode(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_object_pairs)
        if type(value) is not dict or _canonical(value) != data:
            raise CalendarStoreError("noncanonical JSON object")
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CalendarStoreError("invalid JSON") from exc


def _fields(value: dict[str, Any], keys: set[str], schema: str) -> None:
    if set(value) != keys or value.get("schema") != schema:
        raise CalendarStoreError("invalid schema or fields")


def _content(content: Any) -> bytes:
    if type(content) is not dict or set(content) != {
        "trading_days",
        "trading_hours",
        "exceptions",
        "sources",
    }:
        raise CalendarStoreError("content must contain all four calendar layers")
    if any(type(content[k]) not in (dict, list) for k in content):
        raise CalendarStoreError("calendar layers must be objects or arrays")
    return _canonical(content)


def _coverage_bundle(
    content_bytes: bytes,
    manifest_bytes: Any,
    manifest_sha256: Any,
    binding_bytes: Any,
    binding_sha256: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check exact composite artifact hashes; declarations are not verified facts."""
    if (
        type(manifest_bytes) is not bytes
        or not _digest(manifest_sha256)
        or _sha(manifest_bytes) != manifest_sha256
        or type(binding_bytes) is not bytes
        or not _digest(binding_sha256)
        or _sha(binding_bytes) != binding_sha256
    ):
        raise CalendarStoreError("invalid manifest or binding digest")
    manifest = _decode(manifest_bytes)
    _fields(manifest, {"schema", "snapshot_sha256", "coverage"}, _MANIFEST)
    if (
        manifest["snapshot_sha256"] != _sha(content_bytes)
        or type(manifest["coverage"]) is not list
        or not manifest["coverage"]
    ):
        raise CalendarStoreError("manifest does not bind content or has no coverage")
    previous_venue = None
    for row in manifest["coverage"]:
        if type(row) is not dict or set(row) != {
            "venue",
            "coverage_start",
            "coverage_end",
            "semantics",
        }:
            raise CalendarStoreError("invalid coverage declaration")
        venue = row["venue"]
        if (
            type(venue) is not str
            or _VENUE.fullmatch(venue) is None
            or (previous_venue is not None and venue <= previous_venue)
            or row["semantics"] != "DECLARED_INCLUSIVE"
        ):
            raise CalendarStoreError("invalid coverage scope or ordering")
        try:
            start, end = (
                date.fromisoformat(row["coverage_start"]),
                date.fromisoformat(row["coverage_end"]),
            )
        except (TypeError, ValueError) as exc:
            raise CalendarStoreError("invalid coverage date") from exc
        if (
            start.isoformat() != row["coverage_start"]
            or end.isoformat() != row["coverage_end"]
            or start > end
        ):
            raise CalendarStoreError("invalid coverage interval")
        previous_venue = venue
    binding = _decode(binding_bytes)
    _fields(binding, {"schema", "snapshot_sha256", "manifest_sha256"}, _BINDING)
    if (
        binding["snapshot_sha256"] != _sha(content_bytes)
        or binding["manifest_sha256"] != manifest_sha256
    ):
        raise CalendarStoreError("binding references do not match")
    return manifest, binding


_DIFF_KEYS = {
    "sources": ("id",),
    "trading_days": ("venue", "day"),
    "trading_hours": ("venue", "kind", "product", "contracts", "valid_from", "valid_to"),
    "exceptions": ("venue", "kind", "product", "contract", "trading_day", "action", "window_id"),
    "coverage": ("venue",),
}
_DIFF_FIELDS = {
    "sources": {"id", "description"},
    "trading_days": {"venue", "day", "status", "source_ids"},
    "trading_hours": {
        "venue",
        "kind",
        "product",
        "contracts",
        "valid_from",
        "valid_to",
        "day_windows",
        "night_windows",
        "night_mappings",
        "source_ids",
        "windows_complete",
        "exceptions_complete",
    },
    "exceptions": {
        "venue",
        "kind",
        "product",
        "contract",
        "trading_day",
        "action",
        "window_id",
        "source_ids",
    },
    "coverage": {"venue", "coverage_start", "coverage_end", "semantics"},
}

_DIFF_VENUES = frozenset({"SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX"})
_DIFF_CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z", re.ASCII)


def _diff_id(value: Any) -> bool:
    return type(value) is str and _VENUE.fullmatch(value) is not None


def _diff_date(value: Any) -> date:
    try:
        if type(value) is not str:
            raise ValueError
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError
        return result
    except ValueError as exc:
        raise CalendarStoreError("invalid full difference date") from exc


def _diff_refs(value: Any) -> None:
    if (
        type(value) is not list
        or not value
        or len(value) > 1000
        or any(not _diff_id(item) for item in value)
        or len(set(value)) != len(value)
    ):
        raise CalendarStoreError("invalid full difference references")


def _full_difference_row_domain(layer: str, row: dict[str, Any]) -> None:
    """Match individual current CTP candidate domains, without source attestation."""
    if layer == "sources":
        if (
            not _diff_id(row["id"])
            or not row["description"].strip()
            or len(row["description"]) > 4096
        ):
            raise CalendarStoreError("invalid full difference source")
        return
    if row["venue"] not in _DIFF_VENUES:
        raise CalendarStoreError("invalid full difference venue")
    if layer == "coverage":
        if (
            _diff_date(row["coverage_start"]) > _diff_date(row["coverage_end"])
            or row["semantics"] != "DECLARED_INCLUSIVE"
        ):
            raise CalendarStoreError("invalid full difference coverage")
        return
    _diff_refs(row["source_ids"])
    if layer == "trading_days":
        _diff_date(row["day"])
        if row["status"] not in {"open", "closed"}:
            raise CalendarStoreError("invalid full difference day status")
        return
    if row["kind"] not in {"future", "option"} or not _diff_id(row["product"]):
        raise CalendarStoreError("invalid full difference scope")
    if layer == "exceptions":
        _diff_date(row["trading_day"])
        if (row["contract"] is not None and not _diff_id(row["contract"])) or not (
            (row["action"] == "close_day" and row["window_id"] is None)
            or (row["action"] == "cancel_window" and _diff_id(row["window_id"]))
        ):
            raise CalendarStoreError("invalid full difference exception")
        return
    start, end = _diff_date(row["valid_from"]), _diff_date(row["valid_to"])
    _diff_refs(row["contracts"])
    if start > end:
        raise CalendarStoreError("invalid full difference interval")
    names = set()
    for field in ("day_windows", "night_windows"):
        if len(row[field]) > 1000:
            raise CalendarStoreError("too many full difference windows")
        for window in row[field]:
            name = window["id"]
            offset = window["end_day_offset"]
            first, last = window["start"], window["end"]
            if (
                not _diff_id(name)
                or name in names
                or window["phase"] not in {"continuous", "auction"}
                or offset not in (0, 1)
                or (field == "day_windows" and offset != 0)
                or not _DIFF_CLOCK.fullmatch(first)
                or not _DIFF_CLOCK.fullmatch(last)
                or (offset == 0 and last <= first)
                or (offset == 1 and last >= first)
            ):
                raise CalendarStoreError("invalid full difference window domain")
            names.add(name)
    if len(row["night_mappings"]) > 1000:
        raise CalendarStoreError("too many full difference mappings")
    days = set()
    for mapping in row["night_mappings"]:
        day = _diff_date(mapping["trading_day"])
        local = None if mapping["start_date"] is None else _diff_date(mapping["start_date"])
        _diff_refs(mapping["source_ids"])
        if (
            day in days
            or not start <= day <= end
            or (local is not None and (local >= day or not row["night_windows"]))
        ):
            raise CalendarStoreError("invalid full difference night mapping")
        days.add(day)


def _full_difference_changes(changes: dict[str, Any]) -> None:
    """Validate declaration shape and stable keys, not its factual completeness."""
    for layer, fields in _DIFF_FIELDS.items():
        seen = set()
        for change in changes[layer]:
            if (
                type(change) is not dict
                or set(change) != {"key", "operation", "before", "after"}
                or type(change["key"]) is not list
                or len(change["key"]) != len(_DIFF_KEYS[layer])
            ):
                raise CalendarStoreError("invalid full difference change")
            old, new, operation = change["before"], change["after"], change["operation"]
            if not (
                (operation == "added" and old is None and type(new) is dict)
                or (operation == "removed" and type(old) is dict and new is None)
                or (
                    operation == "modified"
                    and type(old) is dict
                    and type(new) is dict
                    and old != new
                )
            ):
                raise CalendarStoreError("invalid full difference operation")
            for row in (old, new):
                if row is None:
                    continue
                if set(row) != fields:
                    raise CalendarStoreError("invalid full difference row fields")
                for field in fields:
                    value = row[field]
                    if field in {"contracts", "source_ids"}:
                        if type(value) is not list or any(
                            type(item) is not str or not item for item in value
                        ):
                            raise CalendarStoreError("invalid full difference references")
                    elif field in {"day_windows", "night_windows", "night_mappings"}:
                        nested_fields = (
                            {"trading_day", "start_date", "source_ids"}
                            if field == "night_mappings"
                            else {"id", "phase", "start", "end", "end_day_offset"}
                        )
                        if type(value) is not list or any(
                            type(item) is not dict or set(item) != nested_fields for item in value
                        ):
                            raise CalendarStoreError("invalid full difference nested rows")
                        for item in value:
                            if field == "night_mappings":
                                if (
                                    type(item["trading_day"]) is not str
                                    or (
                                        item["start_date"] is not None
                                        and type(item["start_date"]) is not str
                                    )
                                    or type(item["source_ids"]) is not list
                                    or any(type(ref) is not str for ref in item["source_ids"])
                                ):
                                    raise CalendarStoreError("invalid full difference mapping")
                            elif (
                                any(
                                    type(item[k]) is not str
                                    for k in ("id", "phase", "start", "end")
                                )
                                or type(item["end_day_offset"]) is not int
                            ):
                                raise CalendarStoreError("invalid full difference window")
                    elif field in {"windows_complete", "exceptions_complete"}:
                        if type(value) is not bool:
                            raise CalendarStoreError("invalid full difference completeness")
                    elif field in {"contract", "window_id"} and value is None:
                        pass
                    elif type(value) is not str:
                        raise CalendarStoreError("invalid full difference row value")
                if [row[key] for key in _DIFF_KEYS[layer]] != change["key"]:
                    raise CalendarStoreError("full difference key mismatch")
                _full_difference_row_domain(layer, row)
            frozen_key = tuple(
                tuple(value) if type(value) is list else value for value in change["key"]
            )
            if frozen_key in seen:
                raise CalendarStoreError("duplicate full difference key")
            seen.add(frozen_key)


def _policy_evidence(
    value: Any,
    snapshot_bytes: bytes,
    previous: Any,
    *,
    review_required: bool = True,
    full: bool = False,
) -> None:
    if type(value) is not dict or set(value) != {
        "directory_id",
        "policy_revision",
        "mode",
        "mode_source",
        "snapshot_sha256",
        "difference",
        "difference_sha256",
        "reviewer",
        "confirmation_snapshot_sha256",
        "confirmation_difference_sha256",
        "confirmation_policy_revision",
        "baseline",
        "confirmation_current",
        "confirmation_utc",
        "identity_status",
        "source_status",
    }:
        raise CalendarStoreError("invalid policy publication evidence")
    difference = _canonical(value["difference"])
    if (
        len(difference) > (_FULL_DIFFERENCE_BYTES if full else _LEGACY_DIFFERENCE_BYTES)
        or not _identifier(value["directory_id"])
        or not _identifier(value["policy_revision"])
        or value["mode"] not in ("auto", "manual")
        or value["mode_source"] not in ("default", "explicit")
        or (value["mode_source"] == "default" and value["mode"] != "auto")
        or value["snapshot_sha256"] != _sha(snapshot_bytes)
        or value["difference_sha256"] != _sha(difference)
        or value["identity_status"] != "UNAUTHENTICATED"
        or value["source_status"] != "UNVERIFIED"
    ):
        raise CalendarStoreError("invalid policy publication evidence")
    if full:
        diff = value["difference"]
        mixed_diff = (
            type(diff) is dict
            and diff.get("schema") == "bt_api.calendar-full-declaration-difference/2"
        )
        if (
            type(diff) is not dict
            or set(diff)
            != {"schema", "candidate_id", "candidate_payload_sha256", "before", "after", "changes"}
            | ({"simulation"} if mixed_diff else set())
            or diff["schema"]
            != (
                "bt_api.calendar-full-declaration-difference/2"
                if mixed_diff
                else "bt_api.calendar-full-declaration-difference/1"
            )
            or type(diff["candidate_id"]) is not str
            or not _CANDIDATE_ID.fullmatch(diff["candidate_id"])
            or not _digest(diff["candidate_payload_sha256"])
            or type(diff["before"]) is not dict
            or set(diff["before"])
            != {
                "publication_id",
                "publication_sha256",
                "version_id",
                "version_sha256",
                "snapshot_sha256",
                "manifest_sha256",
                "binding_sha256",
            }
            or type(previous) is not dict
            or diff["before"]["publication_id"] != previous.get("publication_id")
            or diff["before"]["publication_sha256"] != previous.get("publication_sha256")
            or not _identifier(diff["before"]["version_id"])
            or any(
                not _digest(diff["before"][k])
                for k in ("version_sha256", "snapshot_sha256", "manifest_sha256", "binding_sha256")
            )
            or type(diff["after"]) is not dict
            or set(diff["after"])
            != {"snapshot_sha256", "store_snapshot_sha256", "manifest_sha256", "binding_sha256"}
            or type(diff["changes"]) is not dict
            or set(diff["changes"])
            != {"sources", "trading_days", "trading_hours", "exceptions", "coverage"}
            or any(type(rows) is not list for rows in diff["changes"].values())
        ):
            raise CalendarStoreError("invalid full difference declaration")
        _full_difference_changes(diff["changes"])
        snapshot = _decode(snapshot_bytes)
        mixed_snapshot = snapshot.get("schema") in {
            _SNAPSHOT_MIXED,
            _SNAPSHOT_MULTIMIX,
            _SNAPSHOT_BATCH,
            _SNAPSHOT_PARTIAL,
        }
        _fields(
            snapshot,
            {"schema", "content", "manifest", "manifest_sha256", "binding", "binding_sha256"}
            | ({"simulation_provenance"} if mixed_snapshot else set()),
            snapshot["schema"] if mixed_snapshot else _SNAPSHOT_COVERED,
        )
        if mixed_diff:
            sim = diff["simulation"]
            if (
                type(sim) is not dict
                or set(sim) != {"before", "after"}
                or sim["after"] != snapshot.get("simulation_provenance")
                or (sim["before"] is None and sim["after"] is None)
            ):
                raise CalendarStoreError("simulation difference mismatch")
        elif mixed_snapshot:
            raise CalendarStoreError("simulation difference required")
        if diff["after"] != {
            "snapshot_sha256": _sha(_content(snapshot["content"])),
            "store_snapshot_sha256": _sha(snapshot_bytes),
            "manifest_sha256": snapshot["manifest_sha256"],
            "binding_sha256": snapshot["binding_sha256"],
        }:
            raise CalendarStoreError("full difference snapshot mismatch")
    baseline = value["baseline"]
    if (
        type(baseline) is not dict
        or set(baseline) != {"mode", "publication_id", "publication_sha256"}
        or not (
            (
                baseline == {"mode": "ABSENT", "publication_id": None, "publication_sha256": None}
                and previous is None
            )
            or (
                baseline["mode"] == "MATCH"
                and _identifier(baseline["publication_id"])
                and _digest(baseline["publication_sha256"])
                and previous
                == {
                    "publication_id": baseline["publication_id"],
                    "publication_sha256": baseline["publication_sha256"],
                }
            )
        )
    ):
        raise CalendarStoreError("publication baseline mismatch")
    review_fields = (
        "reviewer",
        "confirmation_snapshot_sha256",
        "confirmation_difference_sha256",
        "confirmation_policy_revision",
        "confirmation_current",
        "confirmation_utc",
    )
    no_review = all(value[key] is None for key in review_fields)
    if value["mode"] == "manual" and not (no_review and not review_required):
        if (
            not _identifier(value["reviewer"])
            or value["confirmation_snapshot_sha256"] != value["snapshot_sha256"]
            or value["confirmation_difference_sha256"] != value["difference_sha256"]
            or value["confirmation_policy_revision"] != value["policy_revision"]
            or value["confirmation_current"] != baseline
        ):
            raise CalendarStoreError("invalid manual confirmation")
        _parse_utc(value["confirmation_utc"])
    elif value["mode"] == "auto" and not no_review:
        raise CalendarStoreError("auto publication cannot claim manual review")


def calendar_snapshot_digest(
    content: dict[str, Any],
    *,
    coverage_manifest_bytes: bytes | None = None,
    coverage_manifest_sha256: str | None = None,
    binding_bytes: bytes | None = None,
    binding_sha256: str | None = None,
    simulation_provenance: dict[str, Any] | None = None,
) -> str:
    """Digest the exact stored candidate, including any coverage artifacts."""
    return _sha(
        _snapshot_bytes(
            _content(content),
            coverage_manifest_bytes,
            coverage_manifest_sha256,
            binding_bytes,
            binding_sha256,
            simulation_provenance=simulation_provenance,
        )
    )


def _simulation_provenance(content: Any, value: Any) -> None:
    """Validate the exact simulated window attribution carried by snapshot /3."""
    if type(value) is dict and value.get("schema") in {
        "bt_api.calendar-mixed-scopes-provenance/1",
        "bt_api.calendar-mixed-scopes-provenance/2",
        "bt_api.calendar-mixed-scopes-provenance/3",
    }:
        _multi_simulation_provenance(content, value)
        return
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema",
            "environment",
            "alignment",
            "target",
            "windows",
            "evidence_locator",
            "semantic_sha256",
            "evidence_sha256",
        }
        or value["schema"] != "bt_api.calendar-mixed-window-provenance/2"
        or value["environment"] != "SIMULATION_ONLY"
        or value["alignment"] != "REPEAT_OVER_MOCK_OWNER_DAY_MAPPING"
        or not _digest(value["semantic_sha256"])
        or not _digest(value["evidence_sha256"])
    ):
        raise CalendarStoreError("invalid simulation provenance")
    locator = value["evidence_locator"]
    if (
        type(locator) is not dict
        or set(locator)
        != {
            "candidate_id",
            "parent_candidate_id",
            "observation_id",
            "observation_run_id",
            "capture_id",
            "response_digest",
        }
        or any(
            type(locator[key]) is not str or _CANDIDATE_ID.fullmatch(locator[key]) is None
            for key in (
                "candidate_id",
                "parent_candidate_id",
                "observation_id",
                "observation_run_id",
            )
        )
        or type(locator["capture_id"]) is not str
        or re.fullmatch(r"akshare\.capture\.[0-9a-f]{32}", locator["capture_id"], re.ASCII) is None
        or not _digest(locator["response_digest"])
    ):
        raise CalendarStoreError("invalid simulation evidence locator")
    target = value["target"]
    if (
        type(target) is not dict
        or set(target) != {"venue", "kind", "product", "mock_contract", "valid_from", "valid_to"}
        or target["kind"] != "future"
        or type(target["mock_contract"]) is not str
        or not target["mock_contract"].startswith("mock_")
    ):
        raise CalendarStoreError("invalid simulation scope")
    rows = [
        row
        for row in content["trading_hours"]
        if row.get("venue") == target["venue"]
        and row.get("kind") == target["kind"]
        and row.get("product") == target["product"]
        and row.get("contracts") == [target["mock_contract"]]
        and row.get("valid_from") == target["valid_from"]
        and row.get("valid_to") == target["valid_to"]
    ]
    if len(rows) != 1 or type(value["windows"]) is not list:
        raise CalendarStoreError("simulation scope unavailable")
    expected = {
        (scope, window["id"]): window
        for scope, key in (("DAY", "day_windows"), ("NIGHT", "night_windows"))
        for window in rows[0][key]
    }
    actual = set()
    for item in value["windows"]:
        if (
            type(item) is not dict
            or set(item)
            != {"scope", "window_id", "source_type", "fields", "observation", "simulation"}
            or item["scope"] not in {"DAY", "NIGHT"}
            or type(item["window_id"]) is not str
            or item["source_type"] not in {"MOCK", "AKSHARE"}
            or (item["scope"], item["window_id"]) in actual
        ):
            raise CalendarStoreError("invalid simulation window attribution")
        key = (item["scope"], item["window_id"])
        actual.add(key)
        window = expected.get(key)
        if window is None:
            raise CalendarStoreError("unknown simulation window")
        basis = (
            "AKSHARE_SYNTAX_CANDIDATE" if item["source_type"] == "AKSHARE" else "MOCK_ASSUMPTION"
        )
        transition = (
            ("EXPLICIT_NEXT_DAY" if window["end_day_offset"] else "SAME_CLOCK_DAY")
            if item["source_type"] == "AKSHARE"
            else window["end_day_offset"]
        )
        if (
            item["fields"]
            != {
                "scope": {"value": item["scope"], "basis": basis},
                "start": {"value": window["start"], "basis": basis},
                "end": {"value": window["end"], "basis": basis},
                "day_transition": {"value": transition, "basis": basis},
            }
            or item["observation"]
            != dict.fromkeys(
                ("phase", "identity", "effective_period", "trading_day", "coverage"), "UNKNOWN"
            )
            or item["simulation"]
            != {
                "phase": {"value": window["phase"], "basis": "MOCK_ASSUMPTION"},
                "timezone": {"value": "Asia/Shanghai", "basis": "MOCK_ASSUMPTION"},
                "local_date": {"value": "UNKNOWN", "basis": "UNKNOWN"},
                "trading_day": {"value": "UNKNOWN", "basis": "UNKNOWN"},
                "night_ownership": {"value": "UNKNOWN", "basis": "UNKNOWN"},
            }
        ):
            raise CalendarStoreError("simulation window fields mismatch")
    if actual != set(expected) or not any(
        item["source_type"] == "AKSHARE" for item in value["windows"]
    ):
        raise CalendarStoreError("incomplete simulation window attribution")


def _scope_key(target):
    return tuple(
        target[key]
        for key in ("venue", "kind", "product", "mock_contract", "valid_from", "valid_to")
    )


def _multi_simulation_provenance(content, value):
    partial = (
        type(value) is dict and value.get("schema") == "bt_api.calendar-mixed-scopes-provenance/3"
    )
    batch = partial or (
        type(value) is dict and value.get("schema") == "bt_api.calendar-mixed-scopes-provenance/2"
    )
    if (
        type(value) is not dict
        or set(value)
        != (
            {
                "schema",
                "environment",
                "scopes",
                "semantic_sha256",
                "evidence_sha256",
            }
            | ({"update_targets", "batch_record_sha256"} if batch else {"update_target"})
            | ({"resolutions"} if partial else set())
        )
        or value["schema"]
        != (
            "bt_api.calendar-mixed-scopes-provenance/3"
            if partial
            else "bt_api.calendar-mixed-scopes-provenance/2"
            if batch
            else "bt_api.calendar-mixed-scopes-provenance/1"
        )
        or (batch and not _digest(value["batch_record_sha256"]))
        or value["environment"] != "SIMULATION_ONLY"
        or type(value["scopes"]) is not list
        or not (1 if partial else 2) <= len(value["scopes"]) <= 64
    ):
        raise CalendarStoreError("invalid multi-scope simulation provenance")
    scopes = value["scopes"]
    for item in scopes:
        if (
            type(item) is not dict
            or item.get("schema") != "bt_api.calendar-mixed-window-provenance/2"
        ):
            raise CalendarStoreError("invalid mixed scope")
        _simulation_provenance(content, item)
    keys = [_scope_key(item["target"]) for item in scopes]
    targets = value["update_targets"] if batch else [value["update_target"]]
    if partial:
        resolutions = value["resolutions"]
        if type(resolutions) is not list or not resolutions:
            raise CalendarStoreError("invalid batch resolutions")
        if any(
            type(item) is not dict
            or set(item)
            != {
                "target",
                "observation_id",
                "check_status",
                "observation_status",
                "reason",
                "disposition",
            }
            for item in resolutions
        ):
            raise CalendarStoreError("invalid batch resolution")
        resolution_keys = [_scope_key(item["target"]) for item in resolutions]
        if resolution_keys != sorted(resolution_keys) or len(set(resolution_keys)) != len(
            resolution_keys
        ):
            raise CalendarStoreError("invalid batch resolution order")
        for item in resolutions:
            disposition = item["disposition"]
            target = item["target"]
            if (
                not _identifier(item["observation_id"])
                or type(item["reason"]) is not str
                or type(item["observation_status"]) is not str
            ):
                raise CalendarStoreError("invalid batch observation")
            if disposition == "UPDATED_AKSHARE":
                valid = item["check_status"] == "SUCCESS" and target in targets
            elif disposition == "RETAINED_AKSHARE":
                valid = (
                    item["check_status"] == "FAILED"
                    and target not in targets
                    and target in [s["target"] for s in scopes]
                )
            elif disposition == "MOCK_FALLBACK":
                matching = [
                    row
                    for row in content["trading_hours"]
                    if row["venue"] == target["venue"]
                    and row["kind"] == target["kind"]
                    and row["product"] == target["product"]
                    and row["contracts"] == [target["mock_contract"]]
                    and row["valid_from"] == target["valid_from"]
                    and row["valid_to"] == target["valid_to"]
                ]
                valid = (
                    item["check_status"] == "FAILED"
                    and target not in [s["target"] for s in scopes]
                    and len(matching) == 1
                    and matching[0]["source_ids"] == ["MOCK.simulation_only"]
                )
            else:
                valid = False
            if not valid:
                raise CalendarStoreError("invalid batch disposition")
        if (
            sorted(
                [
                    item["target"]
                    for item in resolutions
                    if item["disposition"] == "UPDATED_AKSHARE"
                ],
                key=_scope_key,
            )
            != targets
        ):
            raise CalendarStoreError("batch updates do not match resolutions")
    if (
        type(targets) is not list
        or not targets
        or any(item not in [scope["target"] for scope in scopes] for item in targets)
        or len({_scope_key(item) for item in targets}) != len(targets)
        or (batch and targets != sorted(targets, key=_scope_key))
        or keys != sorted(keys)
        or len(set(keys)) != len(keys)
    ):
        raise CalendarStoreError("duplicate or unordered mixed scope")
    for i, left in enumerate(keys):
        for right in keys[i + 1 :]:
            if (
                left[0:2] == right[0:2]
                and left[3] == right[3]
                and left[4] <= right[5]
                and right[4] <= left[5]
            ):
                raise CalendarStoreError("overlapping mixed scope")
    semantic = [
        {"target": item["target"], "semantic_sha256": item["semantic_sha256"]} for item in scopes
    ]
    if value["semantic_sha256"] != _sha(_canonical(semantic)) or value["evidence_sha256"] != _sha(
        _canonical(scopes)
    ):
        raise CalendarStoreError("mixed scope digest mismatch")


def _snapshot_bytes(content_bytes: bytes, *artifacts: Any, simulation_provenance=None) -> bytes:
    if any(item is not None for item in artifacts):
        manifest, binding = _coverage_bundle(content_bytes, *artifacts)
        content = json.loads(content_bytes)
        value = {
            "schema": _SNAPSHOT_COVERED,
            "content": content,
            "manifest": manifest,
            "manifest_sha256": artifacts[1],
            "binding": binding,
            "binding_sha256": artifacts[3],
        }
        if simulation_provenance is not None:
            _simulation_provenance(content, simulation_provenance)
            value["schema"] = (
                _SNAPSHOT_PARTIAL
                if simulation_provenance.get("schema")
                == "bt_api.calendar-mixed-scopes-provenance/3"
                else _SNAPSHOT_BATCH
                if simulation_provenance.get("schema")
                == "bt_api.calendar-mixed-scopes-provenance/2"
                else _SNAPSHOT_MULTIMIX
                if simulation_provenance.get("schema")
                == "bt_api.calendar-mixed-scopes-provenance/1"
                else _SNAPSHOT_MIXED
            )
            value["simulation_provenance"] = simulation_provenance
        return _canonical(value)
    if simulation_provenance is not None:
        raise CalendarStoreError("simulation provenance requires coverage")
    return _canonical({"schema": _SNAPSHOT, "content": json.loads(content_bytes)})


def _decode_version(event: dict[str, Any], snapshot_bytes: bytes) -> CalendarSnapshot:
    snapshot = _decode(snapshot_bytes)
    content_bytes = _content(snapshot.get("content"))
    if event["schema"] in _POLICY_EVENTS:
        _policy_evidence(
            event["review_policy"],
            snapshot_bytes,
            event["previous"],
            full=event["schema"] == _EVENT_POLICY_FULL,
        )
    if _sha(content_bytes) != event["content_sha256"]:
        raise CalendarStoreError("content digest mismatch")
    if snapshot.get("schema") == _SNAPSHOT:
        _fields(snapshot, {"schema", "content"}, _SNAPSHOT)
        if event["schema"] in _COVERED_EVENTS:
            raise CalendarStoreError("covered event cannot reference legacy version")
        return CalendarSnapshot(
            event["publication_id"],
            event["sequence"],
            event["version_id"],
            event["content_sha256"],
            snapshot["content"],
        )
    mixed = snapshot.get("schema") in {
        _SNAPSHOT_MIXED,
        _SNAPSHOT_MULTIMIX,
        _SNAPSHOT_BATCH,
        _SNAPSHOT_PARTIAL,
    }
    _fields(
        snapshot,
        {"schema", "content", "manifest", "manifest_sha256", "binding", "binding_sha256"}
        | ({"simulation_provenance"} if mixed else set()),
        snapshot["schema"] if mixed else _SNAPSHOT_COVERED,
    )
    if mixed:
        _simulation_provenance(snapshot["content"], snapshot["simulation_provenance"])
        if (snapshot["schema"] == _SNAPSHOT_PARTIAL) != (
            snapshot["simulation_provenance"].get("schema")
            == "bt_api.calendar-mixed-scopes-provenance/3"
        ):
            raise CalendarStoreError("partial provenance schema mismatch")
    if event["schema"] not in _COVERED_EVENTS or event["version_sha256"] != _sha(snapshot_bytes):
        raise CalendarStoreError("covered version is not bound to publication")
    manifest_bytes, binding_bytes = (
        _canonical(snapshot["manifest"]),
        _canonical(snapshot["binding"]),
    )
    _coverage_bundle(
        content_bytes,
        manifest_bytes,
        snapshot["manifest_sha256"],
        binding_bytes,
        snapshot["binding_sha256"],
    )
    return CalendarSnapshot(
        event["publication_id"],
        event["sequence"],
        event["version_id"],
        event["content_sha256"],
        snapshot["content"],
        "DECLARED_UNVERIFIED",
        manifest_bytes,
        snapshot["manifest_sha256"],
        binding_bytes,
        snapshot["binding_sha256"],
        event["version_sha256"],
        simulation_provenance=snapshot["simulation_provenance"] if mixed else None,
    )


class TradingCalendarStore:
    """Explicit local storage API, no background tasks and no network access.

    ``publish`` requires caller-stable IDs, and never retries a failed operation.
    A prewritten intent blocks all publications until the same ID is reconciled
    against CURRENT and the complete chain. A corrupt or unprovable intent is
    deliberately not cleared automatically. ``verify_publication`` takes the
    writer lock but does not publish; only ``reconcile_publication`` can remove
    the intent after recovery.
    Returned content is detached mutable JSON; disk snapshots are never overwritten.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        clock_utc: Callable[[], datetime] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
        utc_error_bound: timedelta | None = None,
    ):
        self.root = Path(root)
        if not self.root.is_absolute() or ".." in self.root.parts:
            raise CalendarStoreError("store root must be absolute")
        self._clock_utc = clock_utc if clock_utc is not None else lambda: datetime.now(UTC)
        self._monotonic_ns = monotonic_ns if monotonic_ns is not None else time.monotonic_ns
        if utc_error_bound is None:
            self._utc_error_bound_us = None
        elif type(utc_error_bound) is timedelta and timedelta(0) <= utc_error_bound <= timedelta(
            seconds=60
        ):
            self._utc_error_bound_us = (
                utc_error_bound.days * 86400 + utc_error_bound.seconds
            ) * 1_000_000 + utc_error_bound.microseconds
        else:
            raise CalendarStoreError("UTC error bound must be between zero and sixty seconds")

    def _clock_sample(self) -> tuple[str | None, int | None, str | None, int | None]:
        try:
            boot_id = _system_boot_identity()
            before = self._monotonic_ns()
            wall = self._clock_utc()
            after = self._monotonic_ns()
            if (
                type(before) is not int
                or type(after) is not int
                or before < 0
                or after < before
                or type(wall) is not datetime
                or wall.tzinfo is None
                or wall.utcoffset() != timedelta(0)
            ):
                return None, None, boot_id, self._utc_error_bound_us
            return (
                wall.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
                after,
                boot_id,
                self._utc_error_bound_us,
            )
        except Exception:
            return None, None, None, self._utc_error_bound_us

    def _directory(self, path: Path, *, create: bool = False) -> None:
        if create:
            path.mkdir(mode=0o700, parents=False, exist_ok=True)
        if stat.S_ISLNK(path.lstat().st_mode) or not path.is_dir():
            raise CalendarStoreError("store path is not a real directory")

    def _layout(self, *, create: bool = False) -> None:
        self._directory(self.root, create=create)
        for name in ("versions", "publications", "receipts"):
            self._directory(self.root / name, create=create)

    def _read(self, path: Path) -> bytes:
        if not hasattr(os, "O_NOFOLLOW"):
            raise CalendarStoreError("O_NOFOLLOW required for safe reads")
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise CalendarStoreError("store entry is not a regular file")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                return stream.read()
        finally:
            os.close(fd)

    def _read_bounded(self, path: Path, limit: int) -> bytes:
        """Read a stable regular file, rejecting symlinks and overlarge inputs."""
        if not hasattr(os, "O_NOFOLLOW"):
            raise CalendarStoreError("O_NOFOLLOW required for safe reads")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise CalendarStoreError("policy file invalid or too large")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(limit + 1)
            after = os.fstat(fd)
            if (
                len(data) > limit
                or len(data) != before.st_size
                or (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            ):
                raise CalendarStoreError("policy file changed during read")
            return data
        finally:
            os.close(fd)

    def _write_new(self, path: Path, data: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)

    def _pending_exists(self) -> bool:
        return self._entry_exists(self.root / "PENDING.json")

    def _pending_owner(self) -> str:
        intent = _decode(self._read(self.root / "PENDING.json"))
        _fields(intent, {"schema", "publication_id", "version_id", "pointer"}, _INTENT)
        pointer = intent["pointer"]
        if (
            not _identifier(intent["publication_id"])
            or not _identifier(intent["version_id"])
            or type(pointer) is not dict
        ):
            raise CalendarStoreError("invalid pending intent")
        _fields(pointer, {"schema", "publication_id", "publication_sha256"}, _POINTER)
        if pointer["publication_id"] != intent["publication_id"] or not _digest(
            pointer["publication_sha256"]
        ):
            raise CalendarStoreError("invalid pending pointer")
        return intent["publication_id"]

    def _entry_exists(self, path: Path) -> bool:
        try:
            path.lstat()
            return True
        except FileNotFoundError:
            return False

    def _clear_pending(self) -> None:
        (self.root / "PENDING.json").unlink()
        if self._pending_exists():
            raise CalendarStoreError("pending intent still present after unlink")

    @contextlib.contextmanager
    def _writer_lock(self):
        if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
            raise CalendarStoreError("POSIX flock and O_NOFOLLOW required")
        self._directory(self.root, create=True)
        fd = os.open(self.root / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        locked = False
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise CalendarStoreError("invalid lock file")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            self._layout(create=True)
            yield
        finally:
            try:
                if locked:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @contextlib.contextmanager
    def _readonly_lock(self):
        """Inspect an existing store without creating its lock or layout."""
        if fcntl is None or not hasattr(os, "O_NOFOLLOW"):
            raise CalendarStoreError("POSIX flock and O_NOFOLLOW required")
        self._directory(self.root)
        fd = os.open(self.root / ".lock", os.O_RDONLY | os.O_NOFOLLOW)
        locked = False
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise CalendarStoreError("invalid lock file")
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            locked = True
            self._layout()
            yield
        finally:
            try:
                if locked:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _review_policy_locked(self) -> ReviewPolicy | None:
        marker_path = self.root / "REVIEW_POLICY_INITIALIZED.json"
        policy_path = self.root / "REVIEW_POLICY.json"
        marker_exists, policy_exists = (
            self._entry_exists(marker_path),
            self._entry_exists(policy_path),
        )
        lock_marker = self._read_bounded(self.root / ".lock", _POLICY_MAX_RECORD_BYTES)
        if not marker_exists and not policy_exists and lock_marker == b"":
            self._policy_records_locked([])
            return None
        if not marker_exists or not policy_exists or not lock_marker:
            raise CalendarStoreError("review policy state incomplete")
        marker_bytes = self._read_bounded(marker_path, _POLICY_MAX_RECORD_BYTES)
        if lock_marker != marker_bytes:
            raise CalendarStoreError("review policy lock marker mismatch")
        marker, policy = (
            _decode(marker_bytes),
            _decode(self._read_bounded(policy_path, _POLICY_MAX_RECORD_BYTES)),
        )
        _fields(marker, {"schema", "directory_id"}, _POLICY_MARKER)
        current = _policy_value(policy)
        if (
            not _identifier(marker["directory_id"])
            or marker["directory_id"] != current.directory_id
        ):
            raise CalendarStoreError("review policy invalid")
        if policy["schema"] == _POLICY and self._entry_exists(
            self.root / "POLICY_AUDIT_INITIALIZED.json"
        ):
            raise CalendarStoreError("policy audit history cannot be downgraded")
        chain = self._policy_chain_locked(policy)
        self._policy_records_locked(chain)
        return current

    def _policy_change_pending(self) -> bool:
        return self._entry_exists(self.root / "POLICY_CHANGE_PENDING.json")

    def _policy_change_path(self, operation_id: str) -> Path:
        if not _identifier(operation_id):
            raise CalendarStoreError("invalid policy operation ID")
        return self.root / "policy_changes" / f"{operation_id}.json"

    def _policy_chain_locked(self, policy: dict[str, Any]) -> list[dict[str, Any]]:
        """Validate each hash-bound record back to an unaudited /1 anchor."""
        directory_id = _policy_value(policy).directory_id
        if policy["schema"] == _POLICY_AUDITED:
            self._policy_audit_marker_locked(directory_id)
        seen, entries, total = set(), [], 0
        while policy["schema"] == _POLICY_AUDITED:
            ref = policy["last_change"]
            if ref["operation_id"] in seen or len(seen) >= _POLICY_MAX_RECORDS:
                raise CalendarStoreError("invalid policy change chain")
            seen.add(ref["operation_id"])
            self._directory(self.root / "policy_changes")
            raw = self._read_bounded(
                self._policy_change_path(ref["operation_id"]), _POLICY_MAX_RECORD_BYTES
            )
            total += len(raw)
            if total > _POLICY_MAX_TOTAL_BYTES:
                raise CalendarStoreError("policy change chain too large")
            if _sha(raw) != ref["sha256"]:
                raise CalendarStoreError("policy change digest mismatch")
            record = _decode(raw)
            _change_value(record)
            if (
                record["operation_id"] != ref["operation_id"]
                or record["directory_id"] != directory_id
                or _changed_policy(record) != policy
            ):
                raise CalendarStoreError("policy change binding mismatch")
            entries.append(record)
            policy = record["old_policy"]
            if _policy_value(policy).directory_id != directory_id:
                raise CalendarStoreError("policy change directory mismatch")
        return entries

    def _policy_audit_marker_locked(self, directory_id: str, *, create: bool = False) -> None:
        path = self.root / "POLICY_AUDIT_INITIALIZED.json"
        marker = _canonical({"schema": _POLICY_AUDIT_MARKER, "directory_id": directory_id})
        if create and not self._entry_exists(path):
            self._write_new(path, marker)
        if self._read_bounded(path, _POLICY_MAX_RECORD_BYTES) != marker:
            raise CalendarStoreError("policy audit marker mismatch")

    def _policy_records_locked(
        self, chain: list[dict[str, Any]], *, pending_record: dict[str, Any] | None = None
    ) -> None:
        """Reject orphans; permit only the exact pending record during recovery."""
        expected = {item["operation_id"]: _canonical(item) for item in chain}
        pending_id = None if pending_record is None else pending_record["operation_id"]
        if pending_id in expected:
            raise CalendarStoreError("pending policy ID already exists in prior chain")
        path = self.root / "policy_changes"
        if not self._entry_exists(path):
            if expected:
                raise CalendarStoreError("policy change directory missing")
            return
        self._directory(path)
        found, count, total = set(), 0, 0
        with os.scandir(path) as entries:
            for entry in entries:
                count += 1
                if count > _POLICY_MAX_RECORDS:
                    raise CalendarStoreError("too many policy change records")
                name = entry.name
                if (
                    not name.endswith(".json")
                    or not _identifier(name[:-5])
                    or not entry.is_file(follow_symlinks=False)
                ):
                    raise CalendarStoreError("unrecognized policy change entry")
                operation_id = name[:-5]
                if operation_id not in expected and operation_id != pending_id:
                    raise CalendarStoreError("policy change record outside current chain")
                raw = self._read_bounded(path / name, _POLICY_MAX_RECORD_BYTES)
                total += len(raw)
                if total > _POLICY_MAX_TOTAL_BYTES:
                    raise CalendarStoreError("policy change records too large")
                record = _decode(raw)
                _change_value(record)
                intended = (
                    expected.get(operation_id)
                    if operation_id in expected
                    else _canonical(pending_record)
                )
                if record["operation_id"] != operation_id or raw != intended:
                    raise CalendarStoreError("policy change record binding mismatch")
                found.add(operation_id)
        if not set(expected) <= found:
            raise CalendarStoreError("policy change record missing")

    def initialize_review_policy(
        self,
        *,
        config_read_succeeded: bool,
        mode: str | None = None,
        require_empty_store: bool = False,
    ) -> ReviewPolicy:
        """Initialize once; None means omitted in a successfully read config.

        Configuration provenance is a caller declaration, not authenticated.
        A failed initialization may leave a marker and is deliberately fail-closed.
        """
        if (
            config_read_succeeded is not True
            or mode not in (None, "auto", "manual")
            or type(require_empty_store) is not bool
        ):
            raise CalendarStoreError("invalid or unreadable policy configuration")
        with self._writer_lock():
            if require_empty_store and (
                self._entry_exists(self.root / "CURRENT.json")
                or any(
                    any((self.root / name).iterdir())
                    for name in ("versions", "publications", "receipts")
                )
            ):
                raise CalendarStoreError("store not empty")
            if self._pending_exists() or self._policy_change_pending():
                raise CalendarStoreError("pending publication requires reconciliation")
            if self._review_policy_locked() is not None:
                raise CalendarStoreError("review policy already initialized")
            try:
                pointer_bytes = self._read(self.root / "CURRENT.json")
            except FileNotFoundError:
                pass
            else:
                self._load(pointer_bytes)
                if self._contains_policy_history(pointer_bytes):
                    raise CalendarStoreError("managed publication history without policy")
            directory_id, revision = uuid.uuid4().hex, uuid.uuid4().hex
            policy = ReviewPolicy(
                directory_id, revision, mode or "auto", "default" if mode is None else "explicit"
            )
            marker_bytes = _canonical({"schema": _POLICY_MARKER, "directory_id": directory_id})
            # An empty old lock file denotes an unmanaged legacy directory. The
            # marker is written first, under the already held lock; a partial
            # write remains a blocking unknown instead of defaulting to auto.
            fd = os.open(self.root / ".lock", os.O_WRONLY | os.O_NOFOLLOW)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_size != 0:
                    raise CalendarStoreError("review policy lock marker already present")
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write(marker_bytes)
                    stream.flush()
                    os.fsync(fd)
            finally:
                os.close(fd)
            self._write_new(
                self.root / "REVIEW_POLICY_INITIALIZED.json",
                marker_bytes,
            )
            self._write_new(
                self.root / "REVIEW_POLICY.json",
                _canonical({"schema": _POLICY, **policy.__dict__}),
            )
            if self._review_policy_locked() != policy:
                raise CalendarStoreError("review policy readback mismatch")
            return policy

    def read_review_policy(self) -> ReviewPolicy:
        with self._writer_lock():
            if self._policy_change_pending():
                raise CalendarStoreError("pending policy change requires reconciliation")
            policy = self._review_policy_locked()
            if policy is None:
                raise CalendarStoreError("review policy not initialized")
            return policy

    def _policy_change_intent_locked(
        self, operation_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        intent = _decode(
            self._read_bounded(self.root / "POLICY_CHANGE_PENDING.json", _POLICY_MAX_INTENT_BYTES)
        )
        _fields(intent, {"schema", "record", "new_policy"}, _POLICY_CHANGE_INTENT)
        record, new_policy = intent["record"], intent["new_policy"]
        _change_value(record)
        if record["operation_id"] != operation_id or _changed_policy(record) != new_policy:
            raise CalendarStoreError("policy change intent binding mismatch")
        marker = self._read_bounded(
            self.root / "REVIEW_POLICY_INITIALIZED.json", _POLICY_MAX_RECORD_BYTES
        )
        if marker != self._read_bounded(self.root / ".lock", _POLICY_MAX_RECORD_BYTES):
            raise CalendarStoreError("review policy lock marker mismatch")
        decoded_marker = _decode(marker)
        _fields(decoded_marker, {"schema", "directory_id"}, _POLICY_MARKER)
        if decoded_marker["directory_id"] != record["directory_id"]:
            raise CalendarStoreError("policy change directory mismatch")
        chain = self._policy_chain_locked(record["old_policy"])
        self._policy_records_locked(chain, pending_record=record)
        return record, new_policy

    def _complete_policy_change_locked(
        self, record: dict[str, Any], new_policy: dict[str, Any]
    ) -> ReviewPolicy:
        current_bytes = self._read_bounded(
            self.root / "REVIEW_POLICY.json", _POLICY_MAX_RECORD_BYTES
        )
        old_bytes, new_bytes = _canonical(record["old_policy"]), _canonical(new_policy)
        record_path = self._policy_change_path(record["operation_id"])
        record_bytes = _canonical(record)
        self._policy_audit_marker_locked(record["directory_id"], create=True)
        if current_bytes == old_bytes:
            if self._entry_exists(record_path):
                raise CalendarStoreError("record exists before policy switch")
            temporary = self.root / f".REVIEW_POLICY-{uuid.uuid4().hex}.tmp"
            try:
                self._write_new(temporary, new_bytes)
                os.replace(temporary, self.root / "REVIEW_POLICY.json")
            finally:
                with contextlib.suppress(OSError):
                    temporary.unlink(missing_ok=True)
        elif current_bytes != new_bytes:
            raise CalendarStoreError("policy change current state unprovable")
        if (
            self._read_bounded(self.root / "REVIEW_POLICY.json", _POLICY_MAX_RECORD_BYTES)
            != new_bytes
        ):
            raise CalendarStoreError("policy change readback mismatch")
        if self._entry_exists(record_path):
            if self._read_bounded(record_path, _POLICY_MAX_RECORD_BYTES) != record_bytes:
                raise CalendarStoreError("policy change record conflict")
        else:
            self._write_new(record_path, record_bytes)
            if self._read_bounded(record_path, _POLICY_MAX_RECORD_BYTES) != record_bytes:
                raise CalendarStoreError("policy change record readback mismatch")
        policy = self._review_policy_locked()
        if policy != _policy_value(new_policy):
            raise CalendarStoreError("policy change proof mismatch")
        (self.root / "POLICY_CHANGE_PENDING.json").unlink()
        if self._policy_change_pending():
            raise CalendarStoreError("policy change intent remains")
        return policy

    def change_review_policy(
        self,
        *,
        expected_revision: str,
        mode: str,
        operation_id: str,
        actor: str,
        at_utc: str,
        prepare_only: bool = False,
        locked_guard: Callable[[ReviewPolicy], None] | None = None,
        reject_existing: bool = False,
        mode_source: str | None = None,
    ) -> PolicyChangeResult:
        """Attempt an auditable CAS switch; identity and time are declarations only."""
        if (
            not _identifier(expected_revision)
            or mode not in ("auto", "manual")
            or not _identifier(operation_id)
            or not _actor(actor)
            or type(prepare_only) is not bool
            or type(reject_existing) is not bool
            or mode_source not in (None, "default", "explicit")
            or (mode_source == "default" and mode != "auto")
            or (locked_guard is not None and not callable(locked_guard))
        ):
            return PolicyChangeResult(
                "NOT_COMMITTED", operation_id, detail="invalid change request"
            )
        try:
            _parse_utc(at_utc)
        except CalendarStoreError:
            return PolicyChangeResult(
                "NOT_COMMITTED", operation_id, detail="invalid change request"
            )
        try:
            with self._writer_lock():
                if self._pending_exists():
                    return PolicyChangeResult(
                        "COMMIT_UNKNOWN",
                        operation_id,
                        detail="pending intent requires reconciliation",
                    )
                if self._policy_change_pending():
                    try:
                        pending, _ = self._policy_change_intent_locked(operation_id)
                    except (OSError, CalendarStoreError, ValueError, RecursionError):
                        pass
                    else:
                        if (
                            pending["old_policy"]["revision"] != expected_revision
                            or pending["new_mode"] != mode
                            or pending.get("new_mode_source", "explicit")
                            != (mode_source or "explicit")
                            or pending["actor"] != actor
                            or pending["at_utc"] != at_utc
                        ):
                            return PolicyChangeResult(
                                "ID_CONFLICT",
                                operation_id,
                                detail="operation ID conflicts with pending intent",
                            )
                    return PolicyChangeResult(
                        "COMMIT_UNKNOWN",
                        operation_id,
                        detail="pending intent requires reconciliation",
                    )
                policy = self._review_policy_locked()
                if policy is None:
                    return PolicyChangeResult(
                        "NOT_COMMITTED", operation_id, detail="policy not initialized"
                    )
                policy_data = _decode(
                    self._read_bounded(self.root / "REVIEW_POLICY.json", _POLICY_MAX_RECORD_BYTES)
                )
                chain = self._policy_chain_locked(policy_data)
                prior = next((item for item in chain if item["operation_id"] == operation_id), None)
                if prior is not None:
                    if reject_existing:
                        return PolicyChangeResult(
                            "NOT_COMMITTED",
                            operation_id,
                            detail="original ID requires reconciliation",
                        )
                    matches = (
                        prior["old_policy"]["revision"] == expected_revision
                        and prior["new_mode"] == mode
                        and prior.get("new_mode_source", "explicit") == (mode_source or "explicit")
                        and prior["actor"] == actor
                        and prior["at_utc"] == at_utc
                    )
                    if not matches:
                        return PolicyChangeResult(
                            "ID_CONFLICT", operation_id, detail="operation ID conflicts"
                        )
                    result = _policy_value(_changed_policy(prior))
                    return PolicyChangeResult(
                        "COMMITTED" if chain[0] is prior else "HISTORICAL_COMMITTED",
                        operation_id,
                        result,
                    )
                if self._entry_exists(self._policy_change_path(operation_id)):
                    return PolicyChangeResult(
                        "COMMIT_UNKNOWN",
                        operation_id,
                        detail="operation record outside policy chain",
                    )
                if policy.revision != expected_revision:
                    return PolicyChangeResult(
                        "NOT_COMMITTED", operation_id, detail="policy precondition mismatch"
                    )
                if locked_guard is not None:
                    locked_guard(policy)
                try:
                    self._directory(self.root / "policy_changes", create=True)
                    record = {
                        "schema": _POLICY_CHANGE_V2 if mode_source is not None else _POLICY_CHANGE,
                        "operation_id": operation_id,
                        "directory_id": policy.directory_id,
                        "old_policy": policy_data,
                        "new_revision": uuid.uuid4().hex,
                        "new_mode": mode,
                        "actor": actor,
                        "at_utc": at_utc,
                        "identity_status": "UNAUTHENTICATED",
                        "clock_status": "UNVERIFIED",
                        "prior_history": "UNKNOWN"
                        if policy_data["schema"] == _POLICY
                        else "CHAINED",
                    }
                    if mode_source is not None:
                        record["new_mode_source"] = mode_source
                    new_policy = _changed_policy(record)
                    intent = _canonical(
                        {
                            "schema": _POLICY_CHANGE_INTENT,
                            "record": record,
                            "new_policy": new_policy,
                        }
                    )
                    if (
                        len(chain) >= _POLICY_MAX_RECORDS
                        or len(_canonical(record)) > _POLICY_MAX_RECORD_BYTES
                        or sum(len(_canonical(item)) for item in chain) + len(_canonical(record))
                        > _POLICY_MAX_TOTAL_BYTES
                        or len(intent) > _POLICY_MAX_INTENT_BYTES
                    ):
                        return PolicyChangeResult(
                            "NOT_COMMITTED", operation_id, detail="policy audit capacity exceeded"
                        )
                    self._write_new(self.root / "POLICY_CHANGE_PENDING.json", intent)
                    if (
                        self._read_bounded(
                            self.root / "POLICY_CHANGE_PENDING.json", _POLICY_MAX_INTENT_BYTES
                        )
                        != intent
                    ):
                        raise CalendarStoreError("policy change intent readback mismatch")
                    record, new_policy = self._policy_change_intent_locked(operation_id)
                    if prepare_only:
                        return PolicyChangeResult("PENDING_CONFIG", operation_id, policy)
                    return PolicyChangeResult(
                        "COMMITTED",
                        operation_id,
                        self._complete_policy_change_locked(record, new_policy),
                    )
                except (OSError, CalendarStoreError, ValueError, RecursionError):
                    # Still under the same writer lock: an absent intent and
                    # record and an unchanged, revalidated policy prove this
                    # particular attempted switch had no visible effect.
                    try:
                        if (
                            not self._pending_exists()
                            and not self._policy_change_pending()
                            and not self._entry_exists(self._policy_change_path(operation_id))
                            and self._read_bounded(
                                self.root / "REVIEW_POLICY.json", _POLICY_MAX_RECORD_BYTES
                            )
                            == _canonical(policy_data)
                            and self._review_policy_locked() == policy
                        ):
                            return PolicyChangeResult(
                                "NOT_COMMITTED", operation_id, detail="intent was not created"
                            )
                    except (OSError, CalendarStoreError, ValueError, RecursionError):
                        pass
                    return PolicyChangeResult(
                        "COMMIT_UNKNOWN",
                        operation_id,
                        detail="policy change unavailable; reconcile original operation ID",
                    )
        except (OSError, CalendarStoreError, ValueError, RecursionError):
            return PolicyChangeResult(
                "COMMIT_UNKNOWN",
                operation_id,
                detail="policy change unavailable; reconcile original operation ID",
            )

    def reconcile_review_policy_change(
        self,
        operation_id: str,
        *,
        locked_guard: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
    ) -> PolicyChangeResult:
        """Recover only the exact persisted intent, or verify an existing chain record."""
        if not _identifier(operation_id) or (
            locked_guard is not None and not callable(locked_guard)
        ):
            return PolicyChangeResult("COMMIT_UNKNOWN", operation_id, detail="invalid operation ID")
        try:
            with self._writer_lock():
                if self._pending_exists():
                    return PolicyChangeResult(
                        "COMMIT_UNKNOWN", operation_id, detail="pending publication"
                    )
                if self._policy_change_pending():
                    record, new_policy = self._policy_change_intent_locked(operation_id)
                    if locked_guard is not None:
                        locked_guard(record, new_policy)
                    self._directory(self.root / "policy_changes")
                    return PolicyChangeResult(
                        "COMMITTED",
                        operation_id,
                        self._complete_policy_change_locked(record, new_policy),
                    )
                policy = self._review_policy_locked()
                if policy is None:
                    return PolicyChangeResult(
                        "COMMIT_UNKNOWN", operation_id, detail="policy unavailable"
                    )
                chain = self._policy_chain_locked(
                    _decode(
                        self._read_bounded(
                            self.root / "REVIEW_POLICY.json", _POLICY_MAX_RECORD_BYTES
                        )
                    )
                )
                prior = next((item for item in chain if item["operation_id"] == operation_id), None)
                if prior is None:
                    return PolicyChangeResult(
                        "COMMIT_UNKNOWN",
                        operation_id,
                        detail="operation not proved in policy chain",
                    )
                if locked_guard is not None:
                    locked_guard(prior, _changed_policy(prior))
                return PolicyChangeResult(
                    "COMMITTED" if chain[0] is prior else "HISTORICAL_COMMITTED",
                    operation_id,
                    _policy_value(_changed_policy(prior)),
                )
        except (OSError, CalendarStoreError, ValueError, RecursionError):
            return PolicyChangeResult(
                "COMMIT_UNKNOWN", operation_id, detail="policy change evidence unavailable"
            )

    def read_policy_change(self, operation_id: str) -> dict[str, Any]:
        """Return a chain-bound record; identity and timestamp remain unverified."""
        if not _identifier(operation_id):
            raise CalendarStoreError("invalid policy operation ID")
        with self._writer_lock():
            if self._policy_change_pending():
                raise CalendarStoreError("pending policy change requires reconciliation")
            if self._review_policy_locked() is None:
                raise CalendarStoreError("policy unavailable")
            chain = self._policy_chain_locked(
                _decode(
                    self._read_bounded(self.root / "REVIEW_POLICY.json", _POLICY_MAX_RECORD_BYTES)
                )
            )
            record = next((item for item in chain if item["operation_id"] == operation_id), None)
            if record is None:
                raise CalendarStoreError("policy change is not in current chain")
            return record

    def _load(
        self,
        pointer_bytes: bytes,
        *,
        target: dict[str, Any] | None = None,
        target_version: str | None = None,
        _reader: Callable[[Path], bytes] | None = None,
        _max_publications: int | None = None,
    ) -> CalendarSnapshot:
        read = self._read if _reader is None else _reader
        if target is not None:
            _fields(target, {"schema", "publication_id", "publication_sha256"}, _POINTER)
            if not _identifier(target["publication_id"]) or not _digest(
                target["publication_sha256"]
            ):
                raise CalendarStoreError("invalid target reference")
        pointer = _decode(pointer_bytes)
        _fields(pointer, {"schema", "publication_id", "publication_sha256"}, _POINTER)
        event_id, expected_hash = pointer["publication_id"], pointer["publication_sha256"]
        if not _identifier(event_id) or not _digest(expected_hash):
            raise CalendarStoreError("invalid current reference")
        seen: set[str] = set()
        latest = None
        found_target = False
        expected_sequence = None
        expected_full_before = None
        while event_id is not None:
            if event_id in seen:
                raise CalendarStoreError("publication cycle")
            seen.add(event_id)
            if _max_publications is not None and len(seen) > _max_publications:
                raise CalendarStoreError("publication read budget exceeded")
            data = read(self.root / "publications" / f"{event_id}.json")
            if _sha(data) != expected_hash:
                raise CalendarStoreError("publication digest mismatch")
            event = _decode(data)
            if event.get("schema") == _EVENT_POLICY_FULL and len(data) > _PUBLICATION_EVENT_BYTES:
                raise CalendarStoreError("publication event too large")
            event_keys = {
                "schema",
                "publication_id",
                "sequence",
                "previous",
                "version_id",
                "content_sha256",
            }
            if event.get("schema") in _TIMED_EVENTS:
                event_keys |= {"lower_utc", "monotonic_ns", "boot_id", "utc_bound_us"}
                if event["schema"] in _COVERED_EVENTS:
                    event_keys.add("version_sha256")
                if event["schema"] in _POLICY_EVENTS:
                    event_keys.add("review_policy")
                _fields(event, event_keys, event["schema"])
                _validate_optional_sample(
                    event["lower_utc"],
                    event["monotonic_ns"],
                    event["boot_id"],
                    event["utc_bound_us"],
                )
            else:
                _fields(event, event_keys, _EVENT)
            sequence = event["sequence"]
            if (
                event["publication_id"] != event_id
                or type(sequence) is not int
                or sequence < 1
                or (expected_sequence is not None and sequence != expected_sequence)
                or not _identifier(event["version_id"])
                or not _digest(event["content_sha256"])
                or (event["schema"] in _COVERED_EVENTS and not _digest(event["version_sha256"]))
            ):
                raise CalendarStoreError("invalid publication")
            if target is not None and event_id == target["publication_id"]:
                if (
                    expected_hash != target["publication_sha256"]
                    or event["version_id"] != target_version
                ):
                    raise CalendarStoreError("target publication does not match receipt")
                found_target = True
            snapshot_bytes = read(self.root / "versions" / f"{event['version_id']}.json")
            snapshot = _decode_version(event, snapshot_bytes)
            if expected_full_before is not None and expected_full_before != {
                "publication_id": event_id,
                "publication_sha256": expected_hash,
                "version_id": snapshot.version_id,
                "version_sha256": snapshot.version_sha256,
                "snapshot_sha256": snapshot.content_sha256,
                "manifest_sha256": snapshot.coverage_manifest_sha256,
                "binding_sha256": snapshot.binding_sha256,
            }:
                raise CalendarStoreError("full difference baseline mismatch")
            expected_full_before = (
                event["review_policy"]["difference"]["before"]
                if event["schema"] == _EVENT_POLICY_FULL
                else None
            )
            if latest is None:
                latest = snapshot
            previous = event["previous"]
            if previous is None:
                if sequence != 1:
                    raise CalendarStoreError("truncated publication chain")
                break
            if (
                type(previous) is not dict
                or set(previous) != {"publication_id", "publication_sha256"}
                or not _identifier(previous["publication_id"])
                or not _digest(previous["publication_sha256"])
            ):
                raise CalendarStoreError("invalid previous reference")
            event_id, expected_hash = previous["publication_id"], previous["publication_sha256"]
            expected_sequence = sequence - 1
        if latest is None:
            raise CalendarStoreError("empty publication chain")
        if target is not None and not found_target:
            raise CalendarStoreError("receipt publication not in current chain")
        return replace(latest, publication_sha256=pointer["publication_sha256"])

    def read_current(self) -> CalendarSnapshot:
        """Read CURRENT once and validate its complete referenced chain or fail."""
        try:
            self._layout()
            return self._load(self._read(self.root / "CURRENT.json"))
        except OSError as exc:
            raise CalendarStoreError("current snapshot unavailable") from exc

    def read_current_with_consistent_receipts(
        self, *, budget: CalendarReadBudget | None = None
    ) -> ReceiptConsistentCurrent:
        """Read CURRENT with a structurally consistent local receipt chain.

        This is a non-creating, non-recovering operation on an existing trusted,
        cooperating local POSIX store.  The shared lock coordinates with store
        writers, but receipts have no independent hash or identity anchor: a
        forged receipt with another legal observation is not detected. Neither
        data sources, identities, clocks nor runtime bindings are authenticated.
        Durability, hostile path replacement, network
        filesystems and non-POSIX behavior are not established.
        """
        if budget is not None and type(budget) is not CalendarReadBudget:
            raise CalendarStoreError("invalid calendar read budget")
        if fcntl is None or not all(
            hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")
        ):
            raise CalendarStoreError("current receipt consistency unavailable")
        root_fd = None
        lock_fd = None
        locked = False
        total = 0

        def budgeted_read(path: Path) -> bytes:
            nonlocal total
            remaining = budget.max_total_bytes - total
            if remaining <= 0:
                raise CalendarStoreError("calendar total read budget exceeded")
            data = self._read_bounded(path, min(budget.max_file_bytes, remaining))
            total += len(data)
            return data

        read = self._read if budget is None else budgeted_read
        max_publications = None if budget is None else budget.max_publications

        def identity(value: os.stat_result) -> tuple[int, int]:
            return value.st_dev, value.st_ino

        def stable(root_identity: tuple[int, int], lock_identity: tuple[int, int]) -> bool:
            root_entry = self.root.lstat()
            lock_entry = os.stat(".lock", dir_fd=root_fd, follow_symlinks=False)
            return (
                stat.S_ISDIR(root_entry.st_mode)
                and identity(root_entry) == root_identity == identity(os.fstat(root_fd))
                and stat.S_ISREG(lock_entry.st_mode)
                and identity(lock_entry) == lock_identity == identity(os.fstat(lock_fd))
            )

        try:
            root_entry = self.root.lstat()
            if not stat.S_ISDIR(root_entry.st_mode):
                raise CalendarStoreError("invalid store root")
            root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            root_identity = identity(os.fstat(root_fd))
            if root_identity != identity(root_entry):
                raise CalendarStoreError("store root changed")
            lock_fd = os.open(
                ".lock",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=root_fd,
            )
            lock_info = os.fstat(lock_fd)
            if not stat.S_ISREG(lock_info.st_mode):
                raise CalendarStoreError("invalid lock")
            lock_identity = identity(lock_info)
            fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            locked = True
            if not stable(root_identity, lock_identity):
                raise CalendarStoreError("store identity changed")
            self._layout()
            if self._pending_exists() or self._policy_change_pending():
                raise CalendarStoreError("pending store operation")
            pointer = read(self.root / "CURRENT.json")
            if budget is None:
                snapshot = self._load(pointer)
                self._validate_chain_receipts(pointer)
                reread = self._load(pointer)
            else:
                snapshot = self._load(pointer, _reader=read, _max_publications=max_publications)
                self._validate_chain_receipts(
                    pointer, _reader=read, _max_publications=max_publications
                )
                reread = self._load(pointer, _reader=read, _max_publications=max_publications)
            # Revalidate the immutable chain after receipt reads before returning
            # its detached contents; cooperative writers are held by this lock.
            if reread != snapshot or read(self.root / "CURRENT.json") != pointer:
                raise CalendarStoreError("current snapshot changed")
            if self._pending_exists() or self._policy_change_pending():
                raise CalendarStoreError("pending store operation")
            if not stable(root_identity, lock_identity):
                raise CalendarStoreError("store identity changed")
            return ReceiptConsistentCurrent(snapshot)
        except (OSError, CalendarStoreError, ValueError, RecursionError) as exc:
            raise CalendarStoreError("current receipt consistency unavailable") from exc
        finally:
            if lock_fd is not None:
                with contextlib.suppress(OSError):
                    if locked:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                with contextlib.suppress(OSError):
                    os.close(lock_fd)
            if root_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(root_fd)

    def read_publication_policy(self, publication_id: str) -> dict[str, Any] | None:
        """Read hash-bound declarations from a proved publication, never authenticate them."""
        if not _identifier(publication_id):
            raise CalendarStoreError("invalid publication ID")
        try:
            with self._writer_lock():
                if self._pending_exists():
                    raise CalendarStoreError("pending publication requires reconciliation")
                self._receipt_proof(publication_id)
                event = _decode(self._read(self.root / "publications" / f"{publication_id}.json"))
                if event["schema"] not in _POLICY_EVENTS:
                    return None
                return event["review_policy"]
        except OSError as exc:
            raise CalendarStoreError("publication policy unavailable") from exc

    def read_publication_policy_readonly(
        self, publication_id: str, *, budget: CalendarReadBudget
    ) -> dict[str, Any] | None:
        """Bounded, non-creating receipt-chain read of unauthenticated declarations."""
        if not _identifier(publication_id) or type(budget) is not CalendarReadBudget:
            raise CalendarStoreError("invalid publication read arguments")
        if fcntl is None or not all(
            hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")
        ):
            raise CalendarStoreError("publication read unavailable")
        root_fd = lock_fd = None
        locked = False
        total = 0

        def read(path: Path) -> bytes:
            nonlocal total
            remaining = budget.max_total_bytes - total
            if remaining <= 0:
                raise CalendarStoreError("calendar total read budget exceeded")
            data = self._read_bounded(path, min(budget.max_file_bytes, remaining))
            total += len(data)
            return data

        try:
            root_stat = self.root.lstat()
            if not stat.S_ISDIR(root_stat.st_mode):
                raise CalendarStoreError("invalid store root")
            root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            root_identity = (root_stat.st_dev, root_stat.st_ino)
            if root_identity != (os.fstat(root_fd).st_dev, os.fstat(root_fd).st_ino):
                raise CalendarStoreError("store root changed")
            lock_fd = os.open(".lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
            lock_stat = os.fstat(lock_fd)
            if not stat.S_ISREG(lock_stat.st_mode):
                raise CalendarStoreError("invalid store lock")
            fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            locked = True
            self._layout()
            if self._pending_exists():
                raise CalendarStoreError("pending publication requires reconciliation")
            pointer = read(self.root / "CURRENT.json")
            receipt = self._read_receipt(publication_id, _reader=read)
            self._load(
                pointer,
                target=receipt["pointer"],
                target_version=receipt["version_id"],
                _reader=read,
                _max_publications=budget.max_publications,
            )
            self._validate_chain_receipts(
                pointer, _reader=read, _max_publications=budget.max_publications
            )
            event_bytes = read(self.root / "publications" / f"{publication_id}.json")
            if _sha(event_bytes) != receipt["pointer"]["publication_sha256"]:
                raise CalendarStoreError("publication changed")
            event = _decode(event_bytes)
            root_after = self.root.lstat()
            lock_after = os.stat(".lock", dir_fd=root_fd, follow_symlinks=False)
            if (
                root_identity != (root_after.st_dev, root_after.st_ino)
                or (lock_stat.st_dev, lock_stat.st_ino) != (lock_after.st_dev, lock_after.st_ino)
                or read(self.root / "CURRENT.json") != pointer
            ):
                raise CalendarStoreError("store changed during observation")
            return event["review_policy"] if event["schema"] in _POLICY_EVENTS else None
        except OSError as exc:
            raise CalendarStoreError("publication policy unavailable") from exc
        finally:
            if lock_fd is not None:
                if locked:
                    with contextlib.suppress(OSError):
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            if root_fd is not None:
                os.close(root_fd)

    def verify_publication(self, publication_id: str) -> str:
        """Observe CURRENT under the writer lock; never clear a fence."""
        if not _identifier(publication_id):
            raise CalendarStoreError("invalid publication ID")
        try:
            with self._writer_lock():
                if self._pending_exists():
                    return "UNKNOWN"
                snapshot = self.read_current()
                return "VISIBLE" if snapshot.publication_id == publication_id else "UNKNOWN"
        except (OSError, CalendarStoreError):
            return "UNKNOWN"

    def _read_receipt(
        self, publication_id: str, *, _reader: Callable[[Path], bytes] | None = None
    ) -> dict[str, Any]:
        read = self._read if _reader is None else _reader
        receipt = _decode(read(self.root / "receipts" / f"{publication_id}.json"))
        keys = {"schema", "publication_id", "version_id", "pointer", "observation"}
        if receipt.get("schema") == _RECEIPT_TIMED:
            _fields(
                receipt,
                keys | {"observed_utc", "monotonic_ns", "boot_id", "utc_bound_us", "clock_valid"},
                _RECEIPT_TIMED,
            )
            _validate_optional_sample(
                receipt["observed_utc"],
                receipt["monotonic_ns"],
                receipt["boot_id"],
                receipt["utc_bound_us"],
            )
            if type(receipt["clock_valid"]) is not bool:
                raise CalendarStoreError("invalid receipt clock validity")
        else:
            _fields(receipt, keys, _RECEIPT)
        if (
            receipt["publication_id"] != publication_id
            or not _identifier(receipt["version_id"])
            or type(receipt["pointer"]) is not dict
            or type(receipt["observation"]) is not str
            or receipt["observation"] not in {"POST_REPLACE_READBACK", "RECOVERY_CURRENT_READBACK"}
        ):
            raise CalendarStoreError("invalid receipt binding")
        return receipt

    def _receipt_proof(self, publication_id: str) -> tuple[dict[str, Any], CalendarSnapshot]:
        receipt = self._read_receipt(publication_id)
        current_bytes = self._read(self.root / "CURRENT.json")
        current = self._load(
            current_bytes, target=receipt["pointer"], target_version=receipt["version_id"]
        )
        return receipt, current

    def _validate_chain_receipts(
        self,
        pointer_bytes: bytes,
        *,
        _reader: Callable[[Path], bytes] | None = None,
        _max_publications: int | None = None,
    ) -> None:
        """Require receipt bindings for every already-validated event in a CAS chain."""
        read = self._read if _reader is None else _reader
        pointer = _decode(pointer_bytes)
        count = 0
        while pointer is not None:
            count += 1
            if _max_publications is not None and count > _max_publications:
                raise CalendarStoreError("publication read budget exceeded")
            event = _decode(read(self.root / "publications" / f"{pointer['publication_id']}.json"))
            receipt = (
                self._read_receipt(pointer["publication_id"])
                if _reader is None
                else self._read_receipt(pointer["publication_id"], _reader=read)
            )
            if receipt["pointer"] != pointer or receipt["version_id"] != event["version_id"]:
                raise CalendarStoreError("incomplete current publication proof")
            previous = event["previous"]
            pointer = None if previous is None else {"schema": _POINTER, **previous}

    def _contains_policy_history(self, pointer_bytes: bytes) -> bool:
        """Inspect an already validated chain; an event survives lost policy files."""
        pointer = _decode(pointer_bytes)
        while pointer is not None:
            event = _decode(
                self._read(self.root / "publications" / f"{pointer['publication_id']}.json")
            )
            if event["schema"] in _POLICY_EVENTS:
                return True
            previous = event["previous"]
            pointer = None if previous is None else {"schema": _POINTER, **previous}
        return False

    def history_at(self, at_utc: datetime) -> HistoryResult:
        """Return a proved snapshot, or HISTORY_UNCERTAIN, under the writer lock.

        Each replacement is only known to have happened in its closed interval
        [lower - declared error, upper + declared error]. Boundaries and
        pre-genesis time are uncertain. No bound is trusted by default.
        No historical conclusion is made from orphan files or a v1 event/receipt.
        Clock checks assume trusted cooperating writers and a shared monotonic
        domain for the same host boot; they cannot prove uninterrupted UTC.
        """
        try:
            if (
                type(at_utc) is not datetime
                or at_utc.tzinfo is None
                or at_utc.utcoffset() != timedelta(0)
            ):
                raise CalendarStoreError("query must be an aware UTC time")
            with self._writer_lock():
                if self._pending_exists():
                    raise CalendarStoreError("pending publication")
                pointer_bytes = self._read(self.root / "CURRENT.json")
                self._load(pointer_bytes)
                pointer = _decode(pointer_bytes)
                entries = []
                while pointer is not None:
                    publication_id = pointer["publication_id"]
                    event = _decode(
                        self._read(self.root / "publications" / f"{publication_id}.json")
                    )
                    receipt = self._read_receipt(publication_id)
                    if (
                        event["schema"] not in _TIMED_EVENTS
                        or receipt["schema"] != _RECEIPT_TIMED
                        or receipt["pointer"] != pointer
                        or receipt["version_id"] != event["version_id"]
                        or not receipt["clock_valid"]
                    ):
                        raise CalendarStoreError("incomplete historical clock evidence")
                    lower = (
                        event["lower_utc"],
                        event["monotonic_ns"],
                        event["boot_id"],
                        event["utc_bound_us"],
                    )
                    upper = (
                        receipt["observed_utc"],
                        receipt["monotonic_ns"],
                        receipt["boot_id"],
                        receipt["utc_bound_us"],
                    )
                    if not _clock_pair_valid(lower, upper):
                        raise CalendarStoreError("invalid publication clock interval")
                    entries.append((event, lower, upper))
                    previous = event["previous"]
                    pointer = None if previous is None else {"schema": _POINTER, **previous}
                entries.reverse()
                for earlier, later in zip(entries, entries[1:], strict=False):
                    if not _clock_pair_valid(earlier[2], later[1]):
                        raise CalendarStoreError("clock discontinuity between publications")
                    earlier_high = _parse_utc(earlier[2][0]) + timedelta(microseconds=earlier[2][3])
                    later_low = _parse_utc(later[1][0]) - timedelta(microseconds=later[1][3])
                    if earlier_high >= later_low:
                        raise CalendarStoreError(
                            "publication uncertainty intervals overlap or touch"
                        )
                now = self._clock_sample()
                if not entries or not _clock_pair_valid(entries[-1][2], now):
                    raise CalendarStoreError("current clock unavailable or discontinuous")
                if at_utc > _parse_utc(now[0]) - timedelta(microseconds=now[3]):
                    raise CalendarStoreError("future time")
                selected = None
                for event, lower, upper in entries:
                    low = _parse_utc(lower[0]) - timedelta(microseconds=lower[3])
                    high = _parse_utc(upper[0]) + timedelta(microseconds=upper[3])
                    if low <= at_utc <= high:
                        raise CalendarStoreError("publication time is uncertain")
                    if high < at_utc:
                        selected = event
                    elif at_utc < low:
                        break
                if selected is None:
                    raise CalendarStoreError("before proved publication history")
                snapshot = _decode_version(
                    selected,
                    self._read(self.root / "versions" / f"{selected['version_id']}.json"),
                )
                return HistoryResult("FOUND", snapshot)
        except (OSError, CalendarStoreError, ValueError, RecursionError, OverflowError) as exc:
            return HistoryResult("HISTORY_UNCERTAIN", reason=str(exc))

    def reconcile_publication(self, publication_id: str) -> PublicationResult:
        """Validate receipt and current chain under lock; clear intent only on proof.

        COMMITTED means the target is CURRENT; HISTORICAL_COMMITTED means the
        validated current chain includes it. Neither proves an exact commit time.
        A missing receipt is created only when intact intent matches CURRENT and
        its full chain; its upper bound is the later recovery observation time.
        Corrupt receipts or incomplete proof return COMMIT_UNKNOWN.
        """
        proven = False
        outcome = "COMMIT_UNKNOWN"
        version_id = ""
        try:
            if not _identifier(publication_id):
                raise CalendarStoreError("invalid publication ID")
            with self._writer_lock():
                intent = None
                if self._pending_exists():
                    intent = _decode(self._read(self.root / "PENDING.json"))
                    _fields(intent, {"schema", "publication_id", "version_id", "pointer"}, _INTENT)
                    if (
                        intent["publication_id"] != publication_id
                        or not _identifier(intent["version_id"])
                        or type(intent["pointer"]) is not dict
                    ):
                        raise CalendarStoreError("intent ID or fields do not match")
                receipt_path = self.root / "receipts" / f"{publication_id}.json"
                if intent is not None and not self._entry_exists(receipt_path):
                    target = intent["pointer"]
                    if target.get("publication_id") != publication_id:
                        raise CalendarStoreError("intent pointer ID mismatch")
                    pointer_bytes = _canonical(target)
                    if self._read(self.root / "CURRENT.json") != pointer_bytes:
                        raise CalendarStoreError("pending publication is not proven current")
                    self._load(pointer_bytes, target=target, target_version=intent["version_id"])
                    event = _decode(
                        self._read(self.root / "publications" / f"{publication_id}.json")
                    )
                    recovered = {
                        "schema": _RECEIPT,
                        "publication_id": publication_id,
                        "version_id": intent["version_id"],
                        "pointer": target,
                        "observation": "RECOVERY_CURRENT_READBACK",
                    }
                    if event["schema"] in _TIMED_EVENTS:
                        upper = self._clock_sample()
                        recovered.update(
                            {
                                "schema": _RECEIPT_TIMED,
                                "observed_utc": upper[0],
                                "monotonic_ns": upper[1],
                                "boot_id": upper[2],
                                "utc_bound_us": upper[3],
                                "clock_valid": _clock_pair_valid(
                                    (
                                        event["lower_utc"],
                                        event["monotonic_ns"],
                                        event["boot_id"],
                                        event["utc_bound_us"],
                                    ),
                                    upper,
                                ),
                            }
                        )
                    recovery_bytes = _canonical(recovered)
                    self._write_new(receipt_path, recovery_bytes)
                    if self._read(receipt_path) != recovery_bytes:
                        raise CalendarStoreError("recovery receipt readback mismatch")
                receipt, snapshot = self._receipt_proof(publication_id)
                if intent is not None and (
                    intent["pointer"] != receipt["pointer"]
                    or intent["version_id"] != receipt["version_id"]
                ):
                    raise CalendarStoreError("receipt or intent binding mismatch")
                version_id = receipt["version_id"]
                outcome = (
                    "COMMITTED"
                    if snapshot.publication_id == publication_id
                    else "HISTORICAL_COMMITTED"
                )
                proven = True
                if intent is not None:
                    self._clear_pending()
            return PublicationResult(
                outcome,
                "UNCONFIRMED",
                publication_id,
                version_id,
                fence="CLEAR",
                post_commit="COMPLETE",
            )
        except (OSError, CalendarStoreError, ValueError, RecursionError) as exc:
            return PublicationResult(
                outcome if proven else "COMMIT_UNKNOWN",
                "UNCONFIRMED",
                publication_id,
                version_id,
                str(exc),
                fence="UNKNOWN",
                post_commit="INCOMPLETE" if proven else "NOT_APPLICABLE",
            )

    def publish(
        self,
        content: dict[str, Any],
        *,
        publication_id: str,
        version_id: str,
        republish: bool = False,
        coverage_manifest_bytes: bytes | None = None,
        coverage_manifest_sha256: str | None = None,
        binding_bytes: bytes | None = None,
        binding_sha256: str | None = None,
        simulation_provenance: dict[str, Any] | None = None,
        expected_current: CurrentPrecondition | None = None,
        policy_publication: PolicyPublication | None = None,
        locked_policy_guard: Callable[[ReviewPolicy], None] | None = None,
    ) -> PublicationResult:
        """Publish a whole snapshot; supplied composite artifacts must match exactly.

        Omitting all coverage artifacts creates legacy /1 data. /1 cannot replace
        a covered current version except by an explicit proved-version republish.
        New publication requires initialized directory policy, matching policy
        evidence and a verified CURRENT condition under the writer lock.
        An existing proven publication ID is reconciled before this comparison:
        replay reports the original operation and does not attempt a new switch.
        A mismatch returns NOT_COMMITTED before creating any publication artifacts.
        """
        replaced = False
        visible = False
        fence = "UNKNOWN"
        uncertain_prior = True
        temp_path = None
        try:
            if not _identifier(publication_id):
                return PublicationResult(
                    "NOT_COMMITTED",
                    "UNCONFIRMED",
                    publication_id,
                    version_id,
                    "invalid publication identifier",
                    fence="UNKNOWN",
                )
            if locked_policy_guard is not None and not callable(locked_policy_guard):
                raise CalendarStoreError("invalid policy guard")
            with self._writer_lock():
                if self._policy_change_pending():
                    return PublicationResult(
                        "COMMIT_UNKNOWN",
                        "UNCONFIRMED",
                        publication_id,
                        version_id,
                        "pending policy change requires reconciliation",
                        fence="PRESENT",
                    )
                if self._pending_exists():
                    try:
                        owner = self._pending_owner()
                    except (OSError, CalendarStoreError, ValueError, RecursionError):
                        owner = None
                    return PublicationResult(
                        "NOT_COMMITTED"
                        if owner is not None and owner != publication_id
                        else "COMMIT_UNKNOWN",
                        "UNCONFIRMED",
                        publication_id,
                        version_id,
                        "pending publication requires reconciliation",
                        fence="PRESENT",
                    )
                fence = "CLEAR"
                receipt_path = self.root / "receipts" / f"{publication_id}.json"
                event_path = self.root / "publications" / f"{publication_id}.json"
                receipt_exists = self._entry_exists(receipt_path)
                event_exists = self._entry_exists(event_path)
                uncertain_prior = receipt_exists or event_exists
                if not _identifier(version_id):
                    raise CalendarStoreError("invalid version identifier")
                if type(republish) is not bool:
                    raise CalendarStoreError("republish must be boolean")
                content_bytes = _content(content)
                artifacts = (
                    coverage_manifest_bytes,
                    coverage_manifest_sha256,
                    binding_bytes,
                    binding_sha256,
                )
                covered = any(item is not None for item in artifacts)
                snapshot_bytes = _snapshot_bytes(
                    content_bytes, *artifacts, simulation_provenance=simulation_provenance
                )
                # Replaying a proved ID is a read-only observation, not a new
                # policy authorization or a switch.  Compare original evidence
                # to the caller's original claim, independently of today's mode.
                if receipt_exists:
                    receipt, replay_current = self._receipt_proof(publication_id)
                    prior_bytes = self._read(
                        self.root / "versions" / f"{receipt['version_id']}.json"
                    )
                    prior_event = _decode(self._read(event_path))
                    original = prior_event.get("review_policy")
                    evidence_matches = False
                    if original is None:
                        evidence_matches = policy_publication is None
                    elif (
                        type(policy_publication) is PolicyPublication
                        and type(expected_current) is CurrentPrecondition
                    ):
                        confirmation = policy_publication.confirmation_current
                        if confirmation is None or type(confirmation) is CurrentPrecondition:
                            replay_claim = {
                                "directory_id": policy_publication.directory_id,
                                "policy_revision": policy_publication.policy_revision,
                                "mode": policy_publication.mode,
                                "mode_source": original["mode_source"],
                                "snapshot_sha256": policy_publication.snapshot_sha256,
                                "difference": policy_publication.difference,
                                "difference_sha256": policy_publication.difference_sha256,
                                "reviewer": policy_publication.reviewer,
                                "confirmation_snapshot_sha256": policy_publication.confirmation_snapshot_sha256,
                                "confirmation_difference_sha256": policy_publication.confirmation_difference_sha256,
                                "confirmation_policy_revision": policy_publication.confirmation_policy_revision,
                                "baseline": expected_current.__dict__,
                                "confirmation_current": confirmation.__dict__
                                if confirmation
                                else None,
                                "confirmation_utc": policy_publication.confirmation_utc,
                                "identity_status": "UNAUTHENTICATED",
                                "source_status": "UNVERIFIED",
                            }
                            evidence_matches = _canonical(replay_claim) == _canonical(original)
                    if (
                        receipt["version_id"] != version_id
                        or prior_bytes != snapshot_bytes
                        or not evidence_matches
                    ):
                        return PublicationResult(
                            "ID_CONFLICT",
                            "UNCONFIRMED",
                            publication_id,
                            version_id,
                            "publication ID conflicts with original operation",
                            fence="CLEAR",
                        )
                    return PublicationResult(
                        "COMMITTED"
                        if replay_current.publication_id == publication_id
                        else "HISTORICAL_COMMITTED",
                        "UNCONFIRMED",
                        publication_id,
                        version_id,
                        fence="CLEAR",
                        post_commit="COMPLETE",
                    )
                policy = self._review_policy_locked()
                if policy is None:
                    raise CalendarStoreError("review policy not initialized")
                elif type(policy_publication) is not PolicyPublication:
                    raise CalendarStoreError("managed publication requires policy evidence")
                elif (
                    policy_publication.directory_id != policy.directory_id
                    or policy_publication.policy_revision != policy.revision
                    or policy_publication.mode != policy.mode
                    or type(expected_current) is not CurrentPrecondition
                ):
                    raise CalendarStoreError("review policy precondition mismatch")
                if locked_policy_guard is not None:
                    locked_policy_guard(policy)
                evidence = None
                if policy is not None:
                    confirmation_current = policy_publication.confirmation_current
                    if (
                        confirmation_current is not None
                        and type(confirmation_current) is not CurrentPrecondition
                    ):
                        raise CalendarStoreError("invalid confirmation baseline")
                    evidence = {
                        "directory_id": policy.directory_id,
                        "policy_revision": policy.revision,
                        "mode": policy.mode,
                        "mode_source": policy.mode_source,
                        "snapshot_sha256": policy_publication.snapshot_sha256,
                        "difference": policy_publication.difference,
                        "difference_sha256": policy_publication.difference_sha256,
                        "reviewer": policy_publication.reviewer,
                        "confirmation_snapshot_sha256": policy_publication.confirmation_snapshot_sha256,
                        "confirmation_difference_sha256": policy_publication.confirmation_difference_sha256,
                        "confirmation_policy_revision": policy_publication.confirmation_policy_revision,
                        "baseline": expected_current.__dict__,
                        "confirmation_current": confirmation_current.__dict__
                        if confirmation_current is not None
                        else None,
                        "confirmation_utc": policy_publication.confirmation_utc,
                        "identity_status": "UNAUTHENTICATED",
                        "source_status": "UNVERIFIED",
                    }
                    # Freeze caller-owned diff before the event is generated.
                    evidence = json.loads(_canonical(evidence))
                    expected_previous = (
                        None
                        if expected_current.mode == "ABSENT"
                        else {
                            "publication_id": expected_current.publication_id,
                            "publication_sha256": expected_current.publication_sha256,
                        }
                    )
                    full_difference = len(
                        _canonical(evidence["difference"])
                    ) > _LEGACY_DIFFERENCE_BYTES or (
                        type(evidence["difference"]) is dict
                        and evidence["difference"].get("schema")
                        == "bt_api.calendar-full-declaration-difference/2"
                    )
                    _policy_evidence(
                        evidence,
                        snapshot_bytes,
                        expected_previous,
                        review_required=False,
                        full=full_difference,
                    )
                current_path = self.root / "CURRENT.json"
                try:
                    pointer_bytes = self._read(current_path)
                except FileNotFoundError:
                    # Any orphan or previous file prevents proving this is a new store.
                    if any(
                        any((self.root / d).iterdir())
                        for d in ("versions", "publications", "receipts")
                    ):
                        raise CalendarStoreError(
                            "CURRENT missing in nonempty store; state unknown"
                        ) from None
                    current = None
                    previous = None
                else:
                    current = self._load(pointer_bytes)
                    if policy is None and self._contains_policy_history(pointer_bytes):
                        raise CalendarStoreError("managed publication history without policy")
                    pointer = _decode(pointer_bytes)
                    previous = {
                        "publication_id": pointer["publication_id"],
                        "publication_sha256": pointer["publication_sha256"],
                    }
                if event_exists:
                    raise CalendarStoreError(
                        "publication ID already exists without a proven receipt"
                    )
                if expected_current is not None:
                    if type(expected_current) is not CurrentPrecondition or not (
                        (
                            expected_current.mode == "ABSENT"
                            and expected_current.publication_id is None
                            and expected_current.publication_sha256 is None
                        )
                        or (
                            expected_current.mode == "MATCH"
                            and _identifier(expected_current.publication_id)
                            and _digest(expected_current.publication_sha256)
                        )
                    ):
                        raise CalendarStoreError("invalid current precondition")
                    if current is not None:
                        self._validate_chain_receipts(pointer_bytes)
                    matches = (
                        current is None
                        if expected_current.mode == "ABSENT"
                        else current is not None
                        and current.publication_id == expected_current.publication_id
                        and current.publication_sha256 == expected_current.publication_sha256
                    )
                    if not matches:
                        return PublicationResult(
                            "NOT_COMMITTED",
                            "UNCONFIRMED",
                            publication_id,
                            version_id,
                            "current precondition mismatch",
                            fence="CLEAR",
                        )
                digest = _sha(content_bytes)
                if (
                    current is not None
                    and current.coverage_status == "DECLARED_UNVERIFIED"
                    and not covered
                    and not republish
                ):
                    raise CalendarStoreError(
                        "coverage artifacts required for covered current version"
                    )
                unchanged = (
                    current is not None
                    and self._read(self.root / "versions" / f"{current.version_id}.json")
                    == snapshot_bytes
                )
                if unchanged and not republish:
                    return PublicationResult(
                        "UNCHANGED",
                        "UNCONFIRMED",
                        publication_id,
                        current.version_id,
                        "snapshot identical; source and full checks not proven",
                        fence="CLEAR",
                    )
                # No change is only a storage-level observation. A new event
                # still requires the configured mode's complete evidence.
                _policy_evidence(evidence, snapshot_bytes, previous, full=full_difference)
                if full_difference and (
                    current is None
                    or evidence["difference"]["before"]
                    != {
                        "publication_id": current.publication_id,
                        "publication_sha256": current.publication_sha256,
                        "version_id": current.version_id,
                        "version_sha256": current.version_sha256,
                        "snapshot_sha256": current.content_sha256,
                        "manifest_sha256": current.coverage_manifest_sha256,
                        "binding_sha256": current.binding_sha256,
                    }
                ):
                    raise CalendarStoreError("full difference baseline mismatch")
                if (
                    full_difference
                    and evidence["difference"].get("schema")
                    == "bt_api.calendar-full-declaration-difference/2"
                    and evidence["difference"]["simulation"]["before"]
                    != current.simulation_provenance
                ):
                    raise CalendarStoreError("simulation baseline mismatch")
                if republish:
                    if current is None:
                        raise CalendarStoreError("cannot republish without committed history")
                    prior = _decode(pointer_bytes)
                    while prior is not None:
                        prior_event = _decode(
                            self._read(
                                self.root / "publications" / f"{prior['publication_id']}.json"
                            )
                        )
                        if prior_event["version_id"] == version_id:
                            receipt, _ = self._receipt_proof(prior["publication_id"])
                            if receipt["pointer"] != prior:
                                raise CalendarStoreError("republish receipt mismatch")
                            old = self._read(self.root / "versions" / f"{version_id}.json")
                            if old != snapshot_bytes:
                                raise CalendarStoreError("republish content mismatch")
                            break
                        previous_ref = prior_event["previous"]
                        prior = (
                            None if previous_ref is None else {"schema": _POINTER, **previous_ref}
                        )
                    if prior is None:
                        raise CalendarStoreError("version has no proved publication")
                lower = self._clock_sample()
                event = {
                    "schema": (
                        _EVENT_POLICY_FULL
                        if full_difference
                        else _EVENT_POLICY_COVERED
                        if covered
                        else _EVENT_POLICY
                    )
                    if evidence is not None
                    else (_EVENT_COVERED if covered else _EVENT_TIMED),
                    "publication_id": publication_id,
                    "sequence": 1 if current is None else current.sequence + 1,
                    "previous": previous,
                    "version_id": version_id,
                    "content_sha256": digest,
                    "lower_utc": lower[0],
                    "monotonic_ns": lower[1],
                    "boot_id": lower[2],
                    "utc_bound_us": lower[3],
                }
                if covered:
                    event["version_sha256"] = _sha(snapshot_bytes)
                if evidence is not None:
                    event["review_policy"] = evidence
                event_bytes = _canonical(event)
                if (
                    event["schema"] == _EVENT_POLICY_FULL
                    and len(event_bytes) > _PUBLICATION_EVENT_BYTES
                ):
                    raise CalendarStoreError("publication event too large")
                pointer_bytes = _canonical(
                    {
                        "schema": _POINTER,
                        "publication_id": publication_id,
                        "publication_sha256": _sha(event_bytes),
                    }
                )
                if not republish:
                    self._write_new(self.root / "versions" / f"{version_id}.json", snapshot_bytes)
                self._write_new(self.root / "publications" / f"{publication_id}.json", event_bytes)
                # Verify the prewritten candidate before making it visible.
                self._load(pointer_bytes)
                temp_path = self.root / f".CURRENT-{uuid.uuid4().hex}.tmp"
                self._write_new(temp_path, pointer_bytes)
                intent_bytes = _canonical(
                    {
                        "schema": _INTENT,
                        "publication_id": publication_id,
                        "version_id": version_id,
                        "pointer": _decode(pointer_bytes),
                    }
                )
                # Exclusive creation and fsync precede the only visibility switch.
                # A partial intent after a crash remains a blocking unknown.
                fence = "UNKNOWN"
                self._write_new(self.root / "PENDING.json", intent_bytes)
                fence = "PRESENT"
                # Even an exception from replace may follow an actual replacement.
                replaced = True
                os.replace(temp_path, current_path)
                if self._read(current_path) != pointer_bytes:
                    raise CalendarStoreError("current readback mismatch")
                self._load(pointer_bytes)
                visible = True
                upper = self._clock_sample()
                receipt_bytes = _canonical(
                    {
                        "schema": _RECEIPT_TIMED,
                        "publication_id": publication_id,
                        "version_id": version_id,
                        "pointer": _decode(pointer_bytes),
                        "observation": "POST_REPLACE_READBACK",
                        "observed_utc": upper[0],
                        "monotonic_ns": upper[1],
                        "boot_id": upper[2],
                        "utc_bound_us": upper[3],
                        "clock_valid": _clock_pair_valid(lower, upper),
                    }
                )
                receipt_path = self.root / "receipts" / f"{publication_id}.json"
                self._write_new(receipt_path, receipt_bytes)
                if self._read(receipt_path) != receipt_bytes:
                    raise CalendarStoreError("receipt readback mismatch")
                fence = "UNKNOWN"
                self._clear_pending()
                fence = "CLEAR"
            return PublicationResult(
                "COMMITTED",
                "UNCONFIRMED",
                publication_id,
                version_id,
                fence="CLEAR",
                post_commit="COMPLETE",
            )
        except (OSError, CalendarStoreError, ValueError, RecursionError) as exc:
            return PublicationResult(
                "COMMITTED"
                if visible
                else "COMMIT_UNKNOWN"
                if replaced or uncertain_prior
                else "NOT_COMMITTED",
                "UNCONFIRMED",
                publication_id,
                version_id,
                str(exc),
                fence=fence,
                post_commit="INCOMPLETE" if visible else "NOT_APPLICABLE",
            )
        finally:
            if temp_path is not None:
                with contextlib.suppress(OSError):
                    temp_path.unlink(missing_ok=True)
