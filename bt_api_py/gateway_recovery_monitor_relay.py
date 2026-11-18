"""Bounded local recovery-journal relay to an authenticated Monitor v2 sink.

The journal is borrowed, not closed. The local lock is not cross-host fencing;
simultaneous rollback of source and checkpoint, hostile filesystem ABA, source
truth and trading authority are not established. Journal path/identity/scope
checks use its current private attributes as a trusted composition dependency.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from bt_api_base.market_recovery_journal_event import RecoveryJournalEventError
from bt_api_gateway.recovery_observation_event import recovery_journal_event
from bt_api_gateway.recovery_observation_journal import RecoveryObservationJournal

from bt_api_py.service_monitor_authenticated import (
    AuthenticatedMonitorSinkError,
    AuthenticatedMonitorV2Sink,
    _bounded,
    _canonical,
    _text,
)

_DDL = (
    "CREATE TABLE state (schema INTEGER NOT NULL, manifest TEXT NOT NULL, cursor INTEGER NOT NULL, "
    "anchor_cursor INTEGER, anchor_digest TEXT, cursor_digest TEXT, pending_cursor INTEGER, "
    "pending_digest TEXT, pending_envelope TEXT)"
)
_EVENT_TYPE = "gateway.recovery.journal.v1"


class GatewayRecoveryRelayError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GatewayRecoveryRelayStatus:
    state: str
    cursor: int | None
    pending_count: int
    delivered: int = 0


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _file_identity(path):
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError
    return info.st_dev, info.st_ino


def _lock(fd):
    if os.name != "posix":
        raise GatewayRecoveryRelayError("LOCK_UNAVAILABLE")
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise GatewayRecoveryRelayError("WRITER_BUSY") from None


def _unlock(fd):
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


class GatewayRecoveryMonitorRelay:
    """Synchronous, single-writer, one-pending journal relay on its owner thread."""

    def __init__(
        self,
        journal,
        *,
        journal_path,
        scope_ref,
        source_ref,
        checkpoint_path,
        sink,
        clock_utc=time.time,
    ):
        if (
            type(journal) is not RecoveryObservationJournal
            or type(sink) is not AuthenticatedMonitorV2Sink
            or type(journal.schema_version) is not int
            or journal.schema_version != 2
            or not _text(scope_ref)
            or not _text(source_ref)
            or not callable(clock_utc)
        ):
            raise GatewayRecoveryRelayError("INVALID_CONFIGURATION")
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._journal = self._original_journal = journal
        self._sink = self._original_sink = sink
        self._scope_ref, self._source_ref, self._clock = scope_ref, source_ref, clock_utc
        self._closed, self._failed, self._fd, self._lock_fd, self._db = (
            False,
            False,
            None,
            None,
            None,
        )
        self._confirmed_cursor = 0
        try:
            source_path = str(Path(journal_path).resolve(strict=True))
            checkpoint = Path(checkpoint_path)
            if not checkpoint.is_absolute() or not checkpoint.name:
                raise ValueError
            self._checkpoint = str(checkpoint.parent.resolve(strict=True) / checkpoint.name)
            self._source_path = source_path
            self._source_identity = _file_identity(source_path)
            self._journal_path_attr = journal._path
            self._source_binding = (
                source_path,
                self._source_identity,
                scope_ref,
                source_ref,
                self._journal_path_attr,
            )
            if (
                os.path.realpath(journal._path) != source_path
                or journal._identity != self._source_identity
                or journal._scope != scope_ref
                or sink._source_ref != source_ref
            ):
                raise ValueError
            if (
                os.path.lexists(self._checkpoint)
                and _file_identity(self._checkpoint) == self._source_identity
            ):
                raise ValueError
            self._lock_path = self._checkpoint + ".lock"
            if os.path.realpath(self._lock_path) == source_path or (
                os.path.exists(self._lock_path) and os.path.samefile(self._lock_path, source_path)
            ):
                raise ValueError
            lock_fd = os.open(
                self._lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
            )
            self._lock_fd = lock_fd
            _lock(lock_fd)
            lock_info = os.fstat(lock_fd)
            self._lock_identity = (lock_info.st_dev, lock_info.st_ino)
            if (
                not stat.S_ISREG(lock_info.st_mode)
                or lock_info.st_nlink != 1
                or stat.S_IMODE(lock_info.st_mode) != 0o600
                or _file_identity(self._lock_path) != self._lock_identity
                or self._lock_identity == self._source_identity
            ):
                raise ValueError
            flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(self._checkpoint, flags | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
            except FileExistsError:
                fd = os.open(self._checkpoint, flags)
                created = False
            self._fd = fd
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or _file_identity(self._checkpoint) != (info.st_dev, info.st_ino)
                or (info.st_dev, info.st_ino) in (self._source_identity, self._lock_identity)
            ):
                raise ValueError
            self._checkpoint_identity = (info.st_dev, info.st_ino)
            self._manifest = _canonical(
                {
                    "schema": "sdk.gateway-recovery-relay/1",
                    "source_path": self._source_path,
                    "source_device": self._source_identity[0],
                    "source_inode": self._source_identity[1],
                    "scope_ref": scope_ref,
                    "source_ref": source_ref,
                    "journal_version": 2,
                    "destination": sink.destination_manifest,
                }
            ).decode("ascii")
            self._namespace = _hash(self._manifest.encode("ascii"))
            self._original_manifest = self._manifest
            self._original_namespace = self._namespace
            self._db = sqlite3.connect(
                Path(self._checkpoint).as_uri() + "?mode=rw",
                uri=True,
                timeout=1,
                isolation_level=None,
            )
            self._db.execute("PRAGMA synchronous=FULL")
            if self._db.execute("PRAGMA synchronous").fetchone() != (2,):
                raise ValueError
            if self._db.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                raise ValueError
            page_size = self._db.execute("PRAGMA page_size").fetchone()[0]
            if type(page_size) is not int or not 512 <= page_size <= 65536:
                raise ValueError
            max_pages = (4 * 1024 * 1024) // page_size
            if self._db.execute("PRAGMA page_count").fetchone()[0] > max_pages:
                raise ValueError
            self._db.execute(f"PRAGMA max_page_count={max_pages}")
            if created:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(_DDL)
                self._db.execute(
                    "INSERT INTO state VALUES (1,?,0,NULL,NULL,NULL,NULL,NULL,NULL)",
                    (self._manifest,),
                )
                self._db.execute("COMMIT")
            elif sys.platform == "darwin":
                self._maybe_adopt_darwin_reboot_device()
            self._validate_checkpoint()
            self._verify_source()
        except GatewayRecoveryRelayError:
            self._dispose_on_init_failure()
            raise
        except Exception:
            self._dispose_on_init_failure()
            raise GatewayRecoveryRelayError("INITIALIZATION_FAILED") from None

    def _maybe_adopt_darwin_reboot_device(self):
        """Allow macOS APFS ephemeral st_dev shift across reboots when inode/manifest match."""
        expected = json.loads(self._manifest)
        try:
            rows = self._db.execute("SELECT manifest FROM state").fetchall()
        except Exception:
            return
        if len(rows) != 1 or type(rows[0][0]) is not str:
            return
        stored_text = rows[0][0]
        try:
            stored = json.loads(stored_text)
            parent_dev = os.stat(os.path.dirname(self._source_path), follow_symlinks=False).st_dev
        except Exception:
            return
        if (
            type(stored) is not dict
            or set(stored) != set(expected)
            or type(stored.get("source_device")) is not int
            or stored["source_device"] < 0
            or stored["source_device"] == expected["source_device"]
            or self._source_identity[0] != parent_dev
            or any(stored[k] != expected[k] for k in expected if k != "source_device")
        ):
            return
        try:
            if _canonical(stored).decode("ascii") != stored_text:
                return
        except Exception:
            return
        self._manifest = stored_text
        self._original_manifest = stored_text
        self._namespace = _hash(stored_text.encode("ascii"))
        self._original_namespace = self._namespace

    def _dispose_on_init_failure(self):
        if self._db is not None:
            self._db.close()
            self._db = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _check_owner(self):
        if os.getpid() != self._pid:
            raise GatewayRecoveryRelayError("WRONG_PROCESS")
        if threading.current_thread() is not self._thread:
            raise GatewayRecoveryRelayError("WRONG_OWNER_THREAD")
        if (
            self._closed
            or self._failed
            or self._db is None
            or self._fd is None
            or self._lock_fd is None
        ):
            raise GatewayRecoveryRelayError("RELAY_UNAVAILABLE")
        if (
            self._journal is not self._original_journal
            or self._sink is not self._original_sink
            or (
                self._source_path,
                self._source_identity,
                self._scope_ref,
                self._source_ref,
                self._journal_path_attr,
            )
            != self._source_binding
            or self._manifest != self._original_manifest
            or self._namespace != self._original_namespace
        ):
            raise GatewayRecoveryRelayError("BINDING_CHANGED")

    def _check_files(self):
        self._check_owner()
        try:
            f = os.fstat(self._fd)
            guard = os.fstat(self._lock_fd)
            if (
                (f.st_dev, f.st_ino) != self._checkpoint_identity
                or f.st_nlink != 1
                or f.st_size > 4 * 1024 * 1024
                or stat.S_IMODE(f.st_mode) != 0o600
                or _file_identity(self._checkpoint) != self._checkpoint_identity
                or (guard.st_dev, guard.st_ino) != self._lock_identity
                or guard.st_nlink != 1
                or stat.S_IMODE(guard.st_mode) != 0o600
                or _file_identity(self._lock_path) != self._lock_identity
                or _file_identity(self._source_path) != self._source_identity
                or self._journal._identity != self._source_identity
                or self._journal._path != self._journal_path_attr
                or os.path.realpath(self._journal._path) != self._source_path
                or self._journal._scope != self._scope_ref
                or self._journal.schema_version != 2
            ):
                raise ValueError
            self._sink.assert_current()
        except Exception:
            raise GatewayRecoveryRelayError("BINDING_CHANGED") from None

    def _row(self):
        rows = self._db.execute(
            "SELECT schema,manifest,cursor,anchor_cursor,anchor_digest,cursor_digest,"
            "pending_cursor,pending_digest,pending_envelope FROM state"
        ).fetchall()
        if len(rows) != 1:
            raise ValueError
        cursor = rows[0][2]
        if type(cursor) is int and cursor < self._confirmed_cursor:
            self._failed = True
            raise GatewayRecoveryRelayError("CHECKPOINT_INVALID")
        return rows[0]

    def _validate_checkpoint(self):
        self._check_files()
        tables = self._db.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if (
            tables != [("state", _DDL)]
            or self._db.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') OR (type='index' AND sql IS NOT NULL) LIMIT 1"
            ).fetchone()
        ):
            raise ValueError
        (
            schema,
            manifest,
            cursor,
            anchor,
            anchor_hash,
            cursor_hash,
            pending,
            pending_hash,
            envelope,
        ) = self._row()

        def digest(value):
            return (
                type(value) is str
                and len(value) == 64
                and all(c in "0123456789abcdef" for c in value)
            )

        if (
            schema != 1
            or type(schema) is not int
            or manifest != self._manifest
            or type(cursor) is not int
            or not 0 <= cursor < 2**63
            or ((anchor is None) != (anchor_hash is None))
            or (
                anchor is not None
                and (type(anchor) is not int or anchor != 1 or not digest(anchor_hash))
            )
            or (cursor == 0 and cursor_hash is not None)
            or (cursor > 0 and (anchor is None or not digest(cursor_hash)))
            or ((pending is None) != (pending_hash is None))
            or ((pending is None) != (envelope is None))
            or (pending is not None and anchor is None)
            or (
                pending is not None
                and (
                    type(pending) is not int
                    or pending != cursor + 1
                    or not digest(pending_hash)
                    or type(envelope) is not str
                    or len(envelope.encode("utf-8")) > 65536
                )
            )
        ):
            raise ValueError
        if pending is not None:
            decoded = json.loads(envelope)
            if _bounded(decoded).decode("ascii") != envelope:
                raise ValueError
        self._confirmed_cursor = cursor

    def _project(self, record):
        if record.source_ref != self._source_ref:
            raise GatewayRecoveryRelayError("SOURCE_SCOPE_MISMATCH")
        try:
            event = recovery_journal_event(
                record, scope_ref=self._scope_ref, journal_version=2
            ).to_dict()
        except RecoveryJournalEventError as exc:
            code = "EVENT_TOO_LARGE" if exc.code == "EVENT_TOO_LARGE" else "SOURCE_RECORD_INVALID"
            raise GatewayRecoveryRelayError(code) from None
        encoded = _canonical(event)
        return event, _hash(encoded)

    def _at(self, cursor):
        page = self._journal.read_after(cursor - 1, limit=1)
        if len(page.records) != 1 or page.records[0].cursor != cursor:
            raise GatewayRecoveryRelayError("SOURCE_HISTORY_CHANGED")
        record = page.records[0]
        event, digest = self._project(record)
        return event, digest

    def _event_id(self, cursor):
        return "gateway-recovery-v1:" + _hash(_canonical([self._namespace, cursor]))

    def _verify_source(self):
        self._check_files()
        _, _, cursor, anchor, anchor_hash, cursor_hash, pending, pending_hash, envelope = (
            self._row()
        )
        try:
            first = self._journal.read_after(0, limit=1)
            if anchor is None and first.records:
                if len(first.records) != 1 or first.records[0].cursor != 1:
                    raise GatewayRecoveryRelayError("SOURCE_HISTORY_CHANGED")
                _, anchor_hash = self._project(first.records[0])
                self._transaction(
                    "UPDATE state SET anchor_cursor=?,anchor_digest=?", (1, anchor_hash)
                )
                anchor = 1
                first = self._journal.read_after(0, limit=1)
            if anchor is not None:
                if len(first.records) != 1 or first.records[0].cursor != anchor:
                    raise GatewayRecoveryRelayError("SOURCE_HISTORY_CHANGED")
                _, actual = self._project(first.records[0])
                if actual != anchor_hash:
                    raise GatewayRecoveryRelayError("SOURCE_HISTORY_CHANGED")
            if cursor:
                _, actual = self._at(cursor)
                if actual != cursor_hash:
                    raise GatewayRecoveryRelayError("SOURCE_HISTORY_CHANGED")
            if pending is not None:
                event, actual = self._at(pending)
                if actual != pending_hash:
                    raise GatewayRecoveryRelayError("SOURCE_HISTORY_CHANGED")
                item = json.loads(envelope)
                if (
                    type(item) is not dict
                    or set(item)
                    != {
                        "schema_version",
                        "event_id",
                        "event_type",
                        "occurred_at",
                        "data",
                        "binding_ref",
                        "source_ref",
                    }
                    or type(item["schema_version"]) is not int
                    or item["schema_version"] != 2
                    or item["event_id"] != self._event_id(pending)
                    or item["event_type"] != _EVENT_TYPE
                    or item["binding_ref"] != self._sink._binding_ref
                    or item["source_ref"] != self._source_ref
                    or type(item["occurred_at"]) not in (int, float)
                    or _canonical(item["data"])
                    != _canonical(
                        {
                            "schema": "sdk.gateway_recovery_relay/v1",
                            "envelope_time_basis": "FIRST_RELAY_ENQUEUE",
                            "event": event,
                        }
                    )
                ):
                    raise GatewayRecoveryRelayError("CHECKPOINT_INVALID")
            return first
        except GatewayRecoveryRelayError:
            raise
        except Exception:
            raise GatewayRecoveryRelayError("SOURCE_HISTORY_CHANGED") from None

    def _transaction(self, sql, values):
        try:
            self._check_files()
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute(sql, values)
            self._commit()
        except Exception:
            self._failed = True
            try:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
            except Exception:  # noqa: S110 - the original outcome remains uncertain
                pass
            raise GatewayRecoveryRelayError("CHECKPOINT_UNCERTAIN") from None

    def _commit(self):
        self._db.execute("COMMIT")

    def pump(self, *, max_records=16, max_bytes=65536):
        if (
            type(max_records) is not int
            or not 1 <= max_records <= 256
            or type(max_bytes) is not int
            or not 1 <= max_bytes <= 16 * 1024 * 1024
        ):
            raise GatewayRecoveryRelayError("INVALID_BUDGET")
        self._check_owner()
        delivered, used = 0, 0
        try:
            self._validate_checkpoint()
            while delivered < max_records:
                first = self._verify_source()
                _, _, cursor, anchor, anchor_hash, _, pending, pending_hash, envelope = self._row()
                if pending is None:
                    page = self._journal.read_after(cursor, limit=1)
                    if not page.records:
                        return GatewayRecoveryRelayStatus("IDLE", cursor, 0, delivered)
                    record = page.records[0]
                    if record.cursor != cursor + 1:
                        raise GatewayRecoveryRelayError("SOURCE_HISTORY_CHANGED")
                    event, digest = self._project(record)
                    now = self._clock()
                    if type(now) not in (int, float) or not -62135596800 <= now <= 253402300799:
                        raise GatewayRecoveryRelayError("CLOCK_UNAVAILABLE")
                    item = {
                        "schema_version": 2,
                        "event_id": self._event_id(record.cursor),
                        "event_type": _EVENT_TYPE,
                        "occurred_at": float(now),
                        "data": {
                            "schema": "sdk.gateway_recovery_relay/v1",
                            "envelope_time_basis": "FIRST_RELAY_ENQUEUE",
                            "event": event,
                        },
                        "binding_ref": self._sink._binding_ref,
                        "source_ref": self._source_ref,
                    }
                    try:
                        item = self._sink.validate_envelope(item)
                        envelope = _bounded(item).decode("ascii")
                    except AuthenticatedMonitorSinkError as exc:
                        code = (
                            "EVENT_TOO_LARGE"
                            if exc.code == "EVENT_TOO_LARGE"
                            else "BINDING_CHANGED"
                        )
                        raise GatewayRecoveryRelayError(code) from None
                    except Exception:
                        raise GatewayRecoveryRelayError("EVENT_TOO_LARGE") from None
                    if len(envelope.encode("ascii")) + used > max_bytes:
                        return GatewayRecoveryRelayStatus("BUDGET_EXCEEDED", cursor, 0, delivered)
                    if anchor is None:
                        if len(first.records) != 1 or first.records[0].cursor != 1:
                            raise GatewayRecoveryRelayError("SOURCE_HISTORY_CHANGED")
                        _, anchor_hash = self._project(first.records[0])
                        anchor = 1
                    self._transaction(
                        "UPDATE state SET anchor_cursor=?,anchor_digest=?,pending_cursor=?,"
                        "pending_digest=?,pending_envelope=?",
                        (anchor, anchor_hash, record.cursor, digest, envelope),
                    )
                    pending, pending_hash = record.cursor, digest
                size = len(envelope.encode("ascii"))
                if used + size > max_bytes:
                    return GatewayRecoveryRelayStatus("BUDGET_EXCEEDED", cursor, 1, delivered)
                self._verify_source()
                try:
                    self._sink.append(json.loads(envelope))
                except AuthenticatedMonitorSinkError as exc:
                    if exc.code == "EVENT_TOO_LARGE":
                        raise GatewayRecoveryRelayError("EVENT_TOO_LARGE") from None
                    return GatewayRecoveryRelayStatus("SINK_UNAVAILABLE", cursor, 1, delivered)
                except Exception:
                    return GatewayRecoveryRelayStatus("SINK_UNAVAILABLE", cursor, 1, delivered)
                self._verify_source()
                self._transaction(
                    "UPDATE state SET cursor=?,cursor_digest=?,pending_cursor=NULL,"
                    "pending_digest=NULL,pending_envelope=NULL",
                    (pending, pending_hash),
                )
                self._confirmed_cursor = pending
                delivered, used = delivered + 1, used + size
            return GatewayRecoveryRelayStatus(
                "BUDGET_EXHAUSTED", self._confirmed_cursor, 0, delivered
            )
        except GatewayRecoveryRelayError:
            raise
        except Exception:
            raise GatewayRecoveryRelayError("RELAY_UNAVAILABLE") from None

    def status(self):
        self._check_owner()
        try:
            self._validate_checkpoint()
            row = self._row()
            return GatewayRecoveryRelayStatus("READY", row[2], int(row[6] is not None))
        except Exception:
            raise GatewayRecoveryRelayError("CHECKPOINT_INVALID") from None

    def close(self):
        if os.getpid() != self._pid:
            raise GatewayRecoveryRelayError("WRONG_PROCESS")
        if threading.current_thread() is not self._thread:
            raise GatewayRecoveryRelayError("WRONG_OWNER_THREAD")
        if self._closed:
            return
        try:
            if self._db is not None:
                self._db.close()
                self._db = None
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            if self._lock_fd is not None:
                _unlock(self._lock_fd)
                os.close(self._lock_fd)
                self._lock_fd = None
        except Exception:
            self._failed = True
            raise GatewayRecoveryRelayError("CLOSE_UNCERTAIN") from None
        self._closed = True
