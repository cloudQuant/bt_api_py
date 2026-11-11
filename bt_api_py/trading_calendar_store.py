"""Local, neutral calendar snapshot storage; not a calendar validator.

INTERNAL EXPERIMENTAL SLICE: do not integrate into a production publication path.
The process-crash intent fence is not proof of power-loss durability or 67.02 acceptance.
The caller must validate venue semantics, source coverage and release authority.
This provisional envelope is independent of the as-yet-unfrozen 67.01 schema.
Only cooperating writers on a trusted local POSIX filesystem are supported; external
deletion or replacement of lock, intent, or directory entries violates that model.
History-by-time, rollback, recovery of lost CURRENT, Windows locking, and power
failure durability are intentionally unavailable. Hashes are not authentication.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows is explicitly unsupported
    fcntl = None

_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_SNAPSHOT = "bt_api.calendar-store.snapshot/1"
_EVENT = "bt_api.calendar-store.publication/1"
_POINTER = "bt_api.calendar-store.current/1"
_INTENT = "bt_api.calendar-store.intent/1"
_RECEIPT = "bt_api.calendar-store.receipt/1"


class CalendarStoreError(Exception):
    """Unavailable, invalid, unsupported, or untrusted local store state."""


@dataclass(frozen=True)
class CalendarSnapshot:
    publication_id: str
    sequence: int
    version_id: str
    content_sha256: str
    content: dict[str, Any]


@dataclass(frozen=True)
class PublicationResult:
    visibility: str
    durability: str
    publication_id: str
    version_id: str
    detail: str = ""
    fence: str = "UNKNOWN"
    post_commit: str = "NOT_APPLICABLE"


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

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        if not self.root.is_absolute() or ".." in self.root.parts:
            raise CalendarStoreError("store root must be absolute")

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
            _fields(
                event,
                {
                    "schema",
                    "publication_id",
                    "sequence",
                    "previous",
                    "version_id",
                    "content_sha256",
                },
                _EVENT,
            )
            sequence = event["sequence"]
            if (
                event["publication_id"] != event_id
                or type(sequence) is not int
                or sequence < 1
                or (expected_sequence is not None and sequence != expected_sequence)
                or not _identifier(event["version_id"])
                or not _digest(event["content_sha256"])
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
            snapshot = _decode(snapshot_bytes)
            _fields(snapshot, {"schema", "content"}, _SNAPSHOT)
            content_bytes = _content(snapshot["content"])
            if _sha(content_bytes) != event["content_sha256"]:
                raise CalendarStoreError("content digest mismatch")
            if latest is None:
                latest = CalendarSnapshot(
                    event_id,
                    sequence,
                    event["version_id"],
                    event["content_sha256"],
                    snapshot["content"],
                )
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

    def _receipt_proof(self, publication_id: str) -> tuple[dict[str, Any], CalendarSnapshot]:
        receipt = _decode(self._read(self.root / "receipts" / f"{publication_id}.json"))
        _fields(
            receipt, {"schema", "publication_id", "version_id", "pointer", "observation"}, _RECEIPT
        )
        if (
            receipt["publication_id"] != publication_id
            or not _identifier(receipt["version_id"])
            or type(receipt["pointer"]) is not dict
            or type(receipt["observation"]) is not str
            or receipt["observation"] not in {"POST_REPLACE_READBACK", "RECOVERY_CURRENT_READBACK"}
        ):
            raise CalendarStoreError("invalid receipt binding")
        current_bytes = self._read(self.root / "CURRENT.json")
        current = self._load(
            current_bytes, target=receipt["pointer"], target_version=receipt["version_id"]
        )
        return receipt, current

    def reconcile_publication(self, publication_id: str) -> PublicationResult:
        """Validate receipt and current chain under lock; clear intent only on proof.

        COMMITTED means the target is CURRENT; HISTORICAL_COMMITTED means the
        validated current chain includes it. Neither proves an exact commit time.
        A missing receipt is created only when intact intent matches CURRENT and
        its full chain; its observation is marked as recovery, with no timestamp.
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
                    recovery_bytes = _canonical(
                        {
                            "schema": _RECEIPT,
                            "publication_id": publication_id,
                            "version_id": intent["version_id"],
                            "pointer": target,
                            "observation": "RECOVERY_CURRENT_READBACK",
                        }
                    )
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
        self, content: dict[str, Any], *, publication_id: str, version_id: str
    ) -> PublicationResult:
        """Publish one prevalidated whole snapshot; outcome is visibility only."""
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
                content_bytes = _content(content)
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
                    prior_snapshot = _decode(
                        self._read(self.root / "versions" / f"{receipt['version_id']}.json")
                    )
                    if (
                        receipt["version_id"] != version_id
                        or _content(prior_snapshot["content"]) != content_bytes
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
                if current is not None and current.content_sha256 == digest:
                    return PublicationResult(
                        "UNCHANGED",
                        "UNCONFIRMED",
                        publication_id,
                        current.version_id,
                        fence="CLEAR",
                    )
                event = {
                    "schema": _EVENT,
                    "publication_id": publication_id,
                    "sequence": 1 if current is None else current.sequence + 1,
                    "previous": previous,
                    "version_id": version_id,
                    "content_sha256": digest,
                }
                event_bytes = _canonical(event)
                pointer_bytes = _canonical(
                    {
                        "schema": _POINTER,
                        "publication_id": publication_id,
                        "publication_sha256": _sha(event_bytes),
                    }
                )
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
                receipt_bytes = _canonical(
                    {
                        "schema": _RECEIPT,
                        "publication_id": publication_id,
                        "version_id": version_id,
                        "pointer": _decode(pointer_bytes),
                        "observation": "POST_REPLACE_READBACK",
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
