"""One SIMULATION_ONLY daily batch invocation; no resident or OS scheduler.

Local records, IDs and clocks are unauthenticated. This module requires a trusted,
cooperating local POSIX filesystem; hostile replacement, whole-root rollback and
power-loss durability are not established. It never retries an uncertain batch.

Read-only actions reuse the same evidence: ``inspect-active`` finds one pending
review, ``scan-publications`` enumerates effective terminal committed
publications, and ``check-health`` compares the two most recent store trading
days with successful update-check records. None of them authorizes trading.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

_SUPPORTED_PLATFORM = False
if os.name == "posix":
    try:
        import fcntl as _fcntl  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name != "fcntl":
            raise
    else:
        _SUPPORTED_PLATFORM = True

if _SUPPORTED_PLATFORM:
    from bt_api_py import trading_calendar_daily_batch as batch
    from bt_api_py import trading_calendar_detail_collection as storage
    from bt_api_py import trading_calendar_mixed_publication as mixed
    from bt_api_py import trading_calendar_mock_akshare as akshare
    from bt_api_py import trading_calendar_mock_cli as mock_cli
    from bt_api_py import trading_calendar_observation_run as runs
    from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication, _hash
    from bt_api_py.trading_calendar_preview_reports import _directory_matches, _open_directory
    from bt_api_py.trading_calendar_store import (
        CurrentPrecondition,
        TradingCalendarStore,
        _decode,
        _identifier,
        _policy_value,
        _scope_key,
    )

    FS = runs.TRUSTED_LOCAL_POSIX
else:
    FS = "TRUSTED_LOCAL_POSIX"
LABEL = {
    "environment": "SIMULATION_ONLY",
    "source_authentication": "NOT_PROVEN",
    "clock": "LOCAL_CLOCK_UNVERIFIED",
}
_CONFIG = "bt_api.calendar-schedule-once-config/1"
_PENDING = "bt_api.calendar-schedule-once-pending/1"
_RETURNED = "bt_api.calendar-schedule-once-returned/1"
_TERMINAL = "bt_api.calendar-schedule-once-terminal/1"
_INDEX = "bt_api.calendar-schedule-once-date-index/1"
_PENDING_FILE = "schedule-once-pending.json"
_KEY = re.compile(r"[A-Za-z0-9_.-]{1,80}\Z", re.ASCII)
_CONFIG_LIMIT = 65536
_LEDGER_FILE = "schedule-once-ledger.sqlite3"
_LEDGER_MARKER = "schedule-once-ledger-enabled.json"
_INIT_FENCE = "schedule-once-initialization.json"
_LEDGER_SCHEMA_V1 = "bt_api.calendar-schedule-once-ledger/1"
_LEDGER_SCHEMA = "bt_api.calendar-schedule-once-ledger/2"
_LEDGER_DDL_V1 = (
    "CREATE TABLE ledger_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), registry_id TEXT NOT NULL, active_id TEXT, last_id TEXT)",
    "CREATE TABLE operations (operation_id TEXT PRIMARY KEY, job_key TEXT NOT NULL, requested_date TEXT NOT NULL, pending BLOB NOT NULL, pending_sha256 TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('ACTIVE','RETURNED','TERMINAL')), returned_sha256 TEXT, terminal_sha256 TEXT, UNIQUE(job_key,requested_date))",
    "CREATE UNIQUE INDEX operations_one_active ON operations ((1)) WHERE state IN ('ACTIVE','RETURNED')",
    "CREATE TRIGGER operations_no_delete BEFORE DELETE ON operations BEGIN SELECT RAISE(ABORT,'immutable operation'); END",
    "CREATE TRIGGER operations_update_guard BEFORE UPDATE ON operations BEGIN SELECT CASE WHEN NEW.operation_id != OLD.operation_id OR NEW.job_key != OLD.job_key OR NEW.requested_date != OLD.requested_date OR NEW.pending != OLD.pending OR NEW.pending_sha256 != OLD.pending_sha256 OR NOT ((OLD.state='ACTIVE' AND NEW.state='RETURNED' AND OLD.returned_sha256 IS NULL AND NEW.returned_sha256 IS NOT NULL AND NEW.terminal_sha256 IS NULL) OR (OLD.state='RETURNED' AND NEW.state='TERMINAL' AND NEW.returned_sha256 IS OLD.returned_sha256 AND NEW.terminal_sha256 IS NOT NULL)) THEN RAISE(ABORT,'invalid operation transition') END; END",
    "CREATE TRIGGER ledger_state_no_delete BEFORE DELETE ON ledger_state BEGIN SELECT RAISE(ABORT,'immutable ledger state'); END",
    "CREATE TRIGGER ledger_state_update_guard BEFORE UPDATE ON ledger_state BEGIN SELECT CASE WHEN NEW.singleton != OLD.singleton OR NEW.registry_id != OLD.registry_id OR NOT ((OLD.active_id IS NULL AND NEW.active_id IS NOT NULL AND NEW.last_id IS OLD.last_id AND (SELECT state FROM operations WHERE operation_id=NEW.active_id)='ACTIVE') OR (OLD.active_id IS NOT NULL AND NEW.active_id IS NULL AND NEW.last_id IS OLD.active_id AND (SELECT state FROM operations WHERE operation_id=OLD.active_id)='TERMINAL')) THEN RAISE(ABORT,'invalid active transition') END; END",
)
_LEDGER_DDL = (
    "CREATE TABLE ledger_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), registry_id TEXT NOT NULL, active_id TEXT, last_id TEXT)",
    "CREATE TABLE operations (operation_id TEXT PRIMARY KEY, job_key TEXT NOT NULL, requested_date TEXT NOT NULL, pending BLOB NOT NULL, pending_sha256 TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('ACTIVE','RETURNED','TERMINAL')), returned_sha256 TEXT, terminal_sha256 TEXT, effective INTEGER NOT NULL DEFAULT 1 CHECK(effective IN (0,1)))",
    "CREATE UNIQUE INDEX operations_one_active ON operations ((1)) WHERE state IN ('ACTIVE','RETURNED')",
    "CREATE UNIQUE INDEX operations_one_effective_date ON operations (job_key,requested_date) WHERE effective=1",
    "CREATE TRIGGER operations_no_delete BEFORE DELETE ON operations BEGIN SELECT RAISE(ABORT,'immutable operation'); END",
    "CREATE TRIGGER operations_update_guard BEFORE UPDATE ON operations BEGIN SELECT CASE WHEN NEW.operation_id != OLD.operation_id OR NEW.job_key != OLD.job_key OR NEW.requested_date != OLD.requested_date OR NEW.pending != OLD.pending OR NEW.pending_sha256 != OLD.pending_sha256 OR NOT ((OLD.state='ACTIVE' AND NEW.state='RETURNED' AND OLD.returned_sha256 IS NULL AND NEW.returned_sha256 IS NOT NULL AND NEW.terminal_sha256 IS NULL AND NEW.effective=OLD.effective) OR (OLD.state='RETURNED' AND NEW.state='TERMINAL' AND NEW.returned_sha256 IS OLD.returned_sha256 AND NEW.terminal_sha256 IS NOT NULL AND (NEW.effective=OLD.effective OR (OLD.effective=1 AND NEW.effective=0)))) THEN RAISE(ABORT,'invalid operation transition') END; END",
    "CREATE TRIGGER ledger_state_no_delete BEFORE DELETE ON ledger_state BEGIN SELECT RAISE(ABORT,'immutable ledger state'); END",
    "CREATE TRIGGER ledger_state_update_guard BEFORE UPDATE ON ledger_state BEGIN SELECT CASE WHEN NEW.singleton != OLD.singleton OR NEW.registry_id != OLD.registry_id OR NOT ((OLD.active_id IS NULL AND NEW.active_id IS NOT NULL AND NEW.last_id IS OLD.last_id AND (SELECT state FROM operations WHERE operation_id=NEW.active_id)='ACTIVE') OR (OLD.active_id IS NOT NULL AND NEW.active_id IS NULL AND NEW.last_id IS OLD.active_id AND (SELECT state FROM operations WHERE operation_id=OLD.active_id)='TERMINAL')) THEN RAISE(ABORT,'invalid active transition') END; END",
)
_LEDGER_SCHEMAS = {_LEDGER_SCHEMA_V1: _LEDGER_DDL_V1, _LEDGER_SCHEMA: _LEDGER_DDL}
_DISPOSITION = "bt_api.calendar-schedule-once-disposition/1"
_STALE_OUTCOME = "STALE_REVIEW_CLOSED"
_HEALTH_VENUES = frozenset({"SHFE", "INE", "CZCE", "DCE", "CFFEX", "GFEX"})
_HEALTH_THRESHOLD = 2
_HEALTH_MAX_TRADING_DAYS = 40000
_HEALTH_SUCCESS_OUTCOMES = frozenset({"COMMITTED", "SAME"})
_PUBLICATION_SCAN_LIMIT = 64


def _now_utc():
    return datetime.now(UTC)


def _digest(value):
    return hashlib.sha256(storage._json(value)).hexdigest()


def _read_config(path):
    if type(path) is not str or not os.path.isabs(path) or ".." in path.split(os.sep):
        raise ValueError("invalid config path")
    parent, name = os.path.split(path)
    if not name:
        raise ValueError("invalid config path")
    fds, chain = _open_directory(parent)
    fd = None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fds[-1])
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_mode & 0o022
            or before.st_size > _CONFIG_LIMIT
        ):
            raise ValueError("invalid config file")
        data = bytearray()
        while len(data) <= _CONFIG_LIMIT:
            part = os.read(fd, _CONFIG_LIMIT + 1 - len(data))
            if not part:
                break
            data.extend(part)
        after = os.fstat(fd)
        current = os.stat(name, dir_fd=fds[-1], follow_symlinks=False)

        def identity(info):
            return (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_uid,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )

        if (
            len(data) != before.st_size
            or len(data) > _CONFIG_LIMIT
            or identity(before) != identity(after)
            or identity(before) != identity(current)
            or not _directory_matches(chain)
        ):
            raise ValueError("config changed")
        value = json.loads(bytes(data).decode("utf-8"), object_pairs_hook=runs._unique_pairs)
        _validate_config(value)
        return value, hashlib.sha256(data).hexdigest()
    finally:
        if fd is not None:
            os.close(fd)
        runs._close(fds)


def _validate_config(config):
    fields = {
        "schema",
        "environment",
        "root",
        "job_key",
        "original_run_id",
        "scopes",
        "budget_seconds",
    }
    if (
        type(config) is not dict
        or set(config) not in (fields, fields | {"supplement"})
        or config["schema"] != _CONFIG
        or config["environment"] != "SIMULATION_ONLY"
        or type(config["root"]) is not str
        or not os.path.isabs(config["root"])
        or ".." in config["root"].split(os.sep)
        or (
            "supplement" in config
            and (
                type(config["supplement"]) is not str
                or not os.path.isabs(config["supplement"])
                or ".." in config["supplement"].split(os.sep)
            )
        )
        or type(config["job_key"]) is not str
        or _KEY.fullmatch(config["job_key"]) is None
        or not runs._id(config["original_run_id"])
        or type(config["scopes"]) is not list
        or not 2 <= len(config["scopes"]) <= 8
        or type(config["budget_seconds"]) not in (int, float)
        or not math.isfinite(config["budget_seconds"])
        or not 0 < config["budget_seconds"] <= 600
    ):
        raise ValueError("invalid schedule config")


def _read(fd, chain, name):
    raw = runs._read_entry(fd, chain, name)
    return None if raw is None else storage._unwrap(raw)


def _write(fd, chain, name, value):
    encoded = storage._envelope(value)
    state, reason = runs._write_once(fd, chain, name, encoded)
    if state != "COMMITTED":
        raise ValueError("write outcome " + state + ":" + reason)
    checked = _read(fd, chain, name)
    if checked is None or checked[0] != value:
        raise ValueError("write readback unavailable")
    return checked[1]


def _replace(fd, chain, name, value):
    """Atomically replace one directory entry; used only for ledger marker migration."""
    encoded = storage._envelope(value)
    if not _directory_matches(chain):
        raise ValueError("schedule directory changed")
    temp = f".schedule-replace-{uuid.uuid4().hex}.tmp"
    tfd = os.open(
        temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
    )
    try:
        os.fchmod(tfd, 0o600)
        offset = 0
        while offset < len(encoded):
            count = os.write(tfd, encoded[offset:])
            if count <= 0:
                raise OSError("short write")
            offset += count
        os.fsync(tfd)
        if not _directory_matches(chain):
            raise ValueError("schedule directory changed")
        os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(tfd)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp, dir_fd=fd)
    checked = _read(fd, chain, name)
    if checked is None or checked[0] != value:
        raise ValueError("replacement readback unavailable")
    return checked[1]


def _sqlite_definitions(db):
    return dict(db.execute("SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))


def _expected_definitions(ddl):
    return {
        re.match(r"CREATE (?:UNIQUE )?(?:TABLE|INDEX|TRIGGER) ([^ (]+)", statement).group(
            1
        ): statement
        for statement in ddl
    }


def _migrate_ledger(fd, chain, db, marker_body):
    """Upgrade an enabled /1 ledger to /2 in one transaction, then rewrite the marker.

    The migration is idempotent: a crash after the DDL commit but before the marker
    rewrite is completed on the next writable open. Read-only callers never migrate.
    """
    definitions = _sqlite_definitions(db)
    if definitions == _expected_definitions(_LEDGER_DDL):
        pass
    elif definitions != _expected_definitions(_LEDGER_DDL_V1):
        raise ValueError("schedule ledger schema changed")
    else:
        db.execute("BEGIN IMMEDIATE")
        # Renaming operations rewrites any surviving trigger text, so every trigger is rebuilt.
        for name in (
            "operations_no_delete",
            "operations_update_guard",
            "ledger_state_no_delete",
            "ledger_state_update_guard",
        ):
            db.execute("DROP TRIGGER " + name)
        db.execute("DROP INDEX operations_one_active")
        db.execute("ALTER TABLE operations RENAME TO operations_migrating")
        for statement in _LEDGER_DDL[1:6]:
            db.execute(statement)
        db.execute(
            "INSERT INTO operations (operation_id,job_key,requested_date,pending,pending_sha256,"
            "state,returned_sha256,terminal_sha256,effective) "
            "SELECT operation_id,job_key,requested_date,pending,pending_sha256,state,"
            "returned_sha256,terminal_sha256,1 FROM operations_migrating"
        )
        db.execute("DROP TABLE operations_migrating")
        for statement in _LEDGER_DDL[6:]:
            db.execute(statement)
        db.execute("COMMIT")
        if _sqlite_definitions(db) != _expected_definitions(_LEDGER_DDL):
            raise ValueError("schedule ledger migration changed")
    body = {**marker_body, "schema": _LEDGER_SCHEMA}
    return body, (body, _replace(fd, chain, _LEDGER_MARKER, body))


def _read_exact(fd, chain, name, value):
    found = _read(fd, chain, name)
    if found is None or found[0] != value:
        raise ValueError("record unavailable or changed")
    return found[1]


def _ledger_file(fd, name, *, required=False):
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        if required:
            raise ValueError("schedule ledger file missing") from None
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise ValueError("schedule ledger file unsafe")
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink)


def _ledger_sidecars(fd):
    for suffix in ("-journal", "-wal", "-shm"):
        if _ledger_file(fd, _LEDGER_FILE + suffix) is not None:
            raise ValueError("schedule ledger recovery required")


def _scan_legacy_once(fd, chain, *, allow_fence=False):
    """Exhaust the existing directory only when the ledger has never been enabled."""
    before = os.fstat(fd)
    if not _directory_matches(chain):
        raise ValueError("schedule directory changed")
    with os.scandir(fd) as entries:
        for entry in entries:
            if entry.name.startswith("schedule-once-") and not (
                allow_fence and entry.name == _INIT_FENCE
            ):
                raise ValueError("legacy schedule migration required")
    after = os.fstat(fd)
    if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or not _directory_matches(chain):
        raise ValueError("schedule directory changed")


@contextlib.contextmanager
def _ledger(root, fd, chain, *, initialize=False, readonly=False, initial_fence=None):
    if initialize and readonly:
        raise ValueError("read-only ledger cannot initialize")
    fence = _read(fd, chain, _INIT_FENCE)
    if fence is not None and (initial_fence is None or fence[0] != initial_fence):
        raise ValueError("schedule initialization requires original operation recovery")
    if initial_fence is not None and (fence is None or fence[0] != initial_fence):
        raise ValueError("schedule initialization fence unavailable")
    marker = _read(fd, chain, _LEDGER_MARKER)
    before = _ledger_file(fd, _LEDGER_FILE)
    if marker is None and before is None and initialize:
        _ledger_sidecars(fd)
        _scan_legacy_once(fd, chain, allow_fence=initial_fence is not None)
        registry_id = uuid.uuid4().hex
        if not runs._id(registry_id):
            raise ValueError("registry ID invalid")
        dbfd = os.open(
            _LEDGER_FILE, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        try:
            os.fchmod(dbfd, 0o600)
            os.fsync(dbfd)
        finally:
            os.close(dbfd)
        os.fsync(fd)
        identity = _ledger_file(fd, _LEDGER_FILE, required=True)
        uri = (
            "file:"
            + quote(str(Path(root) / "observation-runs" / _LEDGER_FILE), safe="/")
            + "?mode=rw"
        )
        db = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None)
        try:
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                raise ValueError("schedule ledger journal mode")
            db.execute("BEGIN IMMEDIATE")
            for ddl in _LEDGER_DDL:
                db.execute(ddl)
            db.execute("INSERT INTO ledger_state VALUES(1,?,?,?)", (registry_id, None, None))
            db.execute("COMMIT")
        finally:
            db.close()
        _ledger_sidecars(fd)
        if _ledger_file(fd, _LEDGER_FILE, required=True) != identity:
            raise ValueError("schedule ledger changed")
        dbfd = os.open(_LEDGER_FILE, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            os.fsync(dbfd)
        finally:
            os.close(dbfd)
        os.fsync(fd)
        marker_body = {
            "schema": _LEDGER_SCHEMA,
            "registry_id": registry_id,
            "database_dev": identity[0],
            "database_ino": identity[1],
        }
        _write(fd, chain, _LEDGER_MARKER, marker_body)
        marker = _read(fd, chain, _LEDGER_MARKER)
        before = _ledger_file(fd, _LEDGER_FILE, required=True)
    if marker is None or before is None:
        raise ValueError("schedule ledger initialization incomplete")
    _ledger_file(fd, _LEDGER_MARKER, required=True)
    marker_body = marker[0]
    if (
        type(marker_body) is not dict
        or set(marker_body) != {"schema", "registry_id", "database_dev", "database_ino"}
        or marker_body["schema"] not in _LEDGER_SCHEMAS
        or not runs._id(marker_body["registry_id"])
        or (marker_body["database_dev"], marker_body["database_ino"]) != before[:2]
    ):
        raise ValueError("schedule ledger identity mismatch")
    _ledger_sidecars(fd)
    if not _directory_matches(chain):
        raise ValueError("schedule directory changed")
    anchor = os.open(_LEDGER_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    db = None
    try:
        info = os.fstat(anchor)
        if (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink) != before:
            raise ValueError("schedule ledger anchor changed")
        uri = (
            "file:"
            + quote(str(Path(root) / "observation-runs" / _LEDGER_FILE), safe="/")
            + ("?mode=ro" if readonly else "?mode=rw")
        )
        db = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None)
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 262144)
        db.execute("PRAGMA query_only=ON" if readonly else "PRAGMA synchronous=FULL")
        if _ledger_file(fd, _LEDGER_FILE, required=True) != before or db.execute(
            "PRAGMA journal_mode"
        ).fetchone() != ("delete",):
            raise ValueError("schedule ledger changed")
        if marker_body["schema"] != _LEDGER_SCHEMA:
            if readonly:
                raise ValueError("schedule ledger migration requires writable open")
            marker_body, marker = _migrate_ledger(fd, chain, db, marker_body)
            if (
                _ledger_file(fd, _LEDGER_FILE, required=True) != before
                or _read(fd, chain, _LEDGER_MARKER) != marker
                or db.execute("PRAGMA journal_mode").fetchone() != ("delete",)
            ):
                raise ValueError("schedule ledger migration changed")
        if _sqlite_definitions(db) != _expected_definitions(
            _LEDGER_SCHEMAS[marker_body["schema"]]
        ):
            raise ValueError("schedule ledger schema changed")
        state = db.execute(
            "SELECT registry_id,active_id,last_id FROM ledger_state WHERE singleton=1"
        ).fetchone()
        if (
            state is None
            or state[0] != marker_body["registry_id"]
            or any(value is not None and not runs._id(value) for value in state[1:])
        ):
            raise ValueError("schedule ledger state invalid")
        if (
            initial_fence is None
            and db.execute("SELECT 1 FROM operations LIMIT 1").fetchone() is None
        ):
            raise ValueError("empty enabled ledger requires original initialization recovery")
        yield db
        if (
            db.in_transaction
            or _ledger_file(fd, _LEDGER_FILE, required=True) != before
            or _read(fd, chain, _LEDGER_MARKER) != marker
            or not _directory_matches(chain)
        ):
            raise ValueError("schedule ledger changed")
        _ledger_sidecars(fd)
    finally:
        if db is not None:
            db.close()
        os.close(anchor)


def _ledger_row(db, operation_id, root):
    row = db.execute(
        "SELECT job_key,requested_date,pending,pending_sha256,state,returned_sha256,"
        "terminal_sha256,effective FROM operations WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if row is None:
        raise ValueError("original ledger operation unavailable")
    pending, digest = storage._unwrap(row[2])
    _validate_pending(pending, root)
    if (
        pending["operation_id"] != operation_id
        or pending["job_key"] != row[0]
        or pending["requested_date"] != row[1]
        or digest != row[3]
        or row[4] not in {"ACTIVE", "RETURNED", "TERMINAL"}
        or type(row[7]) is not int
        or row[7] not in (0, 1)
        or (row[4] == "ACTIVE" and (row[5] is not None or row[6] is not None or row[7] != 1))
        or (row[4] == "RETURNED" and (row[5] is None or row[6] is not None or row[7] != 1))
        or (row[4] == "TERMINAL" and (row[5] is None or row[6] is None))
        or any(
            value is not None
            and (type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None)
            for value in row[5:7]
        )
    ):
        raise ValueError("original ledger operation changed")
    return pending, digest, row[4], row[5], row[6], row[7]


def _ledger_state(db, root):
    state = db.execute("SELECT active_id,last_id FROM ledger_state WHERE singleton=1").fetchone()
    if state is None:
        raise ValueError("schedule ledger state unavailable")
    opened = db.execute(
        "SELECT operation_id FROM operations WHERE state IN ('ACTIVE','RETURNED') LIMIT 1"
    ).fetchone()
    if (opened[0] if opened else None) != state[0]:
        raise ValueError("schedule active identity mismatch")
    if state[0] is not None and _ledger_row(db, state[0], root)[2] not in {"ACTIVE", "RETURNED"}:
        raise ValueError("schedule active state changed")
    if state[1] is not None and _ledger_row(db, state[1], root)[2] != "TERMINAL":
        raise ValueError("schedule last state changed")
    return state


def _ledger_insert(db, pending, pending_sha):
    encoded = storage._envelope(pending)
    if len(encoded) > runs._LIMIT or storage._unwrap(encoded)[1] != pending_sha:
        raise ValueError("schedule pending exceeds budget")
    db.execute("BEGIN IMMEDIATE")
    db.execute(
        "INSERT INTO operations (operation_id,job_key,requested_date,pending,pending_sha256,"
        "state,returned_sha256,terminal_sha256,effective) VALUES(?,?,?,?,?,'ACTIVE',NULL,NULL,1)",
        (
            pending["operation_id"],
            pending["job_key"],
            pending["requested_date"],
            encoded,
            pending_sha,
        ),
    )
    if (
        db.execute(
            "UPDATE ledger_state SET active_id=? WHERE singleton=1 AND active_id IS NULL",
            (pending["operation_id"],),
        ).rowcount
        != 1
    ):
        raise ValueError("schedule active claim changed")
    db.execute("COMMIT")


def _ledger_returned(db, operation_id, returned_sha):
    db.execute("BEGIN IMMEDIATE")
    if (
        db.execute(
            "UPDATE operations SET state='RETURNED',returned_sha256=? WHERE operation_id=? AND state='ACTIVE'",
            (returned_sha, operation_id),
        ).rowcount
        != 1
    ):
        raise ValueError("schedule returned transition changed")
    db.execute("COMMIT")


def _ledger_terminal(db, operation_id, terminal_sha, *, effective=True):
    db.execute("BEGIN IMMEDIATE")
    if (
        db.execute(
            "UPDATE operations SET state='TERMINAL',terminal_sha256=?,effective=? "
            "WHERE operation_id=? AND state='RETURNED' AND effective=1",
            (terminal_sha, 1 if effective else 0, operation_id),
        ).rowcount
        != 1
    ):
        raise ValueError("schedule terminal transition changed")
    if (
        db.execute(
            "UPDATE ledger_state SET active_id=NULL,last_id=? WHERE singleton=1 AND active_id=?",
            (operation_id, operation_id),
        ).rowcount
        != 1
    ):
        raise ValueError("schedule active release changed")
    db.execute("COMMIT")


@contextlib.contextmanager
def _store_lock(root, *, readonly=False):
    fds, chain = runs._open_runs(root, create=False)
    anchor = None
    try:
        rootfd = fds[-2]
        rootinfo = os.fstat(rootfd)
        if rootinfo.st_uid != os.geteuid() or stat.S_IMODE(rootinfo.st_mode) != 0o700:
            raise ValueError("invalid simulation root")
        anchor = os.open(".lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=rootfd)
        info = os.fstat(anchor)

        def identity(value):
            return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_nlink)

        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise ValueError("invalid store lock")

        def check():
            if (
                identity(os.fstat(anchor)) != identity(info)
                or identity(os.stat(".lock", dir_fd=rootfd, follow_symlinks=False))
                != identity(info)
                or not _directory_matches(chain)
            ):
                raise ValueError("store lock or directory changed")

        check()
        store = TradingCalendarStore(root)
        store._layout()
        with store._readonly_lock() if readonly else store._writer_lock():
            check()
            yield store, fds[-1], chain, check
            check()
    finally:
        if anchor is not None:
            os.close(anchor)
        runs._close(fds)


def _date_name(job_key, day):
    return f"schedule-once-date-{hashlib.sha256(job_key.encode('ascii')).hexdigest()}-{day}.json"


def _name(kind, operation_id):
    if not runs._id(operation_id):
        raise ValueError("invalid operation ID")
    return f"schedule-once-{kind}-{operation_id}.json"


def _allocate(config):
    generated = [uuid.uuid4().hex for _ in range(10 + len(config["scopes"]))]
    if any(not runs._id(value) for value in generated) or len(set(generated)) != len(generated):
        raise ValueError("identity allocation unavailable")
    (
        op,
        new_run,
        batch_id,
        reservation,
        capture,
        parent,
        derived,
        candidate,
        review,
        pub,
        *oids,
    ) = generated
    version = uuid.uuid4().hex
    bundle = {
        "original_run_id": config["original_run_id"],
        "run_id": new_run,
        "batch_id": batch_id,
        "reservation_id": reservation,
        "capture_id": capture,
        "parent_id": parent,
        "derived_id": derived,
        "candidate_id": candidate,
        "review_id": review,
        "publication_id": "MOCK_" + pub,
        "version_id": "MOCK_" + version,
        "observations": [
            {"observation_id": oid, "scope_sha256": batch.scope_sha256(scope)}
            for oid, scope in zip(oids, config["scopes"], strict=True)
        ],
    }
    if not runs._id(version) or version in generated or op == config["original_run_id"]:
        raise ValueError("identity allocation collision")
    return op, bundle


def _validate_scopes(config, operator, requested_date, bundle):
    scopes = config["scopes"]
    batch._identity_bundle(bundle, config["original_run_id"], scopes, requested_date)
    keys = []
    for scope in scopes:
        if (
            type(scope) is not dict
            or set(scope) != {"contract", "target", "assumptions", "alignment", "timeout"}
            or scope["alignment"] != "REPEAT_OVER_MOCK_OWNER_DAY_MAPPING"
        ):
            raise ValueError("invalid scope")
        mixed.mixed._template_input(scope["target"], scope["assumptions"])
        keys.append(_scope_key(scope["target"]))
        akshare.validate_request(
            {
                "venue": scope["target"]["venue"],
                "contract": scope["contract"],
                "requested_date": requested_date,
                "kind": "future",
                "timeout": scope["timeout"],
            },
            year=operator._audit["year"],
        )
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate scope")
    for index, left in enumerate(keys):
        for right in keys[index + 1 :]:
            if (
                left[:2] == right[:2]
                and left[3] == right[3]
                and left[4] <= right[5]
                and right[4] <= left[5]
            ):
                raise ValueError("overlapping scopes")


def _operator_locked(root, run_id, store):
    operator = MockCalendarPublication(root=root, run_id=run_id, policy_control=True)
    if operator._declaration is None:
        raise ValueError("persistent simulation declaration required")
    operator._guard()
    operator._check_scope_migration_locked()
    if store._pending_exists() or store._policy_change_pending():
        raise ValueError("store operation pending")
    policy = store._review_policy_locked()
    if policy is None:
        raise ValueError("review policy unavailable")
    operator._check_policy_locked(policy)
    current = store.read_current()
    operator._check_simulated_current(current)
    return operator, current, policy


def _validate_pending(value, root):
    fields = {
        "schema",
        "environment",
        "source_authentication",
        "clock",
        "operation_id",
        "job_key",
        "root",
        "original_run_id",
        "config",
        "config_sha256",
        "config_file_sha256",
        "scopes_sha256",
        "utc_instant",
        "requested_date",
        "identity_bundle",
    }
    if (
        type(value) is not dict
        or set(value) != fields
        or value["schema"] != _PENDING
        or any(value[key] != expected for key, expected in LABEL.items())
        or value["root"] != root
        or not runs._id(value["operation_id"])
    ):
        raise ValueError("invalid pending")
    config = value["config"]
    _validate_config(config)
    if (
        config["root"] != root
        or config["job_key"] != value["job_key"]
        or config["original_run_id"] != value["original_run_id"]
        or value["config_sha256"] != _digest(config)
        or type(value["config_file_sha256"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", value["config_file_sha256"]) is None
        or value["scopes_sha256"] != _digest(config["scopes"])
    ):
        raise ValueError("pending config mismatch")
    instant = datetime.fromisoformat(value["utc_instant"].replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() != UTC.utcoffset(instant):
        raise ValueError("invalid local clock declaration")
    requested = date.fromisoformat(value["requested_date"])
    if (
        requested.isoformat() != value["requested_date"]
        or instant.astimezone(ZoneInfo("Asia/Shanghai")).date() != requested
    ):
        raise ValueError("date declaration mismatch")
    validated = batch._identity_bundle(
        value["identity_bundle"], value["original_run_id"], config["scopes"], requested
    )
    all_ids = {item for key, item in validated.items() if key != "observations"}
    all_ids.update(row["observation_id"] for row in validated["observations"])
    if value["operation_id"] in all_ids:
        raise ValueError("operation ID collision")
    return value


def _pending_locked(fd, chain, root):
    found = _read(fd, chain, _PENDING_FILE)
    if found is None:
        return None
    return _validate_pending(found[0], root), found[1]


def _returned(fd, chain, pending, pending_sha):
    found = _read(fd, chain, _name("returned", pending["operation_id"]))
    if found is None:
        return None
    value, digest = found
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema",
            "operation_id",
            "pending_sha256",
            "status",
            "batch_evidence_sha256",
            "batch_status",
            "review_payload_sha256",
            "result_sha256",
        }
        or value["schema"] != _RETURNED
        or value["operation_id"] != pending["operation_id"]
        or value["pending_sha256"] != pending_sha
        or value["status"] not in {"COMMITTED", "SAME", "PENDING_REVIEW", "UNKNOWN"}
        or type(value["result_sha256"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", value["result_sha256"]) is None
    ):
        raise ValueError("returned record invalid")
    for key in ("batch_evidence_sha256", "review_payload_sha256"):
        if value[key] is not None and (
            type(value[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
        ):
            raise ValueError("returned digest invalid")
    if value["batch_status"] not in {None, "COMPLETE", "PARTIAL"}:
        raise ValueError("returned batch status invalid")
    if value["status"] in {"COMMITTED", "SAME", "PENDING_REVIEW"} and (
        value["batch_evidence_sha256"] is None
        or value["batch_status"] is None
        or (value["status"] == "PENDING_REVIEW" and value["review_payload_sha256"] is None)
    ):
        raise ValueError("returned evidence missing")
    return value, digest


def _pending_result(pending, status="UNKNOWN", reason="ORIGINAL_OPERATION_PENDING"):
    return {
        **LABEL,
        "status": status,
        "reason": reason,
        "operation_id": pending["operation_id"],
        "job_key": pending["job_key"],
        "requested_date": pending["requested_date"],
        "identity_bundle": pending["identity_bundle"],
    }


def _returned_from_result(pending, pending_sha, result):
    if type(result) is not dict:
        raise ValueError("invalid batch result")
    status = result.get("status")
    ids = pending["identity_bundle"]
    if (
        status not in {"COMMITTED", "SAME", "PENDING_REVIEW"}
        or result.get("identity_bundle") != ids
        or any(
            result.get(key) != ids[key]
            for key in (
                "original_run_id",
                "run_id",
                "batch_id",
                "capture_id",
                "parent_id",
                "derived_id",
                "candidate_id",
                "review_id",
                "publication_id",
                "version_id",
            )
        )
        or result.get("requested_date") != pending["requested_date"]
    ):
        status = "UNKNOWN"
    evidence_sha = result.get("batch_evidence_sha256")
    review_sha = result.get("review_payload_sha256") if status == "PENDING_REVIEW" else None
    batch_status = result.get("batch_status")
    if (
        type(evidence_sha) is not str
        or re.fullmatch(r"[0-9a-f]{64}", evidence_sha) is None
        or batch_status not in {"COMPLETE", "PARTIAL"}
        or (
            status == "PENDING_REVIEW"
            and (type(review_sha) is not str or re.fullmatch(r"[0-9a-f]{64}", review_sha) is None)
        )
    ):
        status, evidence_sha, batch_status, review_sha = "UNKNOWN", None, None, None
    return {
        "schema": _RETURNED,
        "operation_id": pending["operation_id"],
        "pending_sha256": pending_sha,
        "status": status,
        "batch_evidence_sha256": evidence_sha,
        "batch_status": batch_status,
        "review_payload_sha256": review_sha,
        "result_sha256": _digest(result),
    }


def _batch_show_readonly(pending, *, locked=None):
    from bt_api_py import trading_calendar_scope_checks as checks

    root, ids = pending["root"], pending["identity_bundle"]
    operator = MockCalendarPublication(
        root=root, run_id=pending["original_run_id"], policy_control=True
    )
    raw_intent = batch._read(root, f"mock-daily-batch-intent-{ids['batch_id']}.json")
    raw_evidence = batch._read(root, f"mock-daily-batch-{ids['batch_id']}.json")
    if raw_intent is None or raw_evidence is None:
        raise ValueError("batch evidence unavailable")
    intent, _ = mixed._batch_intent(root, ids["batch_id"], raw_intent[1])
    evidence = mixed._batch_record(root, ids["batch_id"], raw_evidence[1])
    targets = [
        {"target": row["target"], "requested_contract": row["contract"]}
        for row in intent["observations"]
    ]
    if locked is None:
        scopes = checks.show(operator=operator, targets=targets, batch_id=ids["batch_id"])
    else:
        # Reuse the already-held Store lock; checks.show() would acquire it again.
        fd, chain = locked
        operator._guard()
        marker = checks._read(fd, chain, checks._HEAD)
        if marker is not None and marker[0].get("schema") == "bt_api.calendar-scope-check-head/2":
            from bt_api_py import trading_calendar_scope_index as scope_index

            scopes = scope_index.show_locked(
                operator=operator, fd=fd, chain=chain, targets=targets, batch_id=ids["batch_id"]
            )
        else:
            records, _ = checks._load_locked(operator.root, fd, chain)
            if not any(record[0]["batch_id"] == ids["batch_id"] for record in records):
                raise ValueError("original scope record unavailable")
            scopes = checks._result(records, targets)
        operator._guard()
    if scopes.get("status") != "VISIBLE_UNAUTHENTICATED":
        raise ValueError("scope history unavailable")
    return {
        "status": "VISIBLE_UNAUTHENTICATED",
        "intent": intent,
        "evidence": evidence,
        "evidence_sha256": raw_evidence[1],
        "scope_checks": scopes,
    }


def _scope_report(pending, returned):
    """Read an unauthenticated, bounded report without changing the operation outcome."""
    unknown = {
        "status": "UNKNOWN",
        "reason": "SCOPE_EVIDENCE_UNAVAILABLE",
        "venue_last_success": "UNKNOWN",
    }
    try:
        from bt_api_py import trading_calendar_mixed_simulation_version as calendar_mixed

        shown = _batch_show_readonly(pending)
        if shown["evidence_sha256"] != returned["batch_evidence_sha256"]:
            raise ValueError("batch evidence changed")
        intent, evidence, checked = shown["intent"], shown["evidence"], shown["scope_checks"]
        scopes = pending["config"]["scopes"]
        rows, checks = evidence["observations"], checked["scopes"]
        if (
            intent["batch_id"] != pending["identity_bundle"]["batch_id"]
            or intent["run_id"] != pending["identity_bundle"]["run_id"]
            or evidence["schema"] not in {
                "bt_api.calendar-daily-batch-evidence/3",
                "bt_api.calendar-daily-batch-evidence/4",
            }
            or returned["batch_status"]
            != ("COMPLETE" if evidence["schema"].endswith("/3") else "PARTIAL")
            or type(rows) is not list
            or type(checks) is not list
            or not 2 <= len(scopes) == len(rows) == len(checks) <= 8
        ):
            raise ValueError("batch report mismatch")
        values = []
        for scope, row, check, identity in zip(
            scopes, rows, checks, pending["identity_bundle"]["observations"], strict=True
        ):
            if (
                row["target"] != scope["target"]
                or row["contract"] != scope["contract"]
                or row["observation_id"] != identity["observation_id"]
                or check["target"] != scope["target"]
                or check["requested_contract"] != scope["contract"]
                or type(scope["contract"]) is not str
                or re.fullmatch(r"[A-Za-z]{1,8}[0-9]{3,4}", scope["contract"], re.ASCII) is None
            ):
                raise ValueError("report scope mismatch")
            calendar_mixed._template_input(scope["target"], scope["assumptions"])
            observed = row["observation_status"]
            if observed == "SUCCESS":
                if (
                    row["observation_state"] != "COMMITTED"
                    or row["projection_state"] != "VISIBLE_UNAUTHENTICATED"
                    or row.get("check_status", "SUCCESS") != "SUCCESS"
                    or row.get("disposition", "UPDATED_AKSHARE") != "UPDATED_AKSHARE"
                ):
                    raise ValueError("success unavailable")
                status, disposition = "SUCCESS", "UPDATED_AKSHARE"
            elif observed in mixed._DEFINITE_FAILURES:
                status, disposition = row.get("check_status"), row.get("disposition")
                if status != "FAILED" or disposition not in {"RETAINED_AKSHARE", "MOCK_FALLBACK"}:
                    raise ValueError("failure unavailable")
            else:
                raise ValueError("unknown observation")
            last = check["last_success"]
            if last.get("status") == "UNKNOWN":
                last_result = {"status": "UNKNOWN"}
            elif last.get("status") == "VISIBLE_UNAUTHENTICATED":
                if type(last.get("declared_utc")) is not str or len(last["declared_utc"]) > 40:
                    raise ValueError("invalid last success time")
                stamp = datetime.fromisoformat(last["declared_utc"].replace("Z", "+00:00"))
                if (
                    stamp.tzinfo is None
                    or stamp.utcoffset() != UTC.utcoffset(stamp)
                    or type(last["sequence"]) is not int
                    or not 1 <= last["sequence"] <= 2**63 - 1
                ):
                    raise ValueError("invalid last success")
                last_result = {
                    "status": "VISIBLE_UNAUTHENTICATED",
                    "declared_utc": stamp.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                    "sequence": last["sequence"],
                    "clock": "CLOCK_UNVERIFIED",
                }
            else:
                raise ValueError("last success unavailable")
            values.append({
                "target": dict(scope["target"]),
                "requested_contract": scope["contract"],
                "check_status": status,
                "observation_status": observed,
                "reason": observed if status == "FAILED" else "NONE",
                "disposition": disposition,
                "last_success": last_result,
            })
        return {
            "status": "VISIBLE_UNAUTHENTICATED",
            "source_authentication": "NOT_PROVEN",
            "last_success_basis": "CURRENT_REPORT_READ_UNAUTHENTICATED",
            "venue_last_success": "UNKNOWN",
            "scopes": values,
        }
    except Exception:
        return unknown


def _with_scope_report(result, pending, returned):
    try:
        report = _scope_report(pending, returned)
    except KeyboardInterrupt as exc:
        with contextlib.suppress(Exception):
            exc.operation_id = pending["operation_id"]
            exc.identity_bundle = pending["identity_bundle"]
            exc.calendar_outcome = {
                **result,
                "reason": "SCOPE_REPORT_INTERRUPTED",
                "scope_report": {"status": "UNKNOWN", "venue_last_success": "UNKNOWN"},
            }
        raise
    return {**result, "scope_report": report}


def _review_not_started(root, review_id):
    from bt_api_py import trading_calendar_review_bundle as review

    fds, chain = review._open(root, False)
    try:
        return all(
            storage._read(fds[-1], chain, prefix + review_id + ".json", review._OUTPUT_LIMIT)
            is None
            for prefix in ("mock-review-stale-", "mock-confirm-intent-")
        )
    finally:
        runs._close(fds)


def _batch_proof(pending, returned, *, allow_review, locked=None):
    root, ids = pending["root"], pending["identity_bundle"]
    shown = _batch_show_readonly(pending, locked=locked)
    if (
        shown.get("status") != "VISIBLE_UNAUTHENTICATED"
        or shown.get("evidence_sha256") != returned["batch_evidence_sha256"]
    ):
        raise ValueError("original batch evidence unavailable")
    intent, evidence = shown["intent"], shown["evidence"]
    if (
        intent["schema"] != "bt_api.calendar-daily-batch-intent/2"
        or any(
            intent[key] != ids[key]
            for key in (
                "original_run_id",
                "run_id",
                "batch_id",
                "reservation_id",
                "capture_id",
                "parent_id",
                "derived_id",
                "candidate_id",
                "review_id",
                "publication_id",
                "version_id",
            )
        )
        or intent["requested_date"] != pending["requested_date"]
        or [
            {"observation_id": row["observation_id"], "scope_sha256": row["scope_sha256"]}
            for row in intent["observations"]
        ]
        != ids["observations"]
        or [
            {key: row[key] for key in ("contract", "target", "assumptions", "alignment", "timeout")}
            for row in intent["observations"]
        ]
        != pending["config"]["scopes"]
        or evidence["schema"]
        not in {"bt_api.calendar-daily-batch-evidence/3", "bt_api.calendar-daily-batch-evidence/4"}
        or returned["batch_status"]
        != ("COMPLETE" if evidence["schema"].endswith("/3") else "PARTIAL")
    ):
        raise ValueError("batch identity mismatch")
    operator = MockCalendarPublication(root=root, run_id=ids["run_id"], policy_control=True)
    review_sha = returned["review_payload_sha256"]
    if returned["status"] == "PENDING_REVIEW":
        review = mock_cli._read_pending(operator, ids["review_id"])
        if (
            review.get("status") != "VISIBLE_UNAUTHENTICATED"
            or review.get("payload_sha256") != review_sha
            or any(
                review["body"].get(key) != ids[key]
                for key in ("run_id", "candidate_id", "review_id", "publication_id", "version_id")
            )
        ):
            raise ValueError("original review unavailable")
        if allow_review:
            if not _review_not_started(root, ids["review_id"]):
                raise ValueError("review requires reconciliation")
            return {
                "kind": "PENDING_REVIEW",
                "evidence_sha256": shown["evidence_sha256"],
                "review_payload_sha256": review_sha,
            }
    store = TradingCalendarStore(root)
    pointer_before = store._read_bounded(Path(root) / "CURRENT.json", 65536)
    candidate_body_sha = _digest(mixed._body(root, ids["candidate_id"]))
    if returned["status"] in {"COMMITTED", "PENDING_REVIEW"}:
        history = mixed.read_history(
            root=root,
            filesystem=FS,
            candidate_id=ids["candidate_id"],
            publication_id=ids["publication_id"],
        )
        if (
            history.get("status") != "VISIBLE_UNAUTHENTICATED"
            or history.get("publication_consistency") != "RECEIPT_CHAIN_BOUND_UNAUTHENTICATED"
        ):
            raise ValueError("publication proof unavailable")
        pointer = store._read_bounded(Path(root) / "CURRENT.json", 65536)
        if pointer != pointer_before:
            raise ValueError("publication pointer changed")
        receipt, _ = store._receipt_proof(ids["publication_id"])
        if receipt.get("version_id") != ids["version_id"]:
            raise ValueError("publication version mismatch")
        if returned["status"] == "PENDING_REVIEW":
            event = json.loads(
                store._read_bounded(
                    Path(root) / "publications" / f"{ids['publication_id']}.json", 4 * 1024 * 1024
                )
            )
            if not (event.get("review_policy") or {}).get("reviewer"):
                raise ValueError("manual confirmation unavailable")
        return {
            "kind": "COMMITTED",
            "evidence_sha256": shown["evidence_sha256"],
            "candidate_payload_sha256": history["candidate_payload_sha256"],
            "candidate_body_sha256": candidate_body_sha,
            "pointer_sha256": hashlib.sha256(pointer).hexdigest(),
            "review_payload_sha256": review_sha,
        }
    if returned["status"] == "SAME":
        proposal = operator.prepare(candidate_id=ids["candidate_id"])
        claim = operator.inspect_publication_claim(publication_id=ids["publication_id"])
        version = operator.inspect_version_claim(version_id=ids["version_id"])
        if (
            proposal.get("status") != "SAME"
            or claim.get("status") != "VISIBLE_UNAUTHENTICATED"
            or version.get("status") != "VISIBLE_UNAUTHENTICATED"
        ):
            raise ValueError("no-change proof unavailable")
        operator._check_batch_identity(
            proposal,
            publication_id=ids["publication_id"],
            version_id=ids["version_id"],
            review_id=ids["review_id"],
        )
        pub, ver = claim["claim"], version["claim"]
        if (
            pub["state"] != "NO_CHANGE"
            or pub["candidate_id"] != ids["candidate_id"]
            or pub["version_id"] != ids["version_id"]
            or pub["proposal_sha256"] != _hash(proposal)
            or ver["publication_id"] != ids["publication_id"]
            or ver["version_id"] != ids["version_id"]
            or ver["proposal_sha256"] != _hash(proposal)
        ):
            raise ValueError("no-change binding mismatch")
        pointer = store._read_bounded(Path(root) / "CURRENT.json", 65536)
        if pointer != pointer_before:
            raise ValueError("same pointer changed")
        return {
            "kind": "SAME",
            "evidence_sha256": shown["evidence_sha256"],
            "proposal_sha256": _hash(proposal),
            "proposal": proposal,
            "candidate_body_sha256": candidate_body_sha,
            "pointer_sha256": hashlib.sha256(pointer).hexdigest(),
            "review_payload_sha256": None,
        }
    raise ValueError("batch outcome unknown")


def _verify_locked(store, pending, returned, proof):
    root, ids = pending["root"], pending["identity_bundle"]
    operator, _, policy = _operator_locked(root, ids["run_id"], store)
    pointer = store._read_bounded(Path(root) / "CURRENT.json", 65536)
    if hashlib.sha256(pointer).hexdigest() != proof["pointer_sha256"]:
        raise ValueError("current changed")
    record = mixed._batch_record(root, ids["batch_id"], returned["batch_evidence_sha256"])
    intent, _ = mixed._batch_intent(root, ids["batch_id"], record["intent_sha256"])
    if (
        intent["reservation_id"] != ids["reservation_id"]
        or intent["run_id"] != ids["run_id"]
        or intent["candidate_id"] != ids["candidate_id"]
        or intent["requested_date"] != pending["requested_date"]
        or _digest(mixed._body(root, ids["candidate_id"])) != proof["candidate_body_sha256"]
    ):
        raise ValueError("locked batch identity changed")
    from bt_api_py import trading_calendar_review_bundle as review

    candidate = review.show(root=root, candidate_id=ids["candidate_id"], filesystem=FS)
    if candidate.get("status") != "VISIBLE_UNAUTHENTICATED":
        raise ValueError("candidate unavailable")
    candidate_body = mixed._body(root, ids["candidate_id"])
    if (
        candidate_body.get("batch_id") != ids["batch_id"]
        or candidate_body.get("batch_record_sha256") != returned["batch_evidence_sha256"]
    ):
        raise ValueError("candidate batch evidence changed")
    baseline = candidate_body["baseline"]
    total = 0

    def bounded(path):
        nonlocal total
        remaining = 128 * 1024 * 1024 - total
        if remaining <= 0:
            raise ValueError("history read budget exceeded")
        data = store._read_bounded(path, min(32 * 1024 * 1024, remaining))
        total += len(data)
        return data

    store._validate_chain_receipts(pointer, _reader=bounded, _max_publications=256)
    base_receipt = store._read_receipt(baseline["publication_id"], _reader=bounded)
    base_pointer = base_receipt["pointer"]
    if base_pointer["publication_sha256"] != baseline["publication_sha256"]:
        raise ValueError("baseline receipt changed")
    store._load(
        pointer,
        target=base_pointer,
        target_version=base_receipt["version_id"],
        _reader=bounded,
        _max_publications=256,
    )
    previous = store._load(mixed._canonical(base_pointer), _reader=bounded, _max_publications=256)
    if previous.version_sha256 != baseline["version_sha256"]:
        raise ValueError("baseline version changed")
    mixed._verify_batch_record(record, candidate_body["templates"], ids["candidate_id"], baseline)
    materialized = mixed._materialize_batch(
        root,
        FS,
        ids["candidate_id"],
        candidate_body["templates"],
        previous,
        candidate_body["batch_record_sha256"],
        record,
    )
    if (
        candidate["bundle"]["input"] != materialized["input"]
        or candidate_body["provenance"] != materialized["provenance"]
    ):
        raise ValueError("locked candidate evidence changed")
    if proof["kind"] == "COMMITTED":
        receipt = store._read_receipt(ids["publication_id"], _reader=bounded)
        store._load(
            pointer,
            target=receipt["pointer"],
            target_version=receipt["version_id"],
            _reader=bounded,
            _max_publications=256,
        )
        if (
            receipt["version_id"] != ids["version_id"]
            or candidate.get("payload_sha256") != proof["candidate_payload_sha256"]
        ):
            raise ValueError("locked publication proof changed")
    elif proof["kind"] == "SAME":
        proposal = proof["proposal"]
        current = store.read_current()
        if proposal.get("policy") != asdict(policy) or proposal.get("baseline") != asdict(
            CurrentPrecondition.matching(current.publication_id, current.publication_sha256)
        ):
            raise ValueError("no-change baseline changed")
        operator._check_batch_identity(
            proposal,
            publication_id=ids["publication_id"],
            version_id=ids["version_id"],
            review_id=ids["review_id"],
        )
        claim = operator.inspect_publication_claim(publication_id=ids["publication_id"])
        version = operator.inspect_version_claim(version_id=ids["version_id"])
        if (
            claim.get("status") != "VISIBLE_UNAUTHENTICATED"
            or version.get("status") != "VISIBLE_UNAUTHENTICATED"
            or claim["claim"].get("state") != "NO_CHANGE"
            or claim["claim"].get("proposal_sha256") != proof["proposal_sha256"]
            or version["claim"].get("proposal_sha256") != proof["proposal_sha256"]
        ):
            raise ValueError("no-change claim changed")
    else:
        raise ValueError("unsupported terminal proof")
    if returned["status"] == "PENDING_REVIEW":
        pending_review = mock_cli._read_pending(operator, ids["review_id"])
        if (
            pending_review.get("status") != "VISIBLE_UNAUTHENTICATED"
            or pending_review.get("payload_sha256") != returned["review_payload_sha256"]
        ):
            raise ValueError("review binding changed")


def _stage(fd, chain, operation_id, kind, value):
    """On an incomplete prior write, observe only; never recreate the target."""
    target = _name(kind, operation_id)
    attempt_name = _name(kind + "-attempt", operation_id)
    marker = {
        "schema": "bt_api.calendar-schedule-once-stage/1",
        "operation_id": operation_id,
        "stage": kind,
        "payload_sha256": _digest(value),
    }
    old_attempt, old_target = _read(fd, chain, attempt_name), _read(fd, chain, target)
    if old_attempt is not None:
        if old_attempt[0] != marker or old_target is None or old_target[0] != value:
            raise ValueError("original stage requires reconciliation")
        return old_target[1]
    if old_target is not None:
        raise ValueError("stage target without original marker")
    _write(fd, chain, attempt_name, marker)
    return _write(fd, chain, target, value)


def _terminal_records(pending, pending_sha, returned, returned_sha, proof):
    stored_proof = {key: value for key, value in proof.items() if key != "proposal"}
    terminal = {
        "schema": _TERMINAL,
        **LABEL,
        "operation_id": pending["operation_id"],
        "pending_sha256": pending_sha,
        "returned_sha256": returned_sha,
        "job_key": pending["job_key"],
        "requested_date": pending["requested_date"],
        "identity_bundle": pending["identity_bundle"],
        "batch_status": returned["batch_status"],
        "outcome": proof["kind"],
        "proof": stored_proof,
    }
    return terminal


def _release_initial_fence(fd, chain, check):
    check()
    os.fsync(fd)
    os.unlink(_INIT_FENCE, dir_fd=fd)
    os.fsync(fd)
    check()
    if _read(fd, chain, _INIT_FENCE) is not None:
        raise ValueError("initialization fence removal uncertain")


def _release_pending(fd, chain, check):
    check()
    os.fsync(fd)
    os.unlink(_PENDING_FILE, dir_fd=fd)
    os.fsync(fd)
    check()
    if _read(fd, chain, _PENDING_FILE) is not None:
        raise ValueError("pending removal uncertain")


def _finish(pending, pending_sha, returned, returned_sha):
    proof = _batch_proof(pending, returned, allow_review=False)
    terminal = _terminal_records(pending, pending_sha, returned, returned_sha, proof)
    terminal_sha = _digest(terminal)
    index = {
        "schema": _INDEX,
        "operation_id": pending["operation_id"],
        "job_key": pending["job_key"],
        "requested_date": pending["requested_date"],
        "terminal_sha256": terminal_sha,
        "pending_sha256": pending_sha,
    }
    index_name = _date_name(pending["job_key"], pending["requested_date"])
    with _store_lock(pending["root"]) as (store, fd, chain, check):
        with _ledger(pending["root"], fd, chain) as db:
            active, last = _ledger_state(db, pending["root"])
            row = _ledger_row(db, pending["operation_id"], pending["root"])
            if (
                row[:2] != (pending, pending_sha)
                or row[3] != returned_sha
                or not (
                    (row[2] == "RETURNED" and active == pending["operation_id"])
                    or (
                        row[2] == "TERMINAL"
                        and active is None
                        and last == pending["operation_id"]
                        and row[4] == terminal_sha
                    )
                )
            ):
                raise ValueError("original ledger operation changed")
            current = _pending_locked(fd, chain, pending["root"])
            if current != (pending, pending_sha) or _returned(fd, chain, pending, pending_sha) != (
                returned,
                returned_sha,
            ):
                raise ValueError("original operation changed")
            if (
                _read_exact(fd, chain, _name("intent", pending["operation_id"]), pending)
                != pending_sha
            ):
                raise ValueError("original retained intent changed")
            _verify_locked(store, pending, returned, proof)
            if _stage(fd, chain, pending["operation_id"], "terminal", terminal) != terminal_sha:
                raise ValueError("terminal digest mismatch")
            index_stage = _name("index-attempt", pending["operation_id"])
            index_marker = {
                "schema": "bt_api.calendar-schedule-once-stage/1",
                "operation_id": pending["operation_id"],
                "stage": "index",
                "payload_sha256": _digest(index),
            }
            old_marker, old_index = _read(fd, chain, index_stage), _read(fd, chain, index_name)
            if old_marker is None:
                if old_index is not None:
                    raise ValueError("date index conflict")
                _write(fd, chain, index_stage, index_marker)
                _write(fd, chain, index_name, index)
            elif old_marker[0] != index_marker or old_index is None or old_index[0] != index:
                raise ValueError("original date index requires reconciliation")
            _read_exact(fd, chain, _name("terminal", pending["operation_id"]), terminal)
            _read_exact(fd, chain, index_name, index)
            if _pending_locked(fd, chain, pending["root"]) != (pending, pending_sha):
                raise ValueError("pending changed")
            if row[2] == "RETURNED":
                _ledger_terminal(db, pending["operation_id"], terminal_sha)
                if _ledger_row(db, pending["operation_id"], pending["root"])[2:] != (
                    "TERMINAL",
                    returned_sha,
                    terminal_sha,
                    1,
                ) or _ledger_state(db, pending["root"]) != (None, pending["operation_id"]):
                    raise ValueError("schedule terminal readback changed")
        _release_pending(fd, chain, check)
    return _with_scope_report({
        **_pending_result(pending, status=proof["kind"], reason="TERMINAL_VERIFIED"),
        "batch_status": returned["batch_status"],
        "terminal_sha256": terminal_sha,
    }, pending, returned)


def _completed_index(fd, chain, config, day):
    found = _read(fd, chain, _date_name(config["job_key"], day))
    if found is None:
        return None
    index = found[0]
    if (
        type(index) is not dict
        or set(index)
        != {
            "schema",
            "operation_id",
            "job_key",
            "requested_date",
            "terminal_sha256",
            "pending_sha256",
        }
        or index["schema"] != _INDEX
        or index["job_key"] != config["job_key"]
        or index["requested_date"] != day
        or not runs._id(index["operation_id"])
    ):
        raise ValueError("date index invalid")
    terminal = _read(fd, chain, _name("terminal", index["operation_id"]))
    if (
        terminal is None
        or terminal[1] != index["terminal_sha256"]
        or type(terminal[0]) is not dict
        or terminal[0].get("schema") != _TERMINAL
        or terminal[0].get("operation_id") != index["operation_id"]
        or terminal[0].get("pending_sha256") != index["pending_sha256"]
        or terminal[0].get("job_key") != index["job_key"]
        or terminal[0].get("requested_date") != day
        or terminal[0].get("outcome") not in {"COMMITTED", "SAME"}
    ):
        raise ValueError("terminal index unavailable")
    retained = _read(fd, chain, _name("intent", index["operation_id"]))
    if retained is None or retained[1] != index["pending_sha256"]:
        raise ValueError("retained original intent unavailable")
    pending = _validate_pending(retained[0], config["root"])
    returned = _returned(fd, chain, pending, retained[1])
    if (
        pending["operation_id"] != index["operation_id"]
        or pending["job_key"] != index["job_key"]
        or pending["requested_date"] != day
        or returned is None
        or terminal[0].get("returned_sha256") != returned[1]
        or terminal[0].get("identity_bundle") != pending["identity_bundle"]
    ):
        raise ValueError("terminal original identity changed")
    value = terminal[0]
    expected_fields = {
        "schema",
        *LABEL,
        "operation_id",
        "pending_sha256",
        "returned_sha256",
        "job_key",
        "requested_date",
        "identity_bundle",
        "batch_status",
        "outcome",
        "proof",
    }
    proof = value.get("proof")
    expected_proof = (
        {
            "kind",
            "evidence_sha256",
            "candidate_payload_sha256",
            "candidate_body_sha256",
            "pointer_sha256",
            "review_payload_sha256",
        }
        if value["outcome"] == "COMMITTED"
        else {
            "kind",
            "evidence_sha256",
            "proposal_sha256",
            "candidate_body_sha256",
            "pointer_sha256",
            "review_payload_sha256",
        }
    )
    if (
        set(value) != expected_fields
        or any(value[key] != expected for key, expected in LABEL.items())
        or value["batch_status"] != returned[0]["batch_status"]
        or (returned[0]["status"] == "SAME") != (value["outcome"] == "SAME")
        or returned[0]["status"] not in {"COMMITTED", "SAME", "PENDING_REVIEW"}
        or type(proof) is not dict
        or set(proof) != expected_proof
        or proof["kind"] != value["outcome"]
        or proof["evidence_sha256"] != returned[0]["batch_evidence_sha256"]
        or proof["review_payload_sha256"] != returned[0]["review_payload_sha256"]
        or any(
            type(proof[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", proof[key]) is None
            for key in expected_proof - {"kind", "review_payload_sha256"}
        )
    ):
        raise ValueError("terminal evidence invalid")
    for kind, value in (("terminal", terminal[0]), ("index", index)):
        attempt = _read(fd, chain, _name(kind + "-attempt", index["operation_id"]))
        marker = {
            "schema": "bt_api.calendar-schedule-once-stage/1",
            "operation_id": index["operation_id"],
            "stage": kind,
            "payload_sha256": _digest(value),
        }
        if attempt is None or attempt[0] != marker:
            raise ValueError("terminal stage evidence unavailable")
    return terminal[0], terminal[1]


def _report_pending(pending, pending_sha, returned):
    if returned is None:
        return _pending_result(pending)
    if returned[0]["status"] == "PENDING_REVIEW":
        try:
            _batch_proof(pending, returned[0], allow_review=True)
            return _with_scope_report({
                **_pending_result(pending, "PENDING_REVIEW", "ORIGINAL_REVIEW_PENDING"),
                "batch_status": returned[0]["batch_status"],
                "review_payload_sha256": returned[0]["review_payload_sha256"],
            }, pending, returned[0])
        except Exception:
            return _pending_result(pending, reason="REVIEW_EVIDENCE_UNAVAILABLE")
    return _pending_result(pending, reason="ORIGINAL_RECONCILE_REQUIRED")


def _review_policy_fields(value):
    if (
        type(value) is not dict
        or set(value) != {"directory_id", "revision", "mode", "mode_source"}
        or not _identifier(value["directory_id"])
        or not _identifier(value["revision"])
        or value["mode"] not in {"auto", "manual"}
        or value["mode_source"] not in {"default", "explicit"}
        or (value["mode_source"] == "default" and value["mode"] != "auto")
    ):
        raise ValueError("review policy record invalid")
    return value


def _review_policy_divergence_locked(store, pending, returned):
    """Compare the review's embedded policy with the committed policy under the Store lock.

    Returns (stale, embedded_policy, committed_policy). The embedded policy must appear
    in the committed change chain as an old policy; otherwise the review is not provably
    stale and the caller must keep using the original operation identity.
    """
    root, ids = pending["root"], pending["identity_bundle"]
    operator, _, policy = _operator_locked(root, ids["run_id"], store)
    review = mock_cli._read_pending(operator, ids["review_id"])
    if (
        review.get("status") != "VISIBLE_UNAUTHENTICATED"
        or review.get("payload_sha256") != returned["review_payload_sha256"]
    ):
        raise ValueError("review unavailable")
    old = _review_policy_fields(review["body"]["proposal"].get("policy"))
    current = asdict(policy)
    if old == current:
        return False, old, policy
    raw = _decode(store._read(Path(root) / "REVIEW_POLICY.json"))
    chain = store._policy_chain_locked(raw)
    if not any(asdict(_policy_value(record["old_policy"])) == old for record in chain):
        raise ValueError("review policy absent from committed chain")
    return True, old, policy


def _check_review_policy_locked(store, pending, returned):
    """True only when the pending review was created under a superseded policy."""
    return _review_policy_divergence_locked(store, pending, returned)[0]


def _stale_records(
    pending, pending_sha, returned, returned_sha, old_policy, policy, candidate_body_sha
):
    ids = pending["identity_bundle"]
    disposition = {
        "schema": _DISPOSITION,
        **LABEL,
        "operation_id": pending["operation_id"],
        "job_key": pending["job_key"],
        "requested_date": pending["requested_date"],
        "pending_sha256": pending_sha,
        "returned_sha256": returned_sha,
        "identity_bundle": ids,
        "previous_policy": old_policy,
        "current_policy": asdict(policy),
        "review_payload_sha256": returned["review_payload_sha256"],
        "batch_evidence_sha256": returned["batch_evidence_sha256"],
        "candidate_body_sha256": candidate_body_sha,
        "outcome": _STALE_OUTCOME,
        "effective": False,
        "publication": "NOT_ATTEMPTED",
        "review_record": "RETAINED_UNCHANGED",
        "candidate_record": "RETAINED_NOT_PUBLISHED",
        "audit_records": "RETAINED_UNCHANGED",
    }
    disposition_sha = _digest(disposition)
    terminal = {
        "schema": _TERMINAL,
        **LABEL,
        "operation_id": pending["operation_id"],
        "pending_sha256": pending_sha,
        "returned_sha256": returned_sha,
        "job_key": pending["job_key"],
        "requested_date": pending["requested_date"],
        "identity_bundle": ids,
        "batch_status": returned["batch_status"],
        "outcome": _STALE_OUTCOME,
        "proof": {
            "kind": _STALE_OUTCOME,
            "review_payload_sha256": returned["review_payload_sha256"],
            "batch_evidence_sha256": returned["batch_evidence_sha256"],
            "candidate_id": ids["candidate_id"],
            "candidate_body_sha256": candidate_body_sha,
            "publication_id": ids["publication_id"],
            "version_id": ids["version_id"],
            "previous_policy": old_policy,
            "current_policy": asdict(policy),
            "disposition_sha256": disposition_sha,
            "publication": "NOT_ATTEMPTED",
            "review_record": "RETAINED_UNCHANGED",
            "candidate_record": "RETAINED_NOT_PUBLISHED",
        },
    }
    return disposition, terminal


def _stale_terminal(fd, chain, pending, pending_sha, returned_sha, returned, terminal_sha):
    """Validate a non-effective stale closure without requiring a date index."""
    operation_id = pending["operation_id"]
    found = _read(fd, chain, _name("terminal", operation_id))
    if found is None or found[1] != terminal_sha:
        raise ValueError("stale terminal unavailable")
    value = found[0]
    fields = {
        "schema",
        *LABEL,
        "operation_id",
        "pending_sha256",
        "returned_sha256",
        "job_key",
        "requested_date",
        "identity_bundle",
        "batch_status",
        "outcome",
        "proof",
    }
    proof_fields = {
        "kind",
        "review_payload_sha256",
        "batch_evidence_sha256",
        "candidate_id",
        "candidate_body_sha256",
        "publication_id",
        "version_id",
        "previous_policy",
        "current_policy",
        "disposition_sha256",
        "publication",
        "review_record",
        "candidate_record",
    }
    proof = value.get("proof") if type(value) is dict else None
    if (
        type(value) is not dict
        or set(value) != fields
        or any(value[key] != expected for key, expected in LABEL.items())
        or value["schema"] != _TERMINAL
        or value["operation_id"] != operation_id
        or value["pending_sha256"] != pending_sha
        or value["returned_sha256"] != returned_sha
        or value["job_key"] != pending["job_key"]
        or value["requested_date"] != pending["requested_date"]
        or value["identity_bundle"] != pending["identity_bundle"]
        or value["batch_status"] != returned["batch_status"]
        or value["outcome"] != _STALE_OUTCOME
        or type(proof) is not dict
        or set(proof) != proof_fields
        or proof["kind"] != _STALE_OUTCOME
        or proof["review_payload_sha256"] != returned["review_payload_sha256"]
        or proof["batch_evidence_sha256"] != returned["batch_evidence_sha256"]
        or proof["candidate_id"] != pending["identity_bundle"]["candidate_id"]
        or proof["publication_id"] != pending["identity_bundle"]["publication_id"]
        or proof["version_id"] != pending["identity_bundle"]["version_id"]
        or proof["publication"] != "NOT_ATTEMPTED"
        or proof["review_record"] != "RETAINED_UNCHANGED"
        or proof["candidate_record"] != "RETAINED_NOT_PUBLISHED"
        or any(
            type(proof[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", proof[key]) is None
            for key in ("disposition_sha256", "candidate_body_sha256")
        )
    ):
        raise ValueError("stale terminal invalid")
    previous = _review_policy_fields(proof["previous_policy"])
    current = _review_policy_fields(proof["current_policy"])
    if previous == current:
        raise ValueError("stale terminal policy unchanged")
    disposition = _read(fd, chain, _name("disposition", operation_id))
    if disposition is None or disposition[1] != proof["disposition_sha256"]:
        raise ValueError("stale disposition unavailable")
    body = disposition[0]
    if (
        type(body) is not dict
        or set(body)
        != {
            "schema",
            *LABEL,
            "operation_id",
            "job_key",
            "requested_date",
            "pending_sha256",
            "returned_sha256",
            "identity_bundle",
            "previous_policy",
            "current_policy",
            "review_payload_sha256",
            "batch_evidence_sha256",
            "candidate_body_sha256",
            "outcome",
            "effective",
            "publication",
            "review_record",
            "candidate_record",
            "audit_records",
        }
        or body["schema"] != _DISPOSITION
        or any(body[key] != expected for key, expected in LABEL.items())
        or body["operation_id"] != operation_id
        or body["job_key"] != pending["job_key"]
        or body["requested_date"] != pending["requested_date"]
        or body["pending_sha256"] != pending_sha
        or body["returned_sha256"] != value["returned_sha256"]
        or body["identity_bundle"] != pending["identity_bundle"]
        or body["previous_policy"] != previous
        or body["current_policy"] != current
        or body["review_payload_sha256"] != returned["review_payload_sha256"]
        or body["batch_evidence_sha256"] != proof["batch_evidence_sha256"]
        or body["candidate_body_sha256"] != proof["candidate_body_sha256"]
        or body["outcome"] != _STALE_OUTCOME
        or body["effective"] is not False
        or body["publication"] != "NOT_ATTEMPTED"
        or body["review_record"] != "RETAINED_UNCHANGED"
        or body["candidate_record"] != "RETAINED_NOT_PUBLISHED"
        or body["audit_records"] != "RETAINED_UNCHANGED"
    ):
        raise ValueError("stale disposition invalid")
    for kind, record in (("terminal", value), ("disposition", body)):
        attempt = _read(fd, chain, _name(kind + "-attempt", operation_id))
        marker = {
            "schema": "bt_api.calendar-schedule-once-stage/1",
            "operation_id": operation_id,
            "stage": kind,
            "payload_sha256": _digest(record),
        }
        if attempt is None or attempt[0] != marker:
            raise ValueError("stale stage evidence unavailable")
    return value, found[1]


def _close_stale_review_locked(
    store, db, fd, chain, check, pending, pending_sha, returned, returned_sha, old_policy, policy
):
    """Close one policy-stale pending review as a retained, non-effective record.

    The candidate, review and audit bytes stay untouched and no date index is written;
    the date stays open for a fresh effective operation allocated by the caller.
    """
    root, operation_id = pending["root"], pending["operation_id"]
    row = _ledger_row(db, operation_id, root)
    if (
        row[:2] != (pending, pending_sha)
        or row[2] != "RETURNED"
        or row[3] != returned_sha
        or row[5] != 1
        or returned["status"] != "PENDING_REVIEW"
        or _ledger_state(db, root)[0] != operation_id
        or _pending_locked(fd, chain, root) != (pending, pending_sha)
        or _returned(fd, chain, pending, pending_sha) != (returned, returned_sha)
        or _read_exact(fd, chain, _name("intent", operation_id), pending) != pending_sha
    ):
        raise ValueError("original review operation changed")
    stale, fresh_old, fresh_policy = _review_policy_divergence_locked(store, pending, returned)
    if not stale or fresh_old != old_policy or asdict(fresh_policy) != asdict(policy):
        raise ValueError("review policy changed")
    # Retained audit and review bytes must still match the returned record before closing.
    _batch_proof(pending, returned, allow_review=True, locked=(fd, chain))
    candidate_body_sha = _digest(mixed._body(root, pending["identity_bundle"]["candidate_id"]))
    disposition, terminal = _stale_records(
        pending, pending_sha, returned, returned_sha, old_policy, policy, candidate_body_sha
    )
    _stage(fd, chain, operation_id, "disposition", disposition)
    terminal_sha = _stage(fd, chain, operation_id, "terminal", terminal)
    _read_exact(fd, chain, _name("terminal", operation_id), terminal)
    _ledger_terminal(db, operation_id, terminal_sha, effective=False)
    if _ledger_row(db, operation_id, root)[2:] != (
        "TERMINAL",
        returned_sha,
        terminal_sha,
        0,
    ) or _ledger_state(db, root) != (None, operation_id):
        raise ValueError("schedule stale terminal readback changed")
    _release_pending(fd, chain, check)
    if _pending_locked(fd, chain, root) is not None:
        raise ValueError("stale pending release changed")
    return terminal_sha


def _verify_terminal_files(db, fd, chain, root, operation_id):
    """Validate one terminal row; a non-effective closure is never a completed date."""
    row = _ledger_row(db, operation_id, root)
    if row[2] != "TERMINAL":
        raise ValueError("original operation not terminal")
    pending = row[0]
    returned = _returned(fd, chain, pending, row[1])
    if returned is None or returned[1] != row[3]:
        raise ValueError("original returned digest changed")
    if row[5] == 0:
        _stale_terminal(fd, chain, pending, row[1], row[3], returned[0], row[4])
        return row, None
    completed = _completed_index(fd, chain, pending["config"], pending["requested_date"])
    if completed is None or completed[1] != row[4]:
        raise ValueError("original terminal index unavailable")
    return row, completed


def _report_completed(config, row, completed):
    pending, pending_sha, _, returned_sha, terminal_sha, _ = row
    terminal = completed[0]
    hashes = {
        "original_config_sha256": pending["config_sha256"],
        "current_config_sha256": _digest(config),
        "original_scopes_sha256": pending["scopes_sha256"],
        "current_scopes_sha256": _digest(config["scopes"]),
    }
    result = {**_pending_result(pending), **hashes}
    try:
        fds, chain = runs._open_runs(pending["root"], create=False)
        try:
            returned = _returned(fds[-1], chain, pending, pending_sha)
        finally:
            runs._close(fds)
        if returned is None or returned[1] != returned_sha:
            raise ValueError("returned evidence unavailable")
        proof = _batch_proof(pending, returned[0], allow_review=False)
        prior = terminal["proof"]
        keys = set(prior) - {"pointer_sha256"}
        if (
            proof["kind"] != terminal["outcome"]
            or any(proof.get(key) != prior[key] for key in keys)
            or (proof["kind"] == "SAME" and proof["pointer_sha256"] != prior["pointer_sha256"])
        ):
            raise ValueError("original terminal proof changed")
        # Keep the Store lock outermost until the ledger context has exited.
        with _store_lock(pending["root"]) as (store, fd, chain, _):  # noqa: SIM117
            with _ledger(pending["root"], fd, chain) as db:
                active, _ = _ledger_state(db, pending["root"])
                if active is not None or _pending_locked(fd, chain, pending["root"]) is not None:
                    raise ValueError("another operation pending")
                latest_row, latest_terminal = _verify_terminal_files(
                    db, fd, chain, pending["root"], pending["operation_id"]
                )
                if latest_row != row or latest_terminal != completed:
                    raise ValueError("original terminal changed")
                _verify_locked(store, pending, returned[0], proof)
        changed = hashes["original_config_sha256"] != hashes["current_config_sha256"]
        return _with_scope_report({
            **result,
            "status": "CONFIG_CHANGED_ORIGINAL_COMPLETED" if changed else "ALREADY_COMPLETED",
            "reason": "ORIGINAL_EVIDENCE_REVALIDATED",
            "original_outcome": terminal["outcome"],
            "batch_status": terminal["batch_status"],
            "evidence_revalidation": "LOCAL_REVALIDATED_UNAUTHENTICATED",
            "terminal_sha256": terminal_sha,
        }, pending, returned[0])
    except Exception:
        return {
            **result,
            "status": "UNKNOWN",
            "reason": "ORIGINAL_TERMINAL_EVIDENCE_UNAVAILABLE",
            "evidence_revalidation": "NOT_REVALIDATED",
        }


def run_once(*, config_file, filesystem):
    """Start at most one batch after a durable singleton intent is visible."""
    if not _SUPPORTED_PLATFORM or filesystem != FS:
        return {**LABEL, "status": "NOT_COMMITTED", "reason": "UNSUPPORTED_FILESYSTEM"}
    pending = None
    try:
        config, raw_config_sha = _read_config(config_file)
        root = config["root"]
        existing = completed = None
        with _store_lock(root) as (store, fd, chain, check):
            fence = _read(fd, chain, _INIT_FENCE)
            if fence is not None:
                pending = _validate_pending(fence[0], root)
                raise ValueError("initialization requires original operation recovery")
            first = (
                _read(fd, chain, _LEDGER_MARKER) is None and _ledger_file(fd, _LEDGER_FILE) is None
            )
            if first:
                current = _pending_locked(fd, chain, root)
                if current is not None:
                    pending = current[0]
                    raise ValueError("legacy original pending requires migration")
                _scan_legacy_once(fd, chain)
                reread_config, reread_sha = _read_config(config_file)
                if reread_config != config or reread_sha != raw_config_sha:
                    raise ValueError("schedule config changed")
                operator, _, _ = _operator_locked(root, config["original_run_id"], store)
                instant = _now_utc()
                if (
                    type(instant) is not datetime
                    or instant.tzinfo is None
                    or instant.utcoffset() != UTC.utcoffset(instant)
                ):
                    raise ValueError("clock declaration unavailable")
                instant = instant.astimezone(UTC)
                requested_date = instant.astimezone(ZoneInfo("Asia/Shanghai")).date()
                operation_id, identities = _allocate(config)
                _validate_scopes(config, operator, requested_date, identities)
                pending = {
                    "schema": _PENDING,
                    **LABEL,
                    "operation_id": operation_id,
                    "job_key": config["job_key"],
                    "root": root,
                    "original_run_id": config["original_run_id"],
                    "config": config,
                    "config_sha256": _digest(config),
                    "config_file_sha256": raw_config_sha,
                    "scopes_sha256": _digest(config["scopes"]),
                    "utc_instant": instant.isoformat(timespec="microseconds").replace(
                        "+00:00", "Z"
                    ),
                    "requested_date": requested_date.isoformat(),
                    "identity_bundle": identities,
                }
                _validate_pending(pending, root)
                pending_sha = _write(fd, chain, _INIT_FENCE, pending)
                check()
            with _ledger(
                root, fd, chain, initialize=True, initial_fence=pending if first else None
            ) as db:
                active, last = _ledger_state(db, root)
                if first:
                    if active is not None or last is not None:
                        raise ValueError("initial ledger not empty")
                    _ledger_insert(db, pending, pending_sha)
                    if (
                        _ledger_row(db, operation_id, root)
                        != (pending, pending_sha, "ACTIVE", None, None, 1)
                        or _ledger_state(db, root)[0] != operation_id
                    ):
                        raise ValueError("initial ledger claim readback changed")
                    if (
                        _write(fd, chain, _PENDING_FILE, pending) != pending_sha
                        or _write(fd, chain, _name("intent", operation_id), pending) != pending_sha
                    ):
                        raise ValueError("initial intent mismatch")
                    _release_initial_fence(fd, chain, check)
                else:
                    if active is not None:
                        row = _ledger_row(db, active, root)
                        pending = row[0]
                        if (
                            _pending_locked(fd, chain, root) != row[:2]
                            or _read_exact(fd, chain, _name("intent", active), pending) != row[1]
                        ):
                            raise ValueError("original pending unavailable")
                        returned = _returned(fd, chain, pending, row[1])
                        if (row[2] == "ACTIVE" and returned is not None) or (
                            row[2] == "RETURNED" and (returned is None or returned[1] != row[3])
                        ):
                            raise ValueError("returned transition uncertain")
                        existing = (pending, row[1], returned)
                        if row[2] == "RETURNED" and returned[0]["status"] == "PENDING_REVIEW":
                            # A policy change invalidates the pending review without publishing it.
                            try:
                                divergence = _review_policy_divergence_locked(
                                    store, pending, returned[0]
                                )
                            except Exception:
                                divergence = None
                            if divergence is not None and divergence[0]:
                                _close_stale_review_locked(
                                    store,
                                    db,
                                    fd,
                                    chain,
                                    check,
                                    pending,
                                    row[1],
                                    returned[0],
                                    returned[1],
                                    divergence[1],
                                    divergence[2],
                                )
                                existing = pending = None
                                active, last = _ledger_state(db, root)
                    if existing is None:
                        current = _pending_locked(fd, chain, root)
                        if current is not None and last is not None:
                            stale_row = _ledger_row(db, last, root)
                            if stale_row[5] == 0 and stale_row[:2] == current:
                                # complete a stale closure interrupted before pending release
                                _release_pending(fd, chain, check)
                                current = None
                        if current is not None:
                            pending = current[0]
                            raise ValueError("terminal pending release not proven")
                        if last is not None:
                            pending = _ledger_row(db, last, root)[0]
                            _verify_terminal_files(db, fd, chain, root, last)
                            pending = None
                        reread_config, reread_sha = _read_config(config_file)
                        if reread_config != config or reread_sha != raw_config_sha:
                            raise ValueError("schedule config changed")
                        instant = _now_utc()
                        if (
                            type(instant) is not datetime
                            or instant.tzinfo is None
                            or instant.utcoffset() != UTC.utcoffset(instant)
                        ):
                            raise ValueError("clock declaration unavailable")
                        instant = instant.astimezone(UTC)
                        requested_date = instant.astimezone(ZoneInfo("Asia/Shanghai")).date()
                        day = requested_date.isoformat()
                        found = db.execute(
                            "SELECT operation_id FROM operations WHERE job_key=? "
                            "AND requested_date=? AND effective=1",
                            (config["job_key"], day),
                        ).fetchone()
                        if found is not None:
                            pending = _ledger_row(db, found[0], root)[0]
                            completed = _verify_terminal_files(db, fd, chain, root, found[0])
                        else:
                            if _completed_index(fd, chain, config, day) is not None:
                                raise ValueError("orphan date index")
                            operator, _, _ = _operator_locked(
                                root, config["original_run_id"], store
                            )
                            operation_id, identities = _allocate(config)
                            _validate_scopes(config, operator, requested_date, identities)
                            pending = {
                                "schema": _PENDING,
                                **LABEL,
                                "operation_id": operation_id,
                                "job_key": config["job_key"],
                                "root": root,
                                "original_run_id": config["original_run_id"],
                                "config": config,
                                "config_sha256": _digest(config),
                                "config_file_sha256": raw_config_sha,
                                "scopes_sha256": _digest(config["scopes"]),
                                "utc_instant": instant.isoformat(timespec="microseconds").replace(
                                    "+00:00", "Z"
                                ),
                                "requested_date": day,
                                "identity_bundle": identities,
                            }
                            _validate_pending(pending, root)
                            pending_sha = storage._unwrap(storage._envelope(pending))[1]
                            check()
                            _ledger_insert(db, pending, pending_sha)
                            if (
                                _ledger_row(db, operation_id, root)
                                != (pending, pending_sha, "ACTIVE", None, None, 1)
                                or _ledger_state(db, root)[0] != operation_id
                            ):
                                raise ValueError("ledger claim readback changed")
                            if _write(fd, chain, _PENDING_FILE, pending) != pending_sha:
                                raise ValueError("singleton intent mismatch")
                            if (
                                _write(fd, chain, _name("intent", operation_id), pending)
                                != pending_sha
                            ):
                                raise ValueError("retained intent mismatch")
                            check()
        if existing is not None:
            return _report_pending(*existing)
        if completed is not None:
            return _report_completed(config, *completed)
        try:
            result = batch.run(
                root=root,
                run_id=config["original_run_id"],
                scopes=config["scopes"],
                supplement=config.get("supplement"),
                budget_seconds=config["budget_seconds"],
                identity_bundle=identities,
                requested_date=requested_date,
            )
        except BaseException as exc:
            if not isinstance(exc, Exception):
                with contextlib.suppress(Exception):
                    exc.operation_id = pending["operation_id"]
                    exc.identity_bundle = pending["identity_bundle"]
                raise
            return _pending_result(pending, reason="BATCH_OUTCOME_UNKNOWN")
        returned_body = _returned_from_result(pending, pending_sha, result)
        # Keep the Store lock outermost until the ledger context has exited.
        with _store_lock(root) as (_, fd, chain, check):  # noqa: SIM117
            with _ledger(root, fd, chain) as db:
                if (
                    _ledger_state(db, root)[0] != pending["operation_id"]
                    or _ledger_row(db, pending["operation_id"], root)
                    != (pending, pending_sha, "ACTIVE", None, None, 1)
                    or _pending_locked(fd, chain, root) != (pending, pending_sha)
                    or _read_exact(fd, chain, _name("intent", pending["operation_id"]), pending)
                    != pending_sha
                ):
                    raise ValueError("pending changed after batch")
                returned_sha = _write(
                    fd, chain, _name("returned", pending["operation_id"]), returned_body
                )
                _ledger_returned(db, pending["operation_id"], returned_sha)
                if _ledger_row(db, pending["operation_id"], root) != (
                    pending,
                    pending_sha,
                    "RETURNED",
                    returned_sha,
                    None,
                    1,
                ):
                    raise ValueError("returned transition readback changed")
                check()
        if returned_body["status"] == "PENDING_REVIEW":
            return _report_pending(pending, pending_sha, (returned_body, returned_sha))
        if returned_body["status"] not in {"COMMITTED", "SAME"}:
            return _pending_result(pending, reason="BATCH_OUTCOME_UNKNOWN")
        return _finish(pending, pending_sha, returned_body, returned_sha)
    except (KeyboardInterrupt, SystemExit) as exc:
        if pending is not None:
            with contextlib.suppress(Exception):
                exc.operation_id = pending["operation_id"]
                exc.identity_bundle = pending["identity_bundle"]
        raise
    except Exception:
        if pending is not None:
            return _pending_result(pending, reason="ORIGINAL_OPERATION_UNKNOWN")
        return {**LABEL, "status": "UNKNOWN", "reason": "SCHEDULE_INPUT_OR_STATE_UNAVAILABLE"}


def inspect(*, root, operation_id, filesystem):
    """Read the original operation; never create, clear or retry it."""
    if not _SUPPORTED_PLATFORM:
        return {**LABEL, "status": "UNKNOWN", "reason": "UNSUPPORTED_FILESYSTEM"}
    if filesystem != FS or not runs._id(operation_id):
        return {**LABEL, "status": "UNKNOWN", "reason": "INVALID_INPUT"}
    pending = None
    try:
        with _store_lock(root, readonly=True) as (_, fd, chain, _):
            fence = _read(fd, chain, _INIT_FENCE)
            if fence is not None:
                pending = _validate_pending(fence[0], root)
                if pending["operation_id"] != operation_id:
                    pending = None
                raise ValueError("initialization requires original operation recovery")
            with _ledger(root, fd, chain, readonly=True) as db:
                active, _ = _ledger_state(db, root)
                row = _ledger_row(db, operation_id, root)
                pending, pending_sha = row[:2]
                if row[2] == "TERMINAL":
                    if row[5] == 0:
                        returned = _returned(fd, chain, pending, pending_sha)
                        if returned is None or returned[1] != row[3]:
                            raise ValueError("original returned digest changed")
                        terminal = _stale_terminal(
                            fd, chain, pending, pending_sha, row[3], returned[0], row[4]
                        )
                        return {
                            **_pending_result(
                                pending,
                                status="LOCAL_HISTORICAL_RECORD_ONLY",
                                reason="HISTORICAL_EVIDENCE_NOT_REVALIDATED",
                            ),
                            "terminal": terminal[0],
                            "evidence_revalidation": "NOT_REVALIDATED",
                            "release": "NOT_PROVEN_BY_READBACK",
                            "effective": False,
                            "date_completed": False,
                        }
                    _, terminal = _verify_terminal_files(db, fd, chain, root, operation_id)
                    return {
                        **_pending_result(
                            pending,
                            status="LOCAL_HISTORICAL_RECORD_ONLY",
                            reason="HISTORICAL_EVIDENCE_NOT_REVALIDATED",
                        ),
                        "terminal": terminal[0],
                        "evidence_revalidation": "NOT_REVALIDATED",
                        "release": "NOT_PROVEN_BY_READBACK",
                    }
                if active != operation_id or _pending_locked(fd, chain, root) != row[:2]:
                    raise ValueError("original pending unavailable")
                if _read_exact(fd, chain, _name("intent", operation_id), pending) != pending_sha:
                    raise ValueError("original intent unavailable")
                returned = _returned(fd, chain, pending, pending_sha)
                if (row[2] == "ACTIVE" and returned is not None) or (
                    row[2] == "RETURNED" and (returned is None or returned[1] != row[3])
                ):
                    raise ValueError("returned transition uncertain")
        return _report_pending(pending, pending_sha, returned)
    except Exception:
        if pending is not None:
            return _pending_result(pending, reason="ORIGINAL_OPERATION_UNAVAILABLE")
        return {
            **LABEL,
            "status": "UNKNOWN",
            "reason": "ORIGINAL_OPERATION_UNAVAILABLE",
            "operation_id": operation_id,
        }


def inspect_active(*, root, filesystem):
    """Read the single schedule-owned pending review, without authorizing notification.

    Revalidate policy, supplement and CURRENT before any notification. State may
    change immediately after the read lock is released.
    """
    if not _SUPPORTED_PLATFORM:
        return {**LABEL, "status": "UNKNOWN", "reason": "UNSUPPORTED_FILESYSTEM"}
    if filesystem != FS:
        return {**LABEL, "status": "UNKNOWN", "reason": "INVALID_INPUT"}
    pending = None
    original_operation_id = None

    def snapshot(*, expected_proof=None):
        nonlocal pending
        with _store_lock(root, readonly=True) as (store, fd, chain, _):
            fence = _read(fd, chain, _INIT_FENCE)
            if fence is not None:
                pending = _validate_pending(fence[0], root)
                raise ValueError("initialization requires original operation recovery")
            with _ledger(root, fd, chain, readonly=True) as db:
                active, last = _ledger_state(db, root)
                if active is None:
                    if last is None:
                        raise ValueError("ledger terminal state unavailable")
                    current = _pending_locked(fd, chain, root)
                    if current is not None:
                        pending = current[0]
                        raise ValueError("singleton pending without active operation")
                    if last is not None:
                        pending = _ledger_row(db, last, root)[0]
                        _verify_terminal_files(db, fd, chain, root, last)
                        pending = None
                    return None
                row = _ledger_row(db, active, root)
                pending, pending_sha = row[:2]
                if row[2] != "RETURNED" or _pending_locked(fd, chain, root) != row[:2]:
                    raise ValueError("active operation has no verified returned result")
                if _read_exact(fd, chain, _name("intent", active), pending) != pending_sha:
                    raise ValueError("active intent unavailable")
                returned = _returned(fd, chain, pending, pending_sha)
                if (
                    returned is None
                    or returned[1] != row[3]
                    or returned[0]["status"] != "PENDING_REVIEW"
                ):
                    raise ValueError("active review result unavailable")
                policy_changed = False
                if expected_proof is not None:
                    if not _review_not_started(root, pending["identity_bundle"]["review_id"]):
                        raise ValueError("original review already started")
                    if (
                        _batch_proof(pending, returned[0], allow_review=True, locked=(fd, chain))
                        != expected_proof
                    ):
                        raise ValueError("original review proof changed")
                    policy_changed = _check_review_policy_locked(store, pending, returned[0])
                return row, returned, policy_changed

    try:
        original = snapshot()
        if original is None:
            return {
                **LABEL,
                "status": "NONE",
                "reason": "NO_ACTIVE_OPERATION",
                "state_freshness": "NOT_GUARANTEED_AFTER_LOCK_RELEASE",
            }
        row, returned, _ = original
        original_operation_id = row[0]["operation_id"]

        def review_started():
            return {
                **LABEL,
                "status": "UNKNOWN",
                "reason": "ORIGINAL_REVIEW_STARTED_OR_COMPLETED",
                "operation_id": row[0]["operation_id"],
                "identity_bundle": row[0]["identity_bundle"],
                "notification_authorization": "NOT_PROVEN",
                "state_freshness": "NOT_GUARANTEED_AFTER_LOCK_RELEASE",
            }

        try:
            proof = _batch_proof(row[0], returned[0], allow_review=True)
        except ValueError:
            if not _review_not_started(root, row[0]["identity_bundle"]["review_id"]):
                return review_started()
            raise
        if (
            proof.get("kind") != "PENDING_REVIEW"
            or proof.get("review_payload_sha256") != returned[0]["review_payload_sha256"]
        ):
            raise ValueError("original review evidence unavailable")
        verified = None
        try:
            verified = snapshot(expected_proof=proof)
        except ValueError as exc:
            if str(exc) != "original review already started":
                raise
            return review_started()
        if verified is None or verified[:2] != original[:2]:
            raise ValueError("original active operation changed")
        if verified[2]:
            return {
                **LABEL,
                "status": "UNKNOWN",
                "reason": "POLICY_CHANGED_REQUIRES_FRESH_UPDATE",
                "operation_id": row[0]["operation_id"],
                "identity_bundle": row[0]["identity_bundle"],
                "state_freshness": "NOT_GUARANTEED_AFTER_LOCK_RELEASE",
            }
        ids = row[0]["identity_bundle"]
        return {
            **LABEL,
            "status": "PENDING_REVIEW",
            "reason": "ORIGINAL_REVIEW_VISIBLE",
            "operation_id": row[0]["operation_id"],
            "run_id": ids["run_id"],
            "review_id": ids["review_id"],
            "review_payload_sha256": returned[0]["review_payload_sha256"],
            "notification_preconditions": "REVALIDATE_POLICY_SUPPLEMENT_CURRENT",
            "state_freshness": "NOT_GUARANTEED_AFTER_LOCK_RELEASE",
            "notification_authorization": "NOT_PROVEN",
        }
    except Exception:
        result = {**LABEL, "status": "UNKNOWN", "reason": "ACTIVE_OPERATION_UNAVAILABLE"}
        operation_id = original_operation_id or (pending["operation_id"] if pending else None)
        if operation_id is not None:
            result["operation_id"] = operation_id
        if pending is not None:
            result["identity_bundle"] = pending["identity_bundle"]
        return result


def reconcile(*, root, operation_id, filesystem):
    """Explicitly finish the same returned operation only with complete evidence."""
    if not _SUPPORTED_PLATFORM:
        return {**LABEL, "status": "UNKNOWN", "reason": "UNSUPPORTED_FILESYSTEM"}
    if filesystem != FS or not runs._id(operation_id):
        return {**LABEL, "status": "UNKNOWN", "reason": "INVALID_INPUT"}
    pending = None
    try:
        with _store_lock(root) as (_, fd, chain, check):
            fence = _read(fd, chain, _INIT_FENCE)
            if fence is not None:
                pending = _validate_pending(fence[0], root)
                if pending["operation_id"] != operation_id:
                    pending = None
                raise ValueError("initialization requires original operation recovery")
            with _ledger(root, fd, chain) as db:
                active, last = _ledger_state(db, root)
                row = _ledger_row(db, operation_id, root)
                pending, pending_sha = row[:2]
                if row[2] == "TERMINAL" and row[5] == 0:
                    returned = _returned(fd, chain, pending, pending_sha)
                    if returned is None or returned[1] != row[3]:
                        raise ValueError("returned evidence unavailable")
                    _stale_terminal(
                        fd, chain, pending, pending_sha, row[3], returned[0], row[4]
                    )
                    if (
                        _read_exact(fd, chain, _name("intent", operation_id), pending)
                        != pending_sha
                    ):
                        raise ValueError("original intent unavailable")
                    if active is not None:
                        raise ValueError("another operation active")
                    if _pending_locked(fd, chain, root) == row[:2]:
                        _release_pending(fd, chain, check)
                    return _pending_result(
                        pending, status="UNKNOWN", reason="NON_EFFECTIVE_REVIEW_CLOSED"
                    )
                if (
                    _pending_locked(fd, chain, root) != row[:2]
                    or _read_exact(fd, chain, _name("intent", operation_id), pending) != pending_sha
                    or not (
                        active == operation_id or (row[2] == "TERMINAL" and last == operation_id)
                    )
                ):
                    raise ValueError("original pending unavailable")
                returned = _returned(fd, chain, pending, pending_sha)
                if returned is None:
                    raise ValueError("returned evidence unavailable")
                if row[2] == "ACTIVE":
                    _ledger_returned(db, operation_id, returned[1])
                elif returned[1] != row[3]:
                    raise ValueError("returned evidence changed")
        if returned[0]["status"] not in {"COMMITTED", "SAME", "PENDING_REVIEW"}:
            return _pending_result(pending, reason="RETURNED_EVIDENCE_UNAVAILABLE")
        try:
            return _finish(pending, pending_sha, returned[0], returned[1])
        except Exception:
            return _report_pending(pending, pending_sha, returned)
    except Exception:
        if pending is not None:
            return _pending_result(pending, reason="ORIGINAL_OPERATION_UNAVAILABLE")
        return {
            **LABEL,
            "status": "UNKNOWN",
            "reason": "ORIGINAL_OPERATION_UNAVAILABLE",
            "operation_id": operation_id,
        }


def scan_publications(*, root, filesystem, limit=_PUBLICATION_SCAN_LIMIT):
    """Read-only enumeration of effective terminal COMMITTED publications.

    ``SAME`` operations are excluded because no new summary was produced. The
    scan is bounded; reaching the bound or failing to revalidate any row is
    reported as INCOMPLETE so missed notifications are never silently dropped.
    """
    if not _SUPPORTED_PLATFORM:
        return {**LABEL, "status": "UNKNOWN", "reason": "UNSUPPORTED_FILESYSTEM"}
    if (
        filesystem != FS
        or type(limit) is not int
        or not 1 <= limit <= 256
    ):
        return {**LABEL, "status": "UNKNOWN", "reason": "INVALID_INPUT"}
    operations = []
    unverified = 0
    truncated = False
    try:
        with _store_lock(root, readonly=True) as (_, fd, chain, _):  # noqa: SIM117
            with _ledger(root, fd, chain, readonly=True) as db:
                rows = db.execute(
                    "SELECT operation_id FROM operations WHERE state='TERMINAL' "
                    "AND effective=1 ORDER BY requested_date, operation_id LIMIT ?",
                    (limit + 1,),
                ).fetchall()
                if len(rows) > limit:
                    truncated = True
                    rows = rows[:limit]
                for (operation_id,) in rows:
                    try:
                        row = _ledger_row(db, operation_id, root)
                        if row[2] != "TERMINAL" or row[5] != 1:
                            raise ValueError("publication not effective terminal")
                        _, completed = _verify_terminal_files(
                            db, fd, chain, root, operation_id
                        )
                        if completed is None:
                            raise ValueError("publication has no date index")
                        terminal = completed[0]
                        if terminal["outcome"] != "COMMITTED":
                            continue
                        ids = row[0]["identity_bundle"]
                        operations.append(
                            {
                                "operation_id": operation_id,
                                "requested_date": row[0]["requested_date"],
                                "run_id": ids["run_id"],
                                "candidate_id": ids["candidate_id"],
                                "publication_id": ids["publication_id"],
                                "terminal_sha256": row[4],
                                "batch_status": terminal["batch_status"],
                            }
                        )
                    except Exception:
                        unverified += 1
    except Exception:
        return {**LABEL, "status": "UNKNOWN", "reason": "PUBLICATION_SCAN_UNAVAILABLE"}
    complete = not truncated and unverified == 0
    return {
        **LABEL,
        "status": "VISIBLE_UNAUTHENTICATED" if complete else "INCOMPLETE",
        "reason": "PUBLICATION_SCAN_COMPLETE"
        if complete
        else "PUBLICATION_SCAN_LIMIT_REACHED"
        if truncated
        else "PUBLICATION_EVIDENCE_UNAVAILABLE",
        "operations": operations,
        "unverified": unverified,
        "scan_limit": limit,
        "state_freshness": "NOT_GUARANTEED_AFTER_LOCK_RELEASE",
    }


def _store_trading_days(content, venue):
    """Return ordered open days for one venue from the store snapshot only."""
    layer = content.get("trading_days") if type(content) is dict else None
    if (
        type(layer) is not list
        or not layer
        or len(layer) > _HEALTH_MAX_TRADING_DAYS
    ):
        raise ValueError("unsupported trading-day layer")
    days = []
    seen = set()
    for row in layer:
        if (
            type(row) is not dict
            or set(row) != {"venue", "day", "status", "source_ids"}
            or row["venue"] not in _HEALTH_VENUES
            or row["status"] not in {"open", "closed"}
            or type(row["day"]) is not str
        ):
            raise ValueError("unsupported trading-day row")
        try:
            day = date.fromisoformat(row["day"])
        except ValueError:
            raise ValueError("invalid trading day") from None
        if day.isoformat() != row["day"] or (row["venue"], row["day"]) in seen:
            raise ValueError("invalid trading day")
        seen.add((row["venue"], row["day"]))
        if row["venue"] == venue and row["status"] == "open":
            days.append(day)
    return tuple(sorted(days))


def _health_operation(db, fd, chain, root, operation_id):
    """Classify one effective operation as a successful or failed update check."""
    row = _ledger_row(db, operation_id, root)
    if row[2] != "TERMINAL":
        return "NO_SUCCESS", "CHECK_NOT_TERMINAL", {"operation_state": row[2]}
    if row[5] != 1:
        return "NO_SUCCESS", "NON_EFFECTIVE_OPERATION", {"operation_state": row[2]}
    _, completed = _verify_terminal_files(db, fd, chain, root, operation_id)
    if completed is None:
        return "NO_SUCCESS", "NON_EFFECTIVE_OPERATION", {"operation_state": row[2]}
    terminal = completed[0]
    detail = {
        "operation_state": row[2],
        "outcome": terminal["outcome"],
        "batch_status": terminal["batch_status"],
    }
    if (
        terminal["outcome"] in _HEALTH_SUCCESS_OUTCOMES
        and terminal["batch_status"] == "COMPLETE"
    ):
        return "SUCCESS", "CHECK_SUCCEEDED", detail
    if terminal["batch_status"] == "PARTIAL":
        return "NO_SUCCESS", "PARTIAL_CHECK", detail
    return "NO_SUCCESS", "CHECK_NO_SUCCESS", detail


def _health_day(db, fd, chain, root, day):
    listed = db.execute(
        "SELECT operation_id FROM operations WHERE requested_date=? AND effective=1 "
        "ORDER BY operation_id",
        (day.isoformat(),),
    ).fetchall()
    if not listed:
        return {
            "day": day.isoformat(),
            "status": "NO_SUCCESS",
            "reason_codes": ["NO_CHECK_RECORD"],
            "operations": [],
        }
    statuses = []
    reasons = []
    operations = []
    for (operation_id,) in listed:
        try:
            status, reason, detail = _health_operation(db, fd, chain, root, operation_id)
        except Exception:
            status, reason, detail = "UNKNOWN", "CHECK_EVIDENCE_UNAVAILABLE", {}
        statuses.append(status)
        reasons.append(reason)
        operations.append({"operation_id": operation_id, "status": status, **detail})
    if "SUCCESS" in statuses:
        status, reason_codes = "SUCCESS", ["CHECK_SUCCEEDED"]
    elif "UNKNOWN" in statuses:
        status, reason_codes = "UNKNOWN", sorted(set(reasons))
    else:
        status, reason_codes = "NO_SUCCESS", sorted(set(reasons))
    return {
        "day": day.isoformat(),
        "status": status,
        "reason_codes": reason_codes,
        "operations": operations,
    }


def inspect_check_health(
    *, root, venue, as_of_date=None, filesystem, threshold=_HEALTH_THRESHOLD
):
    """Report whether the last two trading days lacked a successful update check.

    Trading days come from the calendar store's CURRENT snapshot, not wall-clock
    weekdays; the caller-declared date or the unverified local clock bounds the
    window. Read-only, unauthenticated local evidence; a warning is not a
    trading prohibition.
    """
    if not _SUPPORTED_PLATFORM:
        return {**LABEL, "status": "UNKNOWN", "reason": "UNSUPPORTED_FILESYSTEM"}
    if (
        filesystem != FS
        or venue not in _HEALTH_VENUES
        or threshold != _HEALTH_THRESHOLD
        or (as_of_date is not None and type(as_of_date) is not date)
    ):
        return {**LABEL, "status": "UNKNOWN", "reason": "INVALID_INPUT"}
    as_of_source = "CALLER_DECLARED"
    try:
        if as_of_date is None:
            instant = _now_utc()
            if (
                type(instant) is not datetime
                or instant.tzinfo is None
                or instant.utcoffset() != UTC.utcoffset(instant)
            ):
                raise ValueError("clock declaration unavailable")
            as_of_date = instant.astimezone(ZoneInfo("Asia/Shanghai")).date()
            as_of_source = "LOCAL_CLOCK_UNVERIFIED"
        current = TradingCalendarStore(root).read_current()
        trading_days = _store_trading_days(current.content, venue)
    except Exception:
        return {
            **LABEL,
            "status": "UNKNOWN",
            "reason": "CHECK_HISTORY_UNAVAILABLE",
            "venue": venue,
            "as_of_date": as_of_date.isoformat() if type(as_of_date) is date else None,
        }
    window = tuple(day for day in trading_days if day <= as_of_date)
    if len(window) < threshold:
        return {
            **LABEL,
            "status": "UNKNOWN",
            "reason": "INSUFFICIENT_TRADING_DAYS",
            "venue": venue,
            "as_of_date": as_of_date.isoformat(),
            "as_of_source": as_of_source,
            "threshold": threshold,
            "trading_days": [day.isoformat() for day in window],
            "days": [],
        }
    window = window[-threshold:]
    try:
        with _store_lock(root, readonly=True) as (_, fd, chain, _):  # noqa: SIM117
            with _ledger(root, fd, chain, readonly=True) as db:
                days = [_health_day(db, fd, chain, root, day) for day in window]
    except Exception:
        return {
            **LABEL,
            "status": "UNKNOWN",
            "reason": "CHECK_HISTORY_UNAVAILABLE",
            "venue": venue,
            "as_of_date": as_of_date.isoformat(),
            "as_of_source": as_of_source,
            "threshold": threshold,
            "trading_days": [day.isoformat() for day in window],
            "days": [],
        }
    if any(row["status"] == "UNKNOWN" for row in days):
        status, reason = "UNKNOWN", "CHECK_HISTORY_UNCERTAIN"
    elif all(row["status"] == "NO_SUCCESS" for row in days):
        status, reason = "WARNING", "TWO_CONSECUTIVE_TRADING_DAYS_NO_SUCCESS"
    else:
        status, reason = "OK", "WITHIN_THRESHOLD"
    return {
        **LABEL,
        "status": status,
        "reason": reason,
        "venue": venue,
        "as_of_date": as_of_date.isoformat(),
        "as_of_source": as_of_source,
        "threshold": threshold,
        "trading_days": [day.isoformat() for day in window],
        "days": days,
        "state_freshness": "NOT_GUARANTEED_AFTER_LOCK_RELEASE",
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments")


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument(
        "--action",
        choices=(
            "run",
            "inspect",
            "inspect-active",
            "reconcile",
            "scan-publications",
            "check-health",
        ),
        default="run",
    )
    parser.add_argument("--config")
    parser.add_argument("--root")
    parser.add_argument("--operation-id")
    parser.add_argument("--venue")
    parser.add_argument("--as-of-date")
    parser.add_argument("--trusted-local-posix", action="store_true", required=True)
    try:
        args = parser.parse_args(argv)
        if args.action == "run":
            if (
                not args.config
                or args.root
                or args.operation_id
                or args.venue
                or args.as_of_date
            ):
                parser.error("invalid arguments")
        elif args.action in ("inspect-active", "scan-publications"):
            if (
                not args.root
                or args.config is not None
                or args.operation_id is not None
                or args.venue
                or args.as_of_date
            ):
                parser.error("invalid arguments")
        elif args.action == "check-health":
            if (
                not args.root
                or args.venue not in _HEALTH_VENUES
                or args.config is not None
                or args.operation_id is not None
            ):
                parser.error("invalid arguments")
            if args.as_of_date is not None:
                try:
                    parsed = date.fromisoformat(args.as_of_date)
                except ValueError:
                    parser.error("invalid arguments")
                if parsed.isoformat() != args.as_of_date:
                    parser.error("invalid arguments")
        elif (
            args.config
            or not args.root
            or not args.operation_id
            or args.venue
            or args.as_of_date
        ):
            parser.error("invalid arguments")
    except ValueError:
        parser.exit(2, "invalid arguments\n")
    try:
        if args.action == "run":
            result = run_once(config_file=args.config, filesystem=FS)
        elif args.action == "inspect-active":
            result = inspect_active(root=args.root, filesystem=FS)
        elif args.action == "scan-publications":
            result = scan_publications(root=args.root, filesystem=FS)
        elif args.action == "check-health":
            result = inspect_check_health(
                root=args.root,
                venue=args.venue,
                as_of_date=(
                    None if args.as_of_date is None else date.fromisoformat(args.as_of_date)
                ),
                filesystem=FS,
            )
        else:
            operation = inspect if args.action == "inspect" else reconcile
            result = operation(root=args.root, operation_id=args.operation_id, filesystem=FS)
    except KeyboardInterrupt as exc:
        outcome = getattr(exc, "calendar_outcome", None)
        if type(outcome) is not dict or outcome.get("reason") != "SCOPE_REPORT_INTERRUPTED":
            raise
        print(json.dumps(outcome, sort_keys=True, ensure_ascii=True), flush=True)
        return 130
    print(json.dumps(result, sort_keys=True, ensure_ascii=True), flush=True)
    if args.action == "scan-publications":
        return 0 if result.get("status") == "VISIBLE_UNAUTHENTICATED" else (
            10 if result.get("status") == "INCOMPLETE" else 25
        )
    if args.action == "check-health":
        return {"OK": 0, "WARNING": 10}.get(result.get("status"), 25)
    return (
        0
        if result.get("status")
        in {"COMMITTED", "SAME", "ALREADY_COMPLETED", "LOCAL_HISTORICAL_RECORD_ONLY"}
        or (args.action == "inspect-active" and result.get("status") == "NONE")
        else 10
        if result.get("status") == "PENDING_REVIEW"
        else 25
    )


if __name__ == "__main__":
    raise SystemExit(main())
