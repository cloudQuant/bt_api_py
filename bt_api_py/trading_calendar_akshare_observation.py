"""Explicit, single-attempt AKShare observation sidecar for trusted POSIX roots.

A caller supplies its run and observation IDs before calling. A durable-looking
claim fences this observation ID before the provider is invoked; an uncertain
write or missing result is inspected with the original IDs, never retried here.
Bounded typed child-IPC rows, provider projections and a time summary are private
internal data. Retained IPC rows are not upstream response bytes. Their digest
and the content digest are consistency checks, not source authentication.
Local hashes and IDs do
not authenticate a source, writer, scope, trading day, validity or coverage.
This module does not read or alter CURRENT, assemble mock data or publish.

Storage relies on cooperating writers on a declared trusted local POSIX host;
ABA, hostile replacement, OS crashes and power-loss durability are unproven.
The provider bounds parent wait and IPC output, not child CPU, memory, response
decompression, uninterruptible waits or descendant lifetime. No timeout grants
permission for an automatic retry. Python callers must not expose private files.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import math
import os
import re
import sqlite3
import stat
import time
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from bt_api_py import trading_calendar_detail_batch as detail
from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py.trading_calendar_preview_reports import (
    TRUSTED_LOCAL_POSIX,
    _directory_matches,
    _identity,
    _open_directory,
    _supported,
)

_INTENT = "bt_api.akshare-observation-intent/1"
_RESULT = "bt_api.akshare-observation-result/2"
_LIMIT = 3 * 1024 * 1024
_CONTRACT = re.compile(r"[A-Za-z]{1,8}[0-9]{3,4}\Z", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_CAPTURE = re.compile(r"akshare\.capture\.[0-9a-f]{32}\Z", re.ASCII)
_CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z", re.ASCII)
_VENUES = frozenset({"SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX"})
_REGISTRY = "reservation-registry.sqlite3"
_REGISTRY_MARKER = "reservation-registry-created.json"
_RESERVATION_PENDING = "reservation-pending.json"
_REGISTRY_SCHEMA = "bt_api.akshare-observation-reservation/1"
_ABORT_SCHEMA = "bt_api.akshare-observation-reservation-abort/1"
_REGISTRY_DDL = (
    "CREATE TABLE reservations (reservation_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL UNIQUE, run_id TEXT NOT NULL UNIQUE, payload BLOB NOT NULL)",
    "CREATE TABLE observation_ids (observation_id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL REFERENCES reservations(reservation_id))",
    "CREATE INDEX observation_ids_reservation ON observation_ids (reservation_id)",
    "CREATE TRIGGER reservations_no_update BEFORE UPDATE ON reservations BEGIN SELECT RAISE(ABORT,'immutable reservation'); END",
    "CREATE TRIGGER reservations_no_delete BEFORE DELETE ON reservations BEGIN SELECT RAISE(ABORT,'immutable reservation'); END",
    "CREATE TRIGGER observation_ids_no_update BEFORE UPDATE ON observation_ids BEGIN SELECT RAISE(ABORT,'immutable observation'); END",
    "CREATE TRIGGER observation_ids_no_delete BEFORE DELETE ON observation_ids BEGIN SELECT RAISE(ABORT,'immutable observation'); END",
)
_STATUSES = frozenset(
    {
        "SUCCESS",
        "EMPTY",
        "MISSING_TRADING_TIME",
        "STRUCTURE_CHANGED",
        "DEPENDENCY_UNAVAILABLE",
        "VERSION_MISMATCH",
        "FETCH_ERROR",
        "FETCH_TIMEOUT",
        "UNKNOWN",
    }
)
_TIME_STATUS = frozenset(
    {
        "SYNTAX_CANDIDATE",
        "NEEDS_REVIEW",
        "MISSING_FIELD",
        "DUPLICATE_FIELD",
        "INVALID_FIELD",
        "INVALID_FIELDS",
        "INPUT_TOO_LONG",
    }
)
_REASONS = frozenset(
    {
        "AMBIGUOUS_DAY_LABEL",
        "AMBIGUOUS_DAY_TRANSITION",
        "INFERRED_DAY_TRANSITION",
        "INVALID_TIME_RANGE",
        "LABEL_WITHOUT_RANGE",
        "NO_LABELED_RANGES",
        "OVERLAPPING_RANGES",
        "UNLABELED_RANGE",
        "UNRECOGNIZED_TEXT",
        "UNSAFE_NORMALIZATION",
        "DUPLICATE_VALUES_OMITTED",
    }
)


def _open(root, create):
    fds, chain = _open_directory(root)
    try:
        parent = fds[-1]
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir("akshare-observations", 0o700, dir_fd=parent)
        info = os.stat("akshare-observations", dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("invalid private directory")
        fd = os.open(
            "akshare-observations", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        fds.append(fd)
        if _identity(os.fstat(fd)) != _identity(info):
            raise ValueError("directory changed")
        chain.append((parent, "akshare-observations", fd, _identity(info)))
        if not _directory_matches(chain):
            raise ValueError("directory changed")
        return fds, chain
    except BaseException:
        runs._close(fds)
        raise


def _arguments(root, observation_id, run_id, filesystem):
    return (
        filesystem == TRUSTED_LOCAL_POSIX
        and _supported()
        and detail._safe_root(root)
        and runs._id(observation_id)
        and runs._id(run_id)
        and observation_id != run_id
    )


def _settings(venue, contract, requested_date, timeout):
    return (
        type(venue) is str
        and venue in _VENUES
        and type(contract) is str
        and _CONTRACT.fullmatch(contract) is not None
        and type(requested_date) is date
        and type(timeout) in (int, float)
        and 0 < timeout <= 60
        and math.isfinite(timeout)
    )


def _registry_file(fd, name, required=False):
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        if required:
            raise ValueError("registry file unavailable") from None
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("invalid registry file")
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink)


def _registry_sidecars(fd):
    for suffix in ("-wal", "-shm", "-journal"):
        if _registry_file(fd, _REGISTRY + suffix) is not None:
            raise ValueError("registry sidecar requires reconciliation")


def _pending(fd, chain):
    raw = storage._read(fd, chain, _RESERVATION_PENDING, _LIMIT)
    if raw is not None:
        _registry_file(fd, _RESERVATION_PENDING, required=True)
    return raw


def _require_no_pending(fd, chain):
    if _pending(fd, chain) is not None:
        raise ValueError("reservation pending original ID reconciliation")


def _require_no_old_reservations(fd, chain):
    # Only initialization scans; exhaustion, not a capped prefix, proves absence.
    if not _directory_matches(chain):
        raise ValueError("directory changed")
    with os.scandir(fd) as entries:
        for entry in entries:
            if entry.name.startswith("reservation-attempt-") or entry.name == _RESERVATION_PENDING:
                raise ValueError("prior reservation evidence exists")
    if not _directory_matches(chain):
        raise ValueError("directory changed")


def _clear_pending(fd, chain, expected):
    before = _registry_file(fd, _RESERVATION_PENDING, required=True)
    if _pending(fd, chain) != expected or not _directory_matches(chain):
        raise ValueError("pending reservation changed")
    if _registry_file(fd, _RESERVATION_PENDING, required=True) != before:
        raise ValueError("pending identity changed")
    os.unlink(_RESERVATION_PENDING, dir_fd=fd)
    os.fsync(fd)
    if not _directory_matches(chain) or _pending(fd, chain) is not None:
        raise ValueError("pending removal uncertain")


@contextlib.contextmanager
def _registry(root, fd, chain):
    """Open under the observation lock; an uncertain initialization stays blocked."""
    marker = storage._read(fd, chain, _REGISTRY_MARKER, _LIMIT)
    current = _registry_file(fd, _REGISTRY)
    if marker is None:
        if current is not None:
            raise ValueError("unmarked registry")
        _registry_sidecars(fd)
        _require_no_old_reservations(fd, chain)
        marker_bytes = storage._envelope({"schema": _REGISTRY_SCHEMA})
        state, _ = runs._write_once(fd, chain, _REGISTRY_MARKER, marker_bytes)
        if state != "COMMITTED":
            raise ValueError("registry initialization uncertain")
        dbfd = os.open(
            _REGISTRY,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=fd,
        )
        try:
            os.fchmod(dbfd, 0o600)
            os.fsync(dbfd)
        finally:
            os.close(dbfd)
        os.fsync(fd)
        initialize = True
    else:
        if storage._unwrap(marker)[0] != {"schema": _REGISTRY_SCHEMA} or current is None:
            raise ValueError("registry marker or database unavailable")
        initialize = False
    _registry_file(fd, _REGISTRY_MARKER, required=True)
    before = _registry_file(fd, _REGISTRY, required=True)
    _registry_sidecars(fd)
    if not _directory_matches(chain):
        raise ValueError("directory changed")
    path = Path(root) / "akshare-observations" / _REGISTRY
    connection = sqlite3.connect(
        "file:" + quote(str(path), safe="/") + "?mode=rw",
        uri=True,
        timeout=0,
        isolation_level=None,
    )
    try:
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 65536)
        if _registry_file(fd, _REGISTRY, required=True) != before or not _directory_matches(chain):
            raise ValueError("registry identity changed")
        if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
            raise ValueError("invalid registry journal mode")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        if initialize:
            connection.execute("BEGIN IMMEDIATE")
            for statement in _REGISTRY_DDL:
                connection.execute(statement)
            connection.execute("COMMIT")
        definitions = dict(
            connection.execute("SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
        )
        if definitions != {statement.split()[2]: statement for statement in _REGISTRY_DDL}:
            raise ValueError("registry schema changed")
        yield connection
        if _registry_file(fd, _REGISTRY, required=True) != before or not _directory_matches(chain):
            raise ValueError("registry identity changed")
        _registry_sidecars(fd)
    finally:
        connection.close()


def _reservation_payload(reservation_id, batch_id, run_id, requests):
    if (
        not all(runs._id(x) for x in (reservation_id, batch_id, run_id))
        or len({reservation_id, batch_id, run_id}) != 3
        or type(requests) is not list
        or not 2 <= len(requests) <= 8
    ):
        raise ValueError("invalid reservation")
    rows = []
    for item in requests:
        if (
            type(item) is not dict
            or set(item)
            != {
                "observation_id",
                "scope_sha256",
                "venue",
                "contract",
                "requested_date",
                "timeout",
            }
            or not runs._id(item["observation_id"])
            or type(item["scope_sha256"]) is not str
            or not _HASH.fullmatch(item["scope_sha256"])
            or not _settings(
                item["venue"], item["contract"], item["requested_date"], item["timeout"]
            )
        ):
            raise ValueError("invalid reservation request")
        row = {**item, "requested_date": item["requested_date"].isoformat()}
        row["request_sha256"] = hashlib.sha256(storage._json(row)).hexdigest()
        rows.append(row)
    if len({row["observation_id"] for row in rows} | {reservation_id, batch_id, run_id}) != len(
        rows
    ) + 3 or len({row["scope_sha256"] for row in rows}) != len(rows):
        raise ValueError("duplicate reservation identities")
    return {
        "schema": _REGISTRY_SCHEMA,
        "reservation_id": reservation_id,
        "batch_id": batch_id,
        "run_id": run_id,
        "requests": rows,
        "source_authentication": "NOT_PROVEN",
    }


def _reservation_ids(payload):
    return [payload["reservation_id"], payload["batch_id"], payload["run_id"]] + [
        row["observation_id"] for row in payload["requests"]
    ]


def _identity_in_registry(db, value):
    return (
        db.execute("SELECT 1 FROM observation_ids WHERE observation_id=?", (value,)).fetchone()
        is not None
        or db.execute(
            "SELECT 1 FROM reservations WHERE reservation_id=? OR batch_id=? OR run_id=?",
            (value, value, value),
        ).fetchone() is not None
    )


def _observation_files_absent(fd, chain, identities):
    for value in identities:
        if (storage._read(fd, chain, f"intent-{value}.json", _LIMIT) is not None
                or storage._read(fd, chain, f"result-{value}.json", _LIMIT) is not None):
            return False
    return True


def _validate_attempt(fd, chain, reservation_id):
    name = f"reservation-attempt-{reservation_id}.json"
    raw = storage._read(fd, chain, name, _LIMIT)
    _registry_file(fd, name, required=True)
    if raw is None or storage._unwrap(raw)[0] != {
        "schema": _REGISTRY_SCHEMA, "reservation_id": reservation_id,
    }:
        raise ValueError("original reservation attempt unavailable")


def reserve_observations(*, root, reservation_id, batch_id, run_id, requests, filesystem):
    """Reserve observation IDs atomically; local declarations do not authenticate sources."""
    result = {
        "reservation_id": reservation_id,
        "batch_id": batch_id,
        "run_id": run_id,
        "source_authentication": "NOT_PROVEN",
    }
    try:
        if filesystem != TRUSTED_LOCAL_POSIX or not _supported() or not detail._safe_root(root):
            raise ValueError("invalid filesystem")
        payload = _reservation_payload(reservation_id, batch_id, run_id, requests)
    except Exception:
        return {**result, "status": "NOT_COMMITTED", "reason": "INVALID_RESERVATION"}
    attempted = False
    fds, lock = None, None
    try:
        fds, chain = _open(root, True)
        lock, ident = storage._lock(fds[-1], chain)
        if os.fstat(lock).st_nlink != 1:
            raise ValueError("invalid lock")
        storage._check_lock(fds[-1], chain, lock, ident)
        _require_no_pending(fds[-1], chain)
        with _registry(root, fds[-1], chain) as db:
            attempt_name = f"reservation-attempt-{reservation_id}.json"
            if storage._read(fds[-1], chain, attempt_name, _LIMIT) is not None:
                raise ValueError("reservation attempt already exists")
            db.execute("BEGIN IMMEDIATE")
            identities = _reservation_ids(payload)
            if (not _observation_files_absent(fds[-1], chain, identities)
                    or any(_identity_in_registry(db, value) for value in identities)):
                db.execute("ROLLBACK")
                return {**result, "status": "NOT_COMMITTED", "reason": "ID_ALREADY_USED"}
            encoded = storage._envelope(payload)
            attempted = True
            state, _ = runs._write_once(fds[-1], chain, _RESERVATION_PENDING, encoded)
            if state != "COMMITTED" or _pending(fds[-1], chain) != encoded:
                raise ValueError("reservation pending installation uncertain")
            state, _ = runs._write_once(
                fds[-1], chain, attempt_name,
                storage._envelope({"schema": _REGISTRY_SCHEMA, "reservation_id": reservation_id}),
            )
            if state != "COMMITTED":
                raise ValueError("reservation attempt installation uncertain")
            db.execute(
                "INSERT INTO reservations VALUES(?,?,?,?)",
                (reservation_id, batch_id, run_id, encoded),
            )
            db.executemany(
                "INSERT INTO observation_ids VALUES(?,?)",
                [(row["observation_id"], reservation_id) for row in payload["requests"]],
            )
            db.execute("COMMIT")
            if db.execute(
                "SELECT payload FROM reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone() != (encoded,):
                raise ValueError("reservation readback failed")
            if set(
                db.execute(
                    "SELECT observation_id FROM observation_ids WHERE reservation_id=? LIMIT 9",
                    (reservation_id,),
                )
            ) != {(row["observation_id"],) for row in payload["requests"]}:
                raise ValueError("reservation mappings changed")
            storage._check_lock(fds[-1], chain, lock, ident)
        storage._check_lock(fds[-1], chain, lock, ident)
        _clear_pending(fds[-1], chain, encoded)
        return {**result, "status": "COMMITTED", "reason": "NONE"}
    except BaseException as exc:
        if not isinstance(exc, Exception):
            with contextlib.suppress(Exception):
                exc.reservation_id = reservation_id
                exc.record_state = "COMMIT_UNKNOWN"
            raise
        return {
            **result,
            "status": "UNKNOWN",
            "reason": "RESERVATION_RECONCILE_ORIGINAL_ID",
            "attempted": attempted,
        }
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        if fds is not None:
            runs._close(fds)


def _check_reservation(
    db, observation_id, run_id, venue, contract, requested_date, timeout, reservation
):
    match = db.execute(
        "SELECT reservation_id FROM observation_ids WHERE observation_id=?",
        (observation_id,),
    ).fetchone()
    if db.execute("SELECT 1 FROM observation_ids WHERE observation_id=?", (run_id,)).fetchone():
        raise ValueError("run ID used in another identity role")
    if db.execute(
        "SELECT 1 FROM reservations WHERE reservation_id=? OR batch_id=?",
        (run_id, run_id),
    ).fetchone():
        raise ValueError("run ID belongs to another reservation")
    if match is None:
        if reservation is not None:
            raise ValueError("reservation unavailable")
        if _identity_in_registry(db, observation_id):
            raise ValueError("observation ID used in another identity role")
        return
    if (
        type(reservation) is not dict
        or set(reservation) != {"reservation_id", "batch_id", "scope_sha256"}
        or match != (reservation["reservation_id"],)
    ):
        raise ValueError("reservation required")
    found = db.execute(
        "SELECT batch_id,run_id,payload FROM reservations WHERE reservation_id=?", match
    ).fetchone()
    if found is None or found[:2] != (reservation["batch_id"], run_id):
        raise ValueError("reservation owner mismatch")
    payload, _ = storage._unwrap(found[2])
    if (
        type(payload) is not dict
        or payload.get("schema") != _REGISTRY_SCHEMA
        or (
            payload.get("reservation_id"),
            payload.get("batch_id"),
            payload.get("run_id"),
        )
        != (reservation["reservation_id"], reservation["batch_id"], run_id)
        or type(payload.get("requests")) is not list
    ):
        raise ValueError("invalid reservation record")
    rows = [row for row in payload["requests"] if row["observation_id"] == observation_id]
    if len(rows) != 1:
        raise ValueError("reservation mapping mismatch")
    if set(db.execute(
        "SELECT observation_id FROM observation_ids WHERE reservation_id=? LIMIT 9", match
    )) != {(item["observation_id"],) for item in payload["requests"]}:
        raise ValueError("reservation group incomplete")
    row = rows[0]
    check = {
        key: row[key]
        for key in (
            "observation_id",
            "scope_sha256",
            "venue",
            "contract",
            "requested_date",
            "timeout",
        )
    }
    if (
        row["request_sha256"] != hashlib.sha256(storage._json(check)).hexdigest()
        or (row["scope_sha256"], row["venue"], row["contract"], row["requested_date"])
        != (reservation["scope_sha256"], venue, contract, requested_date.isoformat())
        or timeout > row["timeout"]
    ):
        raise ValueError("reserved request mismatch")


def _pending_payload(raw, reservation_id):
    payload, digest = storage._unwrap(raw)
    if type(payload) is not dict or payload.get("reservation_id") != reservation_id:
        raise ValueError("pending reservation ID mismatch")
    requests = []
    for row in payload["requests"]:
        request = {key: row[key] for key in (
            "observation_id", "scope_sha256", "venue", "contract", "requested_date", "timeout"
        )}
        request["requested_date"] = date.fromisoformat(request["requested_date"])
        requests.append(request)
    if _reservation_payload(reservation_id, payload["batch_id"], payload["run_id"], requests) != payload:
        raise ValueError("pending reservation invalid")
    return payload, digest


def _abort_record(payload, digest):
    return {
        "schema": _ABORT_SCHEMA,
        "reservation_id": payload["reservation_id"],
        "batch_id": payload["batch_id"],
        "run_id": payload["run_id"],
        "original_payload_sha256": digest,
        "original_payload": payload,
        "observation_ids": [row["observation_id"] for row in payload["requests"]],
        "source_authentication": "NOT_PROVEN",
    }


def _verify_abort(db, fd, chain, reservation_id, encoded, expected_pending=None):
    record, _ = storage._unwrap(encoded)
    if type(record) is not dict or record.get("schema") != _ABORT_SCHEMA:
        raise ValueError("abort tombstone unavailable")
    raw = storage._envelope(record["original_payload"])
    payload, digest = _pending_payload(raw, reservation_id)
    if (record != _abort_record(payload, digest)
            or (expected_pending is not None and expected_pending != raw)
            or db.execute("SELECT batch_id,run_id,payload FROM reservations WHERE reservation_id=?",
                          (reservation_id,)).fetchone() != (payload["batch_id"], payload["run_id"], encoded)):
        raise ValueError("abort tombstone binding mismatch")
    identities = _reservation_ids(payload)
    mappings = set(db.execute(
        "SELECT observation_id FROM observation_ids WHERE reservation_id=? LIMIT 12",
        (reservation_id,),
    ))
    if mappings != {(value,) for value in identities}:
        raise ValueError("abort identities incomplete")
    _validate_attempt(fd, chain, reservation_id)
    if not _observation_files_absent(fd, chain, identities):
        raise ValueError("observation evidence conflicts with abort")
    return payload, digest


def _sync_registry(fd, chain):
    before = _registry_file(fd, _REGISTRY, required=True)
    dbfd = os.open(_REGISTRY, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        info = os.fstat(dbfd)
        if (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink) != before:
            raise ValueError("registry identity changed")
        os.fsync(dbfd)
    finally:
        os.close(dbfd)
    os.fsync(fd)
    if (_registry_file(fd, _REGISTRY, required=True) != before or not _directory_matches(chain)):
        raise ValueError("registry identity changed")
    _registry_sidecars(fd)


def abort_reservation(*, root, reservation_id, filesystem):
    """Permanently seal an original uncommitted group; never invoke a collector."""
    result = {"status": "UNKNOWN", "reservation_id": reservation_id,
              "source_authentication": "NOT_PROVEN"}
    if (filesystem != TRUSTED_LOCAL_POSIX or not _supported() or not detail._safe_root(root)
            or not runs._id(reservation_id)):
        return result
    fds, lock = None, None
    try:
        fds, chain = _open(root, False)
        lock, ident = storage._lock(fds[-1], chain)
        if os.fstat(lock).st_nlink != 1:
            raise ValueError("invalid lock")
        storage._check_lock(fds[-1], chain, lock, ident)
        pending = _pending(fds[-1], chain)
        if pending is None and storage._read(fds[-1], chain, _REGISTRY_MARKER, _LIMIT) is None:
            return result
        with _registry(root, fds[-1], chain) as db:
            if pending is None:
                found = db.execute("SELECT payload FROM reservations WHERE reservation_id=?",
                                   (reservation_id,)).fetchone()
                if found is None:
                    raise ValueError("original pending reservation unavailable")
                _, digest = _verify_abort(db, fds[-1], chain, reservation_id, found[0])
                return {**result, "status": "ABORTED_RELEASE_UNPROVEN", "payload_sha256": digest}
            payload, digest = _pending_payload(pending, reservation_id)
            _validate_attempt(fds[-1], chain, reservation_id)
            identities = _reservation_ids(payload)
            if not _observation_files_absent(fds[-1], chain, identities):
                raise ValueError("observation may have been attempted")
            db.execute("BEGIN IMMEDIATE")
            found = db.execute("SELECT payload FROM reservations WHERE reservation_id=?",
                               (reservation_id,)).fetchone()
            encoded = storage._envelope(_abort_record(payload, digest))
            if found is not None:
                _verify_abort(db, fds[-1], chain, reservation_id, found[0], pending)
                db.execute("ROLLBACK")
            else:
                if any(_identity_in_registry(db, value) for value in identities):
                    raise ValueError("reservation identity already used")
                db.execute("INSERT INTO reservations VALUES(?,?,?,?)",
                           (reservation_id, payload["batch_id"], payload["run_id"], encoded))
                db.executemany("INSERT INTO observation_ids VALUES(?,?)",
                               [(value, reservation_id) for value in identities])
                db.execute("COMMIT")
                _verify_abort(db, fds[-1], chain, reservation_id, encoded, pending)
            storage._check_lock(fds[-1], chain, lock, ident)
        _sync_registry(fds[-1], chain)
        storage._check_lock(fds[-1], chain, lock, ident)
        _clear_pending(fds[-1], chain, pending)
        return {**result, "status": "ABORTED", "payload_sha256": digest}
    except BaseException as exc:
        if not isinstance(exc, Exception):
            with contextlib.suppress(Exception):
                exc.reservation_id = reservation_id
                exc.record_state = "COMMIT_UNKNOWN"
            raise
        return result
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        if fds is not None:
            runs._close(fds)


def reconcile_reservation(*, root, reservation_id, filesystem):
    """Clear a pending fence only when the exact original group is fully committed."""
    result = {"status": "UNKNOWN", "reservation_id": reservation_id,
              "source_authentication": "NOT_PROVEN"}
    if (filesystem != TRUSTED_LOCAL_POSIX or not _supported() or not detail._safe_root(root)
            or not runs._id(reservation_id)):
        return result
    fds, lock = None, None
    try:
        fds, chain = _open(root, False)
        lock, ident = storage._lock(fds[-1], chain)
        if os.fstat(lock).st_nlink != 1:
            raise ValueError("invalid lock")
        storage._check_lock(fds[-1], chain, lock, ident)
        raw = _pending(fds[-1], chain)
        if raw is None:
            if storage._read(fds[-1], chain, _REGISTRY_MARKER, _LIMIT) is None:
                return result
            with _registry(root, fds[-1], chain) as db:
                found = db.execute("SELECT payload FROM reservations WHERE reservation_id=?",
                                   (reservation_id,)).fetchone()
                if found is not None:
                    _, digest = _verify_abort(db, fds[-1], chain, reservation_id, found[0])
                    return {**result, "status": "ABORTED_RELEASE_UNPROVEN", "payload_sha256": digest}
            return result
        payload, digest = _pending_payload(raw, reservation_id)
        aborted = False
        with _registry(root, fds[-1], chain) as db:
            found = db.execute(
                "SELECT batch_id,run_id,payload FROM reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            if found == (payload["batch_id"], payload["run_id"], raw):
                ids = set(db.execute(
                    "SELECT observation_id FROM observation_ids WHERE reservation_id=? LIMIT 9",
                    (reservation_id,),
                ))
                if ids != {(row["observation_id"],) for row in payload["requests"]}:
                    raise ValueError("original reservation mapping incomplete")
            elif found is not None:
                _verify_abort(db, fds[-1], chain, reservation_id, found[2], raw)
                aborted = True
            else:
                raise ValueError("original reservation not proven committed")
        if aborted:
            _sync_registry(fds[-1], chain)
        storage._check_lock(fds[-1], chain, lock, ident)
        _clear_pending(fds[-1], chain, raw)
        return {**result, "status": "ABORTED" if aborted else "VISIBLE_UNAUTHENTICATED",
                "payload_sha256": digest}
    except Exception:
        return result
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        if fds is not None:
            runs._close(fds)


def show_reservation(*, root, reservation_id, filesystem):
    """Inspect the original local reservation without granting a new attempt."""
    result = {
        "status": "UNKNOWN",
        "reservation_id": reservation_id,
        "source_authentication": "NOT_PROVEN",
    }
    if (
        filesystem != TRUSTED_LOCAL_POSIX
        or not _supported()
        or not detail._safe_root(root)
        or not runs._id(reservation_id)
    ):
        return result
    fds, lock = None, None
    try:
        fds, chain = _open(root, False)
        lock, ident = storage._lock(fds[-1], chain)
        if os.fstat(lock).st_nlink != 1:
            raise ValueError("invalid lock")
        storage._check_lock(fds[-1], chain, lock, ident)
        pending = _pending(fds[-1], chain)
        if pending is not None:
            _pending_payload(pending, reservation_id)
            with _registry(root, fds[-1], chain) as db:
                found = db.execute("SELECT payload FROM reservations WHERE reservation_id=?",
                                   (reservation_id,)).fetchone()
                if found is not None:
                    record, _ = storage._unwrap(found[0])
                    if type(record) is dict and record.get("schema") == _ABORT_SCHEMA:
                        _, digest = _verify_abort(db, fds[-1], chain, reservation_id, found[0], pending)
                        return {**result, "status": "ABORTED_PENDING", "payload_sha256": digest}
            return {**result, "status": "ATTEMPT_UNKNOWN", "reason": "RESERVATION_PENDING"}
        if storage._read(fds[-1], chain, _REGISTRY_MARKER, _LIMIT) is None:
            return result
        with _registry(root, fds[-1], chain) as db:
            found = db.execute(
                "SELECT batch_id,run_id,payload FROM reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            if found is None:
                attempt = storage._read(
                    fds[-1], chain, f"reservation-attempt-{reservation_id}.json", _LIMIT
                )
                return {**result, "status": "ATTEMPT_UNKNOWN" if attempt is not None else "NOT_OBSERVED"}
            payload, digest = storage._unwrap(found[2])
            if type(payload) is dict and payload.get("schema") == _ABORT_SCHEMA:
                _, digest = _verify_abort(db, fds[-1], chain, reservation_id, found[2])
                return {**result, "status": "ABORTED_RELEASE_UNPROVEN", "payload_sha256": digest}
            if (
                payload.get("schema") != _REGISTRY_SCHEMA
                or (
                    payload.get("reservation_id"),
                    payload.get("batch_id"),
                    payload.get("run_id"),
                )
                != (reservation_id, found[0], found[1])
                or type(payload.get("requests")) is not list
                or set(
                    db.execute(
                        "SELECT observation_id FROM observation_ids WHERE reservation_id=? LIMIT 9",
                        (reservation_id,),
                    )
                )
                != {(row["observation_id"],) for row in payload["requests"]}
            ):
                raise ValueError("reservation inconsistent")
            return {
                **result,
                "status": "VISIBLE_UNAUTHENTICATED",
                "reservation": payload,
                "payload_sha256": digest,
            }
    except Exception:
        return result
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        if fds is not None:
            runs._close(fds)


def verify_reservation_readonly(
    *, root, reservation_id, batch_id, run_id, requests, payload_sha256, filesystem
):
    """Check one local reservation without initialization, writes or observation flock.

    The SQLite snapshot and file checks are local consistency checks, not source
    authentication. Noncooperative replacement after the last check is outside
    the trusted-local-filesystem model.
    """
    result = {
        "status": "UNKNOWN",
        "reservation_id": reservation_id,
        "source_authentication": "NOT_PROVEN",
    }
    fds, anchor, db = None, None, None
    try:
        if (
            filesystem != TRUSTED_LOCAL_POSIX
            or not _supported()
            or not detail._safe_root(root)
            or type(payload_sha256) is not str
            or _HASH.fullmatch(payload_sha256) is None
        ):
            raise ValueError("invalid reservation check")
        payload = _reservation_payload(reservation_id, batch_id, run_id, requests)
        encoded = storage._envelope(payload)
        if storage._unwrap(encoded)[1] != payload_sha256:
            raise ValueError("reservation digest mismatch")
        fds, chain = _open(root, False)
        fd = fds[-1]
        marker_identity = _registry_file(fd, _REGISTRY_MARKER, required=True)
        marker = storage._read(fd, chain, _REGISTRY_MARKER, _LIMIT)
        if marker is None or storage._unwrap(marker)[0] != {"schema": _REGISTRY_SCHEMA}:
            raise ValueError("registry marker unavailable")
        _validate_attempt(fd, chain, reservation_id)
        _require_no_pending(fd, chain)
        _registry_sidecars(fd)
        before = _registry_file(fd, _REGISTRY, required=True)
        anchor = os.open(_REGISTRY, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        info = os.fstat(anchor)
        if (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_uid,
            info.st_nlink,
        ) != before or not _directory_matches(chain):
            raise ValueError("registry identity changed")
        stamp = (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        path = Path(root) / "akshare-observations" / _REGISTRY
        db = sqlite3.connect(
            "file:" + quote(str(path), safe="/") + "?mode=ro",
            uri=True,
            timeout=0,
            isolation_level=None,
        )
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 65536)
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        if _registry_file(fd, _REGISTRY, required=True) != before or db.execute(
            "PRAGMA journal_mode"
        ).fetchone() != ("delete",):
            raise ValueError("registry unavailable")
        definitions = dict(
            db.execute("SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
        )
        if definitions != {statement.split()[2]: statement for statement in _REGISTRY_DDL}:
            raise ValueError("registry schema changed")
        if db.execute(
            "SELECT batch_id,run_id,payload FROM reservations WHERE reservation_id=?",
            (reservation_id,),
        ).fetchone() != (batch_id, run_id, encoded):
            raise ValueError("reservation changed")
        mappings = set(
            db.execute(
                "SELECT observation_id FROM observation_ids WHERE reservation_id=? LIMIT 9",
                (reservation_id,),
            )
        )
        if mappings != {(row["observation_id"],) for row in payload["requests"]}:
            raise ValueError("reservation mappings changed")
        _require_no_pending(fd, chain)
        _registry_sidecars(fd)
        _validate_attempt(fd, chain, reservation_id)
        after = os.fstat(anchor)
        if (
            _registry_file(fd, _REGISTRY, required=True) != before
            or (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != stamp
            or _registry_file(fd, _REGISTRY_MARKER, required=True) != marker_identity
            or storage._read(fd, chain, _REGISTRY_MARKER, _LIMIT) != marker
            or not _directory_matches(chain)
        ):
            raise ValueError("registry changed")
        db.execute("ROLLBACK")
        db.close()
        db = None
        _require_no_pending(fd, chain)
        _registry_sidecars(fd)
        _validate_attempt(fd, chain, reservation_id)
        after = os.fstat(anchor)
        if (
            _registry_file(fd, _REGISTRY, required=True) != before
            or (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != stamp
            or _registry_file(fd, _REGISTRY_MARKER, required=True) != marker_identity
            or storage._read(fd, chain, _REGISTRY_MARKER, _LIMIT) != marker
            or not _directory_matches(chain)
        ):
            raise ValueError("registry changed")
        return {**result, "status": "VISIBLE_UNAUTHENTICATED", "payload_sha256": payload_sha256}
    except Exception:
        return result
    finally:
        if db is not None:
            db.close()
        if anchor is not None:
            os.close(anchor)
        if fds is not None:
            runs._close(fds)


def _time_summary(value, capture_id):
    from bt_api_ctp.calendar_time_candidates import TimeTextCandidate

    if (
        type(value) is not TimeTextCandidate
        or value.source_id != capture_id
        or value.verification != "UNVERIFIED"
    ):
        raise ValueError("invalid time candidate")
    ranges = [
        {
            "label": item.label,
            "scope": item.scope,
            "start": item.start,
            "end": item.end,
            "day_transition": item.day_transition,
        }
        for item in value.ranges
    ]
    summary = {
        "status": value.status,
        "ranges": ranges,
        "review_reasons": list(value.review_reasons),
        "uncertain_count": len(value.uncertain_spans),
        "duplicate_count": len(value.duplicate_indexes),
    }
    _validate_time(summary)
    return summary


def _validate_time(value):
    if (
        type(value) is not dict
        or set(value)
        != {"status", "ranges", "review_reasons", "uncertain_count", "duplicate_count"}
        or type(value["status"]) is not str
        or value["status"] not in _TIME_STATUS
        or type(value["ranges"]) is not list
        or len(value["ranges"]) > 512
        or type(value["review_reasons"]) is not list
        or len(value["review_reasons"]) > len(_REASONS)
        or any(type(x) is not str or x not in _REASONS for x in value["review_reasons"])
        or len(set(value["review_reasons"])) != len(value["review_reasons"])
        or any(
            type(value[k]) is not int or not 0 <= value[k] <= 4096
            for k in ("uncertain_count", "duplicate_count")
        )
    ):
        raise ValueError("invalid time summary")
    for row in value["ranges"]:
        if (
            type(row) is not dict
            or set(row) != {"label", "scope", "start", "end", "day_transition"}
            or row["label"] not in {"日盘", "夜盘", "上午", "下午"}
            or row["scope"] != ("NIGHT" if row["label"] == "夜盘" else "DAY")
            or type(row["start"]) is not str
            or not _CLOCK.fullmatch(row["start"])
            or type(row["end"]) is not str
            or not _CLOCK.fullmatch(row["end"])
            or row["day_transition"]
            not in {"SAME_CLOCK_DAY", "EXPLICIT_NEXT_DAY", "INFERRED_NEXT_DAY"}
        ):
            raise ValueError("invalid time range")
    if value["status"] == "SYNTAX_CANDIDATE" and (
        not value["ranges"] or value["review_reasons"] or value["uncertain_count"]
    ):
        raise ValueError("invalid syntax status")


def _validate_fields(fields):
    from bt_api_ctp.calendar_akshare_candidates import _normalize

    if type(fields) is not list or len(fields) > 512:
        raise ValueError("invalid field count")
    rows = []
    for row in fields:
        if type(row) is not dict or set(row) != {"raw_name", "raw_value", "name", "value"}:
            raise ValueError("invalid field")
        for key in ("raw_name", "raw_value", "name", "value"):
            if row[key] is not None and (type(row[key]) is not str or len(row[key]) > 73728):
                raise ValueError("invalid field text")
        if (
            row["name"] != _normalize(row["raw_name"])
            or row["value"] != _normalize(row["raw_value"])
            or any(
                row[key] is not None and len(row[key]) > 4096 for key in ("raw_name", "raw_value")
            )
        ):
            raise ValueError("invalid normalization")
        rows.append((row["raw_name"], row["raw_value"]))
    return rows


def _project(capture, intent, injected):
    from bt_api_ctp.calendar_akshare_candidates import CandidateField, CandidateResult
    from bt_api_ctp.calendar_akshare_fetch import VERSION, AkshareCapture, TimeDiagnostic
    from bt_api_ctp.calendar_time_candidates import parse_time_candidates

    if type(capture) is not AkshareCapture or type(capture.candidate) is not CandidateResult:
        raise ValueError("invalid provider result")
    c = capture.candidate
    if (
        capture.status not in _STATUSES
        or capture.status != c.status
        or c.venue != intent["venue"]
        or c.contract != intent["contract"]
        or c.requested_date.isoformat() != intent["requested_date"]
        or c.source != "akshare.futures_contract_detail"
        or capture.required_version != VERSION
        or capture.observed_version != c.source_version
        or c.verification != "UNVERIFIED"
        or (
            capture.verification,
            capture.authorization,
            capture.coverage,
            capture.descendant_cleanup,
        )
        != ("UNVERIFIED", "NOT_AUTHORIZED", "UNKNOWN", "NOT_PROVEN")
        or type(capture.capture_id) is not str
        or not _CAPTURE.fullmatch(capture.capture_id)
        or type(c.collected_at) is not datetime
        or c.collected_at.utcoffset() is None
        or c.collected_at.utcoffset().total_seconds() != 0
        or type(c.fields) is not tuple
        or len(c.fields) > 512
    ):
        raise ValueError("invalid provider status")
    if injected and c.timeout_enforced:
        raise ValueError("injected timeout not proven")
    if any(type(item) is not CandidateField for item in c.fields):
        raise ValueError("invalid fields")
    if capture.raw_rows is not None and (
        type(capture.raw_rows) is not tuple
        or any(type(row) is not tuple for row in capture.raw_rows)
    ):
        raise ValueError("invalid typed rows")
    fields = [asdict(item) for item in c.fields]
    rows = _validate_fields(fields)
    time_value = capture.time_candidate
    diagnostic = capture.time_diagnostic
    if capture.status == "SUCCESS":
        if time_value is None or diagnostic is not None:
            raise ValueError("invalid time status")
    elif time_value is not None:
        raise ValueError("invalid time status")
    if diagnostic is not None:
        if (
            type(diagnostic) is not TimeDiagnostic
            or diagnostic.status != "UPSTREAM_NOT_SUCCESS"
            or diagnostic.upstream_status != capture.status
        ):
            raise ValueError("invalid diagnostic")
        time_value = diagnostic.candidate
    if bool(c.fields) != (time_value is not None):
        raise ValueError("invalid field projection")
    if (
        time_value is not None
        and parse_time_candidates(source_id=capture.capture_id, fields=rows) != time_value
    ):
        raise ValueError("time parser mismatch")
    content = {
        "fields": fields,
        "raw_rows": [list(row) for row in capture.raw_rows]
        if capture.raw_rows is not None
        else None,
        "time": _time_summary(time_value, capture.capture_id) if time_value else None,
    }
    body = {
        "schema": _RESULT,
        "observation_id": intent["observation_id"],
        "run_id": intent["run_id"],
        "intent_sha256": storage._unwrap(storage._envelope(intent))[1],
        "status": capture.status,
        "transport_status": capture.transport_status,
        "source_id": capture.source_id,
        "capture_id": capture.capture_id,
        "response_digest": capture.response_digest,
        "required_version": capture.required_version,
        "observed_version": capture.observed_version,
        "captured_at": c.collected_at.isoformat(),
        "content": content,
        "content_sha256": hashlib.sha256(storage._json(content)).hexdigest(),
        "data_state": "DATA_RETAINED",
        "classification_check": "REPLAYED"
        if capture.observed_version is not None
        else "NOT_APPLICABLE",
        "automatic_retry": "NOT_PROVEN_SAFE",
        "time_role": "CANDIDATE"
        if capture.time_candidate
        else "DIAGNOSTIC"
        if diagnostic
        else "NONE",
        "timeout_enforced": c.timeout_enforced,
        "direct_child_cleanup": capture.direct_child_cleanup,
        "descendant_cleanup": capture.descendant_cleanup,
        "timeout_retry": capture.timeout_retry,
        "transport_assurance": "INJECTED_UNVERIFIED" if injected else "PROVIDER_SUPERVISED",
        "source_type": "AKSHARE",
        "verification": "UNVERIFIED",
        "source_authentication": "NOT_PROVEN",
        "identity": "UNKNOWN",
        "effective_period": "UNKNOWN",
        "trading_day": "UNKNOWN",
        "coverage": "UNKNOWN",
        "publication": "NOT_AUTHORIZED",
        "association": "NOT_PROVEN",
        "durability": "NOT_PROVEN",
    }
    _validate_result(intent, body)
    if len(storage._envelope(body)) > _LIMIT:
        body.update(
            content=None,
            content_sha256=None,
            data_state="DATA_OMITTED",
            classification_check="NOT_POSSIBLE",
            time_role="OMITTED",
        )
        _validate_result(intent, body)
    return body


def _validate_result(intent, body):
    from bt_api_ctp.calendar_akshare_fetch import VERSION

    keys = {
        "schema",
        "observation_id",
        "run_id",
        "intent_sha256",
        "status",
        "transport_status",
        "source_id",
        "capture_id",
        "response_digest",
        "required_version",
        "observed_version",
        "captured_at",
        "content",
        "content_sha256",
        "data_state",
        "classification_check",
        "automatic_retry",
        "time_role",
        "timeout_enforced",
        "direct_child_cleanup",
        "descendant_cleanup",
        "timeout_retry",
        "transport_assurance",
        "source_type",
        "verification",
        "source_authentication",
        "identity",
        "effective_period",
        "trading_day",
        "coverage",
        "publication",
        "association",
        "durability",
    }
    if type(body) is not dict or set(body) != keys:
        raise ValueError("invalid result")
    if (
        body["schema"] != _RESULT
        or body["observation_id"] != intent["observation_id"]
        or body["run_id"] != intent["run_id"]
        or body["intent_sha256"] != storage._unwrap(storage._envelope(intent))[1]
        or type(body["status"]) is not str
        or body["status"] not in _STATUSES
        or type(body["capture_id"]) is not str
        or not _CAPTURE.fullmatch(body["capture_id"])
        or body["required_version"] != VERSION
        or body["observed_version"] not in {None, VERSION}
        or body["source_type"] != "AKSHARE"
        or body["verification"] != "UNVERIFIED"
        or body["source_authentication"] != "NOT_PROVEN"
        or body["identity"] != "UNKNOWN"
        or body["effective_period"] != "UNKNOWN"
        or body["trading_day"] != "UNKNOWN"
        or body["coverage"] != "UNKNOWN"
        or body["publication"] != "NOT_AUTHORIZED"
        or body["association"] != "NOT_PROVEN"
        or body["durability"] != "NOT_PROVEN"
        or body["descendant_cleanup"] != "NOT_PROVEN"
        or type(body["timeout_enforced"]) is not bool
        or body["transport_assurance"] not in {"INJECTED_UNVERIFIED", "PROVIDER_SUPERVISED"}
        or body["direct_child_cleanup"] not in {"UNKNOWN", "DIRECT_CHILD_REAPED"}
        or body["automatic_retry"] != "NOT_PROVEN_SAFE"
    ):
        raise ValueError("invalid result metadata")
    stamp = datetime.fromisoformat(body["captured_at"])
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("invalid clock")
    status, transport = body["status"], body["transport_status"]
    if transport not in {
        "CHILD_OK",
        "CHILD_TIMEOUT",
        "CHILD_ERROR",
        "CHILD_CLEANUP_UNCERTAIN",
        "SUPERVISOR_UNSUPPORTED",
    }:
        raise ValueError("invalid transport")
    expected = {
        "CHILD_TIMEOUT": {"FETCH_TIMEOUT"},
        "CHILD_ERROR": {"FETCH_ERROR"},
        "CHILD_CLEANUP_UNCERTAIN": {"UNKNOWN"},
        "SUPERVISOR_UNSUPPORTED": {"UNKNOWN"},
        "CHILD_OK": {
            "SUCCESS",
            "EMPTY",
            "MISSING_TRADING_TIME",
            "STRUCTURE_CHANGED",
            "DEPENDENCY_UNAVAILABLE",
            "VERSION_MISMATCH",
            "FETCH_ERROR",
        },
    }
    if status not in expected[transport]:
        raise ValueError("transport mismatch")
    if body["observed_version"] is not None:
        if transport != "CHILD_OK" or status not in {
            "SUCCESS",
            "EMPTY",
            "MISSING_TRADING_TIME",
            "STRUCTURE_CHANGED",
        }:
            raise ValueError("version mismatch")
        if (
            body["source_id"]
            != f"akshare.futures_contract_detail.v1.{intent['venue']}.{intent['contract']}"
            or type(body["response_digest"]) is not str
            or not _HASH.fullmatch(body["response_digest"])
        ):
            raise ValueError("invalid source")
    elif (
        body["source_id"] is not None
        or body["response_digest"] is not None
        or status in {"SUCCESS", "EMPTY", "MISSING_TRADING_TIME"}
    ):
        raise ValueError("unobserved source")
    if body["transport_assurance"] == "INJECTED_UNVERIFIED" and (
        body["timeout_enforced"] or body["direct_child_cleanup"] != "UNKNOWN"
    ):
        raise ValueError("injected assurance")
    if body["timeout_enforced"] and transport not in {"CHILD_OK", "CHILD_TIMEOUT"}:
        raise ValueError("invalid timeout assurance")
    if body["direct_child_cleanup"] == "DIRECT_CHILD_REAPED" and (
        transport != "CHILD_TIMEOUT" or not body["timeout_enforced"]
    ):
        raise ValueError("invalid cleanup")
    if body["timeout_retry"] != (
        "NOT_PROVEN_SAFE" if status in {"FETCH_TIMEOUT", "UNKNOWN"} else "NOT_APPLICABLE"
    ):
        raise ValueError("invalid retry")
    if body["data_state"] == "DATA_OMITTED":
        if (
            body["classification_check"] != "NOT_POSSIBLE"
            or body["content"] is not None
            or body["content_sha256"] is not None
            or body["time_role"] != "OMITTED"
        ):
            raise ValueError("invalid omission")
        return
    if body["data_state"] != "DATA_RETAINED" or body["classification_check"] != (
        "REPLAYED" if body["observed_version"] is not None else "NOT_APPLICABLE"
    ):
        raise ValueError("invalid data state")
    if body["time_role"] not in {"NONE", "CANDIDATE", "DIAGNOSTIC"} or (
        body["time_role"] == "CANDIDATE"
    ) != (status == "SUCCESS"):
        raise ValueError("invalid time role")
    content = body["content"]
    if (
        type(content) is not dict
        or set(content) != {"fields", "raw_rows", "time"}
        or type(content["fields"]) is not list
        or len(content["fields"]) > 512
        or body["content_sha256"] != hashlib.sha256(storage._json(content)).hexdigest()
    ):
        raise ValueError("invalid retained content")
    from bt_api_ctp.calendar_time_candidates import parse_time_candidates

    rows = _validate_fields(content["fields"])
    if rows and body["observed_version"] is None:
        raise ValueError("fields without observed source")
    raw_rows = content["raw_rows"]
    if body["observed_version"] is not None:
        from bt_api_ctp.calendar_akshare_candidates import collect_contract_candidate
        from bt_api_ctp.calendar_akshare_fetch import _Frame, _response_digest, _valid_rows

        if not _valid_rows(raw_rows) or _response_digest(raw_rows) != body["response_digest"]:
            raise ValueError("typed response mismatch")
        replay = collect_contract_candidate(
            venue=intent["venue"],
            contract=intent["contract"],
            requested_date=date.fromisoformat(intent["requested_date"]),
            source_version=body["observed_version"],
            fetch=lambda **_: _Frame(raw_rows),
        )
        if (
            replay.status != status
            or [asdict(field) for field in replay.fields] != content["fields"]
        ):
            raise ValueError("collector mismatch")
    elif raw_rows is not None:
        raise ValueError("unobserved typed rows")
    if (status == "SUCCESS" and not rows) or (status == "EMPTY" and rows):
        raise ValueError("invalid field status")
    if status == "SUCCESS":
        names = [row["name"] for row in content["fields"]]
        if (
            None in names
            or len(names) != len(set(names))
            or "交易时间" not in names
            or not content["fields"][names.index("交易时间")]["value"]
        ):
            raise ValueError("invalid successful fields")
    if bool(rows) != (content["time"] is not None):
        raise ValueError("invalid field time")
    if (content["time"] is None) != (body["time_role"] == "NONE"):
        raise ValueError("invalid time")
    if content["time"] is not None:
        _validate_time(content["time"])
        parsed = parse_time_candidates(source_id=body["capture_id"], fields=rows)
        if _time_summary(parsed, body["capture_id"]) != content["time"]:
            raise ValueError("time mismatch")


def _valid_intent(intent, observation_id, run_id):
    if type(intent) is not dict or set(intent) != {
        "schema",
        "observation_id",
        "run_id",
        "venue",
        "contract",
        "requested_date",
        "timeout",
    }:
        return False
    try:
        requested = date.fromisoformat(intent["requested_date"])
        return (
            intent["schema"] == _INTENT
            and intent["observation_id"] == observation_id
            and intent["run_id"] == run_id
            and requested.isoformat() == intent["requested_date"]
            and _settings(intent["venue"], intent["contract"], requested, intent["timeout"])
        )
    except (ValueError, TypeError, OverflowError):
        return False


def _interrupt(exc, observation_id, run_id, record_state):
    """Annotate, but never consume, process-control exceptions."""
    if not isinstance(exc, Exception):
        with contextlib.suppress(Exception):
            exc.observation_id = observation_id
            exc.run_id = run_id
            exc.record_state = record_state
            exc.observation_status = "UNKNOWN"
            if hasattr(exc, "add_note"):
                exc.add_note("AKSHARE_OBSERVATION_RESULT_UNKNOWN")
        raise exc


class _MockDescriptor:
    """Private descriptor write performed only inside the observation claim lock."""

    def __init__(self, root, observation_id, body, deadline):
        self.root = root
        self.observation_id = observation_id
        self.body = body
        self.deadline = deadline
        self.state = "NOT_ATTEMPTED"
        self.reason = "NONE"
        self.effective_timeout = None

    def install(self):
        if self.deadline is not None and self.deadline <= time.monotonic():
            self.reason = "BUDGET_EXHAUSTED"
            return
        fds, chain = runs._open_runs(self.root, create=False)
        try:
            self.state = "COMMIT_UNKNOWN"
            self.state, self.reason = runs._write_once(
                fds[-1], chain, f"mock-akshare-{self.observation_id}.json", self.body
            )
        finally:
            runs._close(fds)


def _observe_impl(
    *,
    root,
    observation_id,
    run_id,
    venue,
    contract,
    requested_date,
    timeout,
    filesystem,
    reservation=None,
    _mock_descriptor=None,
    _collector=None,
):
    """Install intent before one call; never retry an existing or uncertain ID.

    COMMITTED means only local record installation. Always call show with the
    same IDs to recheck it; no status updates a trusted source-success clock.
    """
    if not _arguments(root, observation_id, run_id, filesystem) or not _settings(
        venue, contract, requested_date, timeout
    ):
        return {"status": "NOT_COMMITTED", "reason": "INVALID_ARGUMENTS"}
    try:
        from bt_api_ctp.calendar_akshare_fetch import collect_akshare_contract

        if (_collector is not None and not callable(_collector)) or (
            _mock_descriptor is not None and (
                type(_mock_descriptor) is not _MockDescriptor
                or _mock_descriptor.root != root
                or _mock_descriptor.observation_id != observation_id
            )
        ):
            raise ValueError
        collector = collect_akshare_contract if _collector is None else _collector
        fds, chain = _open(root, True)
    except BaseException as exc:
        _interrupt(exc, observation_id, run_id, "NOT_COMMITTED")
        return {"status": "NOT_COMMITTED", "reason": "UNAVAILABLE"}
    intent = {
        "schema": _INTENT,
        "observation_id": observation_id,
        "run_id": run_id,
        "venue": venue,
        "contract": contract,
        "requested_date": requested_date.isoformat(),
        "timeout": timeout,
    }
    lock = None
    attempted = False
    registry_check = True
    try:
        lock, ident = storage._lock(fds[-1], chain)
        if os.fstat(lock).st_nlink != 1:
            raise ValueError("invalid lock")
        storage._check_lock(fds[-1], chain, lock, ident)
        _require_no_pending(fds[-1], chain)
        with _registry(root, fds[-1], chain) as db:
            _check_reservation(
                db,
                observation_id,
                run_id,
                venue,
                contract,
                requested_date,
                timeout,
                reservation,
            )
        storage._check_lock(fds[-1], chain, lock, ident)
        registry_check = False
        # Reject existing claims before a descriptor can consume this ID.
        if (storage._read(fds[-1], chain, f"result-{observation_id}.json", _LIMIT) is not None
                or storage._read(fds[-1], chain, f"intent-{observation_id}.json", _LIMIT) is not None):
            return {"status": "NOT_COMMITTED", "reason": "ID_ALREADY_USED"}
        if _mock_descriptor is not None:
            _mock_descriptor.install()
            if _mock_descriptor.state != "COMMITTED":
                return {"status": "UNKNOWN" if _mock_descriptor.state == "COMMIT_UNKNOWN" else "NOT_COMMITTED",
                        "reason": _mock_descriptor.reason, "record_state": "NOT_ATTEMPTED",
                        "descriptor_state": _mock_descriptor.state}
            if _mock_descriptor.deadline is not None:
                remaining = _mock_descriptor.deadline - time.monotonic()
                if remaining <= 0:
                    return {"status": "NOT_COMMITTED", "reason": "BUDGET_EXHAUSTED",
                            "record_state": "NOT_ATTEMPTED", "descriptor_state": "COMMITTED"}
                timeout = min(timeout, remaining)
                intent["timeout"] = timeout
            _mock_descriptor.effective_timeout = timeout
            storage._check_lock(fds[-1], chain, lock, ident)
        attempted = True
        claim = storage._write(
            fds[-1],
            chain,
            f"intent-{observation_id}.json",
            storage._envelope(intent),
            _LIMIT,
        )
        if claim != "COMMITTED":
            return {
                "status": claim,
                "reason": "CLAIM_NOT_PROVEN",
                "observation_id": observation_id,
                "run_id": run_id,
            }
    except BaseException as exc:
        descriptor_uncertain = (
            _mock_descriptor is not None
            and _mock_descriptor.state in {"COMMITTED", "COMMIT_UNKNOWN"}
        )
        _interrupt(
            exc,
            observation_id,
            run_id,
            "COMMIT_UNKNOWN" if attempted or descriptor_uncertain else "NOT_COMMITTED",
        )
        return {
            "status": (
                "COMMIT_UNKNOWN" if attempted else "UNKNOWN" if registry_check or descriptor_uncertain else "NOT_COMMITTED"
            ),
            "reason": "CLAIM_UNCERTAIN",
            "observation_id": observation_id,
            "run_id": run_id,
            **({"descriptor_state": _mock_descriptor.state} if _mock_descriptor is not None else {}),
        }
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)
    try:
        capture = collector(
            venue=venue,
            contract=contract,
            requested_date=requested_date,
            timeout=timeout,
        )
        body = _project(capture, intent, _collector is not None)
    except BaseException as exc:
        _interrupt(exc, observation_id, run_id, "ATTEMPT_UNKNOWN")
        return {
            "status": "ATTEMPT_UNKNOWN",
            "reason": "COLLECTOR_OR_RESULT_UNCERTAIN",
            "observation_id": observation_id,
            "run_id": run_id,
        }
    try:
        fds, chain = _open(root, False)
    except BaseException as exc:
        _interrupt(exc, observation_id, run_id, "ATTEMPT_UNKNOWN")
        return {
            "status": "ATTEMPT_UNKNOWN",
            "reason": "DIRECTORY_UNAVAILABLE",
            "observation_id": observation_id,
            "run_id": run_id,
        }
    lock = None
    attempted = False
    try:
        lock, ident = storage._lock(fds[-1], chain)
        storage._check_lock(fds[-1], chain, lock, ident)
        raw = storage._read(fds[-1], chain, f"intent-{observation_id}.json", _LIMIT)
        if raw is None or storage._unwrap(raw)[0] != intent:
            raise ValueError("claim changed")
        attempted = True
        result = storage._write(
            fds[-1],
            chain,
            f"result-{observation_id}.json",
            storage._envelope(body),
            _LIMIT,
        )
        return {
            "status": "ATTEMPT_UNKNOWN" if result == "NOT_COMMITTED" else result,
            "record_state": "ATTEMPT_UNKNOWN" if result == "NOT_COMMITTED" else result,
            "observation_status": body["status"],
            "data_state": body["data_state"],
            "requires_readback": True,
            "observation_id": observation_id,
            "run_id": run_id,
        }
    except BaseException as exc:
        _interrupt(
            exc,
            observation_id,
            run_id,
            "COMMIT_UNKNOWN" if attempted else "ATTEMPT_UNKNOWN",
        )
        return {
            "status": "COMMIT_UNKNOWN" if attempted else "ATTEMPT_UNKNOWN",
            "record_state": "COMMIT_UNKNOWN" if attempted else "ATTEMPT_UNKNOWN",
            "observation_status": body["status"],
            "data_state": body["data_state"],
            "requires_readback": True,
            "reason": "RESULT_UNCERTAIN",
            "observation_id": observation_id,
            "run_id": run_id,
        }
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)


def observe(
    *,
    root,
    observation_id,
    run_id,
    venue,
    contract,
    requested_date,
    timeout,
    filesystem,
    reservation=None,
    _mock_descriptor=None,
    _collector=None,
):
    """Make one claimed attempt; inspect the same IDs with show before use.

    Record installation does not establish a successful or fresh source check.
    """
    result = _observe_impl(
        root=root,
        observation_id=observation_id,
        run_id=run_id,
        venue=venue,
        contract=contract,
        requested_date=requested_date,
        timeout=timeout,
        filesystem=filesystem,
        reservation=reservation,
        _mock_descriptor=_mock_descriptor,
        _collector=_collector,
    )
    result.setdefault("record_state", result["status"])
    result.setdefault("observation_status", "UNKNOWN")
    result.setdefault("requires_readback", True)
    if result.get("data_state") == "DATA_OMITTED":
        result["reported_observation_status"] = result["observation_status"]
        result["observation_status"] = "UNKNOWN"
        result["capture_state"] = "CAPTURE_TOO_LARGE"
        result["classification_check"] = "NOT_POSSIBLE"
        result["automatic_retry"] = "NOT_PROVEN_SAFE"
    return result


def show(*, root, observation_id, run_id, filesystem, include_content=False):
    """Inspect original IDs; opt-in content is private internal data."""
    unknown = {"status": "UNKNOWN", "reason": "UNAVAILABLE_OR_CHANGED"}
    if (
        not _arguments(root, observation_id, run_id, filesystem)
        or type(include_content) is not bool
    ):
        return unknown
    try:
        fds, chain = _open(root, False)
        try:
            raw_intent = storage._read(fds[-1], chain, f"intent-{observation_id}.json", _LIMIT)
            raw_result = storage._read(fds[-1], chain, f"result-{observation_id}.json", _LIMIT)
            if raw_intent is None:
                return {"status": "NOT_OBSERVED"} if raw_result is None else unknown
            intent, _ = storage._unwrap(raw_intent)
            if not _valid_intent(intent, observation_id, run_id):
                return unknown
            if raw_result is None:
                return {
                    "status": "ATTEMPT_UNKNOWN",
                    "observation_id": observation_id,
                    "run_id": run_id,
                    "requested_venue": intent["venue"],
                    "requested_contract": intent["contract"],
                    "requested_date": intent["requested_date"],
                    "request_scope": "DECLARED_NOT_RESPONSE_IDENTITY_OR_TRADING_DAY",
                }
            body, digest = storage._unwrap(raw_result)
            _validate_result(intent, body)
            result = {
                key: body[key]
                for key in (
                    "observation_id",
                    "run_id",
                    "status",
                    "transport_status",
                    "source_id",
                    "capture_id",
                    "response_digest",
                    "required_version",
                    "observed_version",
                    "captured_at",
                    "time_role",
                    "data_state",
                    "classification_check",
                    "automatic_retry",
                    "timeout_enforced",
                    "direct_child_cleanup",
                    "descendant_cleanup",
                    "timeout_retry",
                    "transport_assurance",
                    "source_type",
                    "verification",
                    "source_authentication",
                    "identity",
                    "effective_period",
                    "trading_day",
                    "coverage",
                    "publication",
                    "association",
                    "durability",
                )
            }
            result["observation_status"] = result.pop("status")
            if body["data_state"] == "DATA_OMITTED":
                result["reported_observation_status"] = result["observation_status"]
                result["observation_status"] = "UNKNOWN"
                result["capture_state"] = "CAPTURE_TOO_LARGE"
            result["status"] = "VISIBLE_UNAUTHENTICATED"
            result["record_state"] = "VISIBLE_UNAUTHENTICATED"
            result["requested_venue"] = intent["venue"]
            result["requested_contract"] = intent["contract"]
            result["requested_date"] = intent["requested_date"]
            result["request_scope"] = "DECLARED_NOT_RESPONSE_IDENTITY_OR_TRADING_DAY"
            result["payload_sha256"] = digest
            result["content_sha256"] = body["content_sha256"]
            if include_content:
                result["content"] = body["content"]
            return result
        finally:
            runs._close(fds)
    except Exception:
        return unknown
