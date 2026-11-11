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
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows is explicitly unsupported
    fcntl = None

_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_SNAPSHOT = "bt_api.calendar-store.snapshot/1"
_SNAPSHOT_COVERED = "bt_api.calendar-store.snapshot/2"
_EVENT = "bt_api.calendar-store.publication/1"
_EVENT_TIMED = "bt_api.calendar-store.publication/2"
_EVENT_COVERED = "bt_api.calendar-store.publication/3"
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


def _decode_version(event: dict[str, Any], snapshot_bytes: bytes) -> CalendarSnapshot:
    snapshot = _decode(snapshot_bytes)
    content_bytes = _content(snapshot.get("content"))
    if _sha(content_bytes) != event["content_sha256"]:
        raise CalendarStoreError("content digest mismatch")
    if snapshot.get("schema") == _SNAPSHOT:
        _fields(snapshot, {"schema", "content"}, _SNAPSHOT)
        if event["schema"] == _EVENT_COVERED:
            raise CalendarStoreError("covered event cannot reference legacy version")
        return CalendarSnapshot(
            event["publication_id"],
            event["sequence"],
            event["version_id"],
            event["content_sha256"],
            snapshot["content"],
        )
    _fields(
        snapshot,
        {"schema", "content", "manifest", "manifest_sha256", "binding", "binding_sha256"},
        _SNAPSHOT_COVERED,
    )
    if event["schema"] != _EVENT_COVERED or event["version_sha256"] != _sha(snapshot_bytes):
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
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise CalendarStoreError("store entry is not a regular file")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                return stream.read()
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

    def _load(
        self,
        pointer_bytes: bytes,
        *,
        target: dict[str, Any] | None = None,
        target_version: str | None = None,
    ) -> CalendarSnapshot:
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
        while event_id is not None:
            if event_id in seen:
                raise CalendarStoreError("publication cycle")
            seen.add(event_id)
            data = self._read(self.root / "publications" / f"{event_id}.json")
            if _sha(data) != expected_hash:
                raise CalendarStoreError("publication digest mismatch")
            event = _decode(data)
            event_keys = {
                "schema",
                "publication_id",
                "sequence",
                "previous",
                "version_id",
                "content_sha256",
            }
            if event.get("schema") in (_EVENT_TIMED, _EVENT_COVERED):
                event_keys |= {"lower_utc", "monotonic_ns", "boot_id", "utc_bound_us"}
                if event["schema"] == _EVENT_COVERED:
                    event_keys.add("version_sha256")
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
                or (event["schema"] == _EVENT_COVERED and not _digest(event["version_sha256"]))
            ):
                raise CalendarStoreError("invalid publication")
            if target is not None and event_id == target["publication_id"]:
                if (
                    expected_hash != target["publication_sha256"]
                    or event["version_id"] != target_version
                ):
                    raise CalendarStoreError("target publication does not match receipt")
                found_target = True
            snapshot_bytes = self._read(self.root / "versions" / f"{event['version_id']}.json")
            snapshot = _decode_version(event, snapshot_bytes)
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
        return latest

    def read_current(self) -> CalendarSnapshot:
        """Read CURRENT once and validate its complete referenced chain or fail."""
        try:
            self._layout()
            return self._load(self._read(self.root / "CURRENT.json"))
        except OSError as exc:
            raise CalendarStoreError("current snapshot unavailable") from exc

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

    def _read_receipt(self, publication_id: str) -> dict[str, Any]:
        receipt = _decode(self._read(self.root / "receipts" / f"{publication_id}.json"))
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
                        event["schema"] not in (_EVENT_TIMED, _EVENT_COVERED)
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
                    if event["schema"] in (_EVENT_TIMED, _EVENT_COVERED):
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
    ) -> PublicationResult:
        """Publish a whole snapshot; supplied composite artifacts must match exactly.

        Omitting all coverage artifacts creates legacy /1 data. /1 cannot replace
        a covered current version except by an explicit proved-version republish.
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
            with self._writer_lock():
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
                if covered:
                    manifest, binding = _coverage_bundle(content_bytes, *artifacts)
                    snapshot_bytes = _canonical(
                        {
                            "schema": _SNAPSHOT_COVERED,
                            "content": json.loads(content_bytes),
                            "manifest": manifest,
                            "manifest_sha256": coverage_manifest_sha256,
                            "binding": binding,
                            "binding_sha256": binding_sha256,
                        }
                    )
                else:
                    snapshot_bytes = _canonical(
                        {"schema": _SNAPSHOT, "content": json.loads(content_bytes)}
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
                    pointer = _decode(pointer_bytes)
                    previous = {
                        "publication_id": pointer["publication_id"],
                        "publication_sha256": pointer["publication_sha256"],
                    }
                if receipt_exists:
                    receipt, current = self._receipt_proof(publication_id)
                    prior_snapshot_bytes = self._read(
                        self.root / "versions" / f"{receipt['version_id']}.json"
                    )
                    if (
                        receipt["version_id"] != version_id
                        or prior_snapshot_bytes != snapshot_bytes
                    ):
                        return PublicationResult(
                            "ID_CONFLICT",
                            "UNCONFIRMED",
                            publication_id,
                            version_id,
                            "publication ID conflicts with committed content or version",
                            fence="CLEAR",
                        )
                    return PublicationResult(
                        "COMMITTED"
                        if current.publication_id == publication_id
                        else "HISTORICAL_COMMITTED",
                        "UNCONFIRMED",
                        publication_id,
                        version_id,
                        fence="CLEAR",
                        post_commit="COMPLETE",
                    )
                if event_exists:
                    raise CalendarStoreError(
                        "publication ID already exists without a proven receipt"
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
                    and current.content_sha256 == digest
                    and (
                        (not covered and current.coverage_status == "NOT_PRESENT")
                        or (covered and current.version_sha256 == _sha(snapshot_bytes))
                    )
                )
                if unchanged and not republish:
                    return PublicationResult(
                        "UNCHANGED",
                        "UNCONFIRMED",
                        publication_id,
                        current.version_id,
                        fence="CLEAR",
                    )
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
                    "schema": _EVENT_COVERED if covered else _EVENT_TIMED,
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
                event_bytes = _canonical(event)
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
