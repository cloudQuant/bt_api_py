"""SQLite primitives for declared, isolated calendar simulation scope indexes.

All callers must hold the calendar store's coordinating lock. SQLite is a local
consistency aid: neither its records nor a maintenance declaration authenticate
source data or defend against a whole-directory rollback.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_observation_run as runs

_DECLARATION = "OLD_WRITERS_STOPPED_DECLARED_NOT_VERIFIED"
_SCHEMA = "bt_api.calendar-scope-sqlite/1"
_MAX_PAYLOAD = 65536
_DDL = (
    "CREATE TABLE metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema TEXT NOT NULL, database_id TEXT NOT NULL, migration_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence>=0), head_sha256 TEXT)",
    "CREATE TABLE records (sequence INTEGER PRIMARY KEY CHECK(sequence>0), batch_id TEXT NOT NULL UNIQUE, record_sha256 TEXT NOT NULL, previous_sha256 TEXT, payload BLOB NOT NULL, legacy INTEGER NOT NULL CHECK(legacy IN (0,1)))",
    "CREATE TABLE scope_success (scope_key TEXT NOT NULL, sequence INTEGER NOT NULL, entry_index INTEGER NOT NULL CHECK(entry_index>=0), PRIMARY KEY(scope_key,sequence), UNIQUE(sequence,entry_index), FOREIGN KEY(sequence) REFERENCES records(sequence))",
    "CREATE TABLE audits (audit_id TEXT PRIMARY KEY, head_sequence INTEGER NOT NULL, head_sha256 TEXT)",
    "CREATE TABLE audit_pages (audit_id TEXT NOT NULL, page_number INTEGER NOT NULL, start_sequence INTEGER NOT NULL, next_sequence INTEGER NOT NULL, previous_sha256 TEXT, tail_sha256 TEXT, PRIMARY KEY(audit_id,page_number), FOREIGN KEY(audit_id) REFERENCES audits(audit_id))",
    "CREATE TRIGGER records_no_update BEFORE UPDATE ON records BEGIN SELECT RAISE(ABORT,'immutable record'); END",
    "CREATE TRIGGER records_no_delete BEFORE DELETE ON records BEGIN SELECT RAISE(ABORT,'immutable record'); END",
    "CREATE TRIGGER metadata_no_delete BEFORE DELETE ON metadata BEGIN SELECT RAISE(ABORT,'immutable metadata'); END",
    "CREATE TRIGGER records_insert_guard BEFORE INSERT ON records BEGIN SELECT CASE WHEN NEW.sequence != (SELECT sequence+1 FROM metadata WHERE singleton=1) OR NEW.previous_sha256 IS NOT (SELECT head_sha256 FROM metadata WHERE singleton=1) THEN RAISE(ABORT,'record predecessor mismatch') END; END",
    "CREATE TRIGGER success_insert_guard BEFORE INSERT ON scope_success BEGIN SELECT CASE WHEN NEW.sequence != (SELECT sequence+1 FROM metadata WHERE singleton=1) OR NEW.scope_key IS NOT scope_success_key((SELECT payload FROM records WHERE sequence=NEW.sequence),NEW.entry_index) THEN RAISE(ABORT,'success entry mismatch') END; END",
    "CREATE TRIGGER success_no_update BEFORE UPDATE ON scope_success BEGIN SELECT RAISE(ABORT,'immutable success'); END",
    "CREATE TRIGGER success_no_delete BEFORE DELETE ON scope_success BEGIN SELECT RAISE(ABORT,'immutable success'); END",
    "CREATE TRIGGER metadata_update_guard BEFORE UPDATE ON metadata BEGIN SELECT CASE WHEN NEW.singleton != OLD.singleton OR NEW.schema != OLD.schema OR NEW.database_id != OLD.database_id OR NEW.migration_id != OLD.migration_id OR NEW.sequence != OLD.sequence+1 OR NEW.head_sha256 IS NOT (SELECT record_sha256 FROM records WHERE sequence=NEW.sequence) OR scope_success_count((SELECT payload FROM records WHERE sequence=NEW.sequence)) IS NOT (SELECT COUNT(*) FROM scope_success WHERE sequence=NEW.sequence) THEN RAISE(ABORT,'scope head mismatch') END; END",
    "CREATE TRIGGER audits_insert_guard BEFORE INSERT ON audits BEGIN SELECT CASE WHEN NEW.head_sequence != (SELECT sequence FROM metadata WHERE singleton=1) OR NEW.head_sha256 IS NOT (SELECT head_sha256 FROM metadata WHERE singleton=1) THEN RAISE(ABORT,'audit head mismatch') END; END",
    "CREATE TRIGGER audits_no_update BEFORE UPDATE ON audits BEGIN SELECT RAISE(ABORT,'immutable audit'); END",
    "CREATE TRIGGER audits_no_delete BEFORE DELETE ON audits BEGIN SELECT RAISE(ABORT,'immutable audit'); END",
    "CREATE TRIGGER audit_pages_no_update BEFORE UPDATE ON audit_pages BEGIN SELECT RAISE(ABORT,'immutable audit page'); END",
    "CREATE TRIGGER audit_pages_no_delete BEFORE DELETE ON audit_pages BEGIN SELECT RAISE(ABORT,'immutable audit page'); END",
    "CREATE TRIGGER audit_pages_insert_guard BEFORE INSERT ON audit_pages BEGIN SELECT CASE WHEN scope_audit_authorize(NEW.audit_id,NEW.page_number,NEW.start_sequence,NEW.next_sequence,NEW.previous_sha256,NEW.tail_sha256) IS NOT 1 OR NEW.page_number != COALESCE((SELECT MAX(page_number)+1 FROM audit_pages WHERE audit_id=NEW.audit_id),1) OR NEW.start_sequence != COALESCE((SELECT next_sequence FROM audit_pages WHERE audit_id=NEW.audit_id ORDER BY page_number DESC LIMIT 1),1) OR NEW.previous_sha256 IS NOT (SELECT tail_sha256 FROM audit_pages WHERE audit_id=NEW.audit_id ORDER BY page_number DESC LIMIT 1) OR NEW.next_sequence <= NEW.start_sequence OR NEW.next_sequence > (SELECT head_sequence+1 FROM audits WHERE audit_id=NEW.audit_id) OR (NEW.next_sequence = (SELECT head_sequence+1 FROM audits WHERE audit_id=NEW.audit_id) AND NEW.tail_sha256 IS NOT (SELECT head_sha256 FROM audits WHERE audit_id=NEW.audit_id)) THEN RAISE(ABORT,'audit page mismatch') END; END",
)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink)


def _regular(fd, name, *, required):
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        if required:
            raise ValueError("index file unavailable") from None
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("invalid index file")
    return _identity(info)


def _record_entries(payload):
    if type(payload) is not bytes or len(payload) > _MAX_PAYLOAD:
        raise ValueError("invalid scope record payload")
    body, _ = storage._unwrap(payload)
    if type(body.get("entries")) is not list:
        raise ValueError("invalid scope record entries")
    return body["entries"]


def _success_key_from_payload(payload, entry_index):
    entries = _record_entries(payload)
    if type(entry_index) is not int or not 0 <= entry_index < len(entries):
        raise ValueError("invalid scope entry index")
    entry = entries[entry_index]
    if entry["check_status"] != "SUCCESS":
        raise ValueError("scope entry is not successful")
    contract = entry["success_proof"]["requested_contract"]
    if "requested_contract" in entry and entry["requested_contract"] != contract:
        raise ValueError("scope contract mismatch")
    return _scope_key(entry["target"], contract)


def _success_count_from_payload(payload):
    return sum(entry["check_status"] == "SUCCESS" for entry in _record_entries(payload))


@contextlib.contextmanager
def connect(*, root, fd, chain, database_id, migration_id, write, recovery=False, initialize=False):
    if (
        not runs._id(database_id)
        or not runs._id(migration_id)
        or not runs._directory_matches(chain)
    ):
        raise ValueError("invalid local index identity")
    name = f"scope-check-index-{database_id}.sqlite3"
    before = _regular(fd, name, required=True)
    for suffix in ("-wal", "-shm"):
        if _regular(fd, name + suffix, required=False) is not None:
            raise ValueError("unexpected SQLite WAL")
    journal = _regular(fd, name + "-journal", required=False)
    if journal is not None and not recovery:
        raise ValueError("SQLite journal requires original ID recovery")
    path = Path(root) / "observation-runs" / name
    uri = "file:" + quote(str(path), safe="/") + ("?mode=rw" if write else "?mode=ro")
    connection = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None)
    try:
        connection.create_function("scope_success_key", 2, _success_key_from_payload)
        connection.create_function("scope_success_count", 1, _success_count_from_payload)
        connection.create_function("scope_audit_authorize", 6, lambda *_: 0)
        if _regular(fd, name, required=True) != before or not runs._directory_matches(chain):
            raise ValueError("index file identity changed")
        if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
            raise ValueError("unsupported SQLite journal mode")
        connection.execute("PRAGMA foreign_keys=ON")
        if write:
            connection.execute("PRAGMA synchronous=FULL")
        if not initialize:
            definitions = dict(
                connection.execute(
                    "SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                )
            )
            expected = {statement.split()[2]: statement for statement in _DDL}
            if definitions != expected:
                raise ValueError("index schema changed")
            meta = connection.execute(
                "SELECT schema, database_id, migration_id, sequence, head_sha256 FROM metadata WHERE singleton=1"
            ).fetchone()
            if (
                meta is None
                or meta[:3] != (_SCHEMA, database_id, migration_id)
                or type(meta[3]) is not int
                or meta[3] < 0
            ):
                raise ValueError("index metadata changed")
            if meta[3] == 0:
                if (
                    meta[4] is not None
                    or connection.execute("SELECT 1 FROM records WHERE sequence=1").fetchone()
                ):
                    raise ValueError("index head changed")
            else:
                tail = connection.execute(
                    "SELECT record_sha256 FROM records WHERE sequence=?", (meta[3],)
                ).fetchone()
                if (
                    tail != (meta[4],)
                    or connection.execute(
                        "SELECT 1 FROM records WHERE sequence=?", (meta[3] + 1,)
                    ).fetchone()
                ):
                    raise ValueError("index head changed")
        yield connection
        if _regular(fd, name, required=True) != before or not runs._directory_matches(chain):
            raise ValueError("index file identity changed")
    finally:
        connection.close()


def create(*, root, fd, chain, database_id, migration_id, records):
    name = f"scope-check-index-{database_id}.sqlite3"
    index_fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    os.fchmod(index_fd, 0o600)
    os.close(index_fd)
    os.fsync(fd)
    with connect(
        root=root,
        fd=fd,
        chain=chain,
        database_id=database_id,
        migration_id=migration_id,
        write=True,
        initialize=True,
    ) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in _DDL:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO metadata VALUES(1,?,?,?,?,?)",
                (_SCHEMA, database_id, migration_id, 0, None),
            )
            for record, digest in records:
                insert_record(connection, record, digest, legacy=True)
            connection.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                connection.execute("ROLLBACK")
            raise
    os.fsync(fd)
    with connect(
        root=root,
        fd=fd,
        chain=chain,
        database_id=database_id,
        migration_id=migration_id,
        write=False,
    ) as connection:
        meta = connection.execute(
            "SELECT sequence,head_sha256 FROM metadata WHERE singleton=1"
        ).fetchone()
        if meta != (len(records), records[-1][1] if records else None):
            raise ValueError("migration index verification failed")


def _scope_key(target, requested_contract):
    if type(target) is not dict or type(requested_contract) is not str or not requested_contract:
        raise ValueError("incomplete scope")
    return storage._json({"target": target, "requested_contract": requested_contract}).decode(
        "ascii"
    )


def insert_record(connection, record, digest, *, legacy):
    """Insert one record inside a caller-owned SQLite transaction."""
    if type(legacy) is not bool or storage._unwrap(storage._envelope(record))[1] != digest:
        raise ValueError("invalid scope record digest")
    previous = connection.execute(
        "SELECT sequence,head_sha256 FROM metadata WHERE singleton=1"
    ).fetchone()
    if (
        previous is None
        or record["sequence"] != previous[0] + 1
        or record["previous_sha256"] != previous[1]
    ):
        raise ValueError("scope index CAS failed")
    payload = storage._envelope(record)
    if len(payload) > _MAX_PAYLOAD:
        raise ValueError("scope record exceeds bound")
    connection.execute(
        "INSERT INTO records VALUES(?,?,?,?,?,?)",
        (
            record["sequence"],
            record["batch_id"],
            digest,
            record["previous_sha256"],
            payload,
            int(legacy),
        ),
    )
    for index, entry in enumerate(record["entries"]):
        if entry["check_status"] != "SUCCESS":
            continue
        contract = entry["success_proof"]["requested_contract"]
        if "requested_contract" in entry and entry["requested_contract"] != contract:
            raise ValueError("scope contract mismatch")
        key = _scope_key(entry["target"], contract)
        connection.execute(
            "INSERT INTO scope_success VALUES(?,?,?)",
            (key, record["sequence"], index),
        )
    changed = connection.execute(
        "UPDATE metadata SET sequence=?, head_sha256=? WHERE singleton=1 AND sequence=? AND head_sha256 IS ?",
        (record["sequence"], digest, previous[0], previous[1]),
    ).rowcount
    if changed != 1:
        raise ValueError("scope index CAS failed")


def _validate_migration_intent(body, migration_id):
    if not runs._id(migration_id):
        raise ValueError("invalid migration ID")
    if (
        type(body) is not dict
        or set(body)
        != {
            "schema",
            "environment",
            "source_authentication",
            "migration_id",
            "run_id",
            "declaration",
            "legacy_ids",
            "legacy_head",
            "policy",
            "baseline",
        }
        or body["schema"] != "bt_api.calendar-scope-migration-intent/1"
        or body["environment"] != "SIMULATION_ONLY"
        or body["source_authentication"] != "NOT_PROVEN"
        or body["migration_id"] != migration_id
        or body["declaration"] != _DECLARATION
        or not runs._id(body["run_id"])
        or type(body["legacy_ids"]) is not list
        or len(body["legacy_ids"]) > 256
        or any(not runs._id(item) for item in body["legacy_ids"])
        or len(set(body["legacy_ids"])) != len(body["legacy_ids"])
    ):
        raise ValueError("migration intent changed")


def _migration_intent(root, fd, chain, migration_id):
    from bt_api_py import trading_calendar_scope_checks as checks

    raw = checks._read(fd, chain, f"scope-check-migration-{migration_id}.json")
    if raw is None:
        raise ValueError("migration intent unavailable")
    body, digest = raw
    _validate_migration_intent(body, migration_id)
    return body, digest


class MigrationPendingError(ValueError):
    def __init__(self, migration_id):
        super().__init__("scope migration requires original ID reconciliation")
        self.scope_migration_id = migration_id


def _migration_pending(root, fd, chain):
    from bt_api_py import trading_calendar_scope_checks as checks

    pending = checks._read(fd, chain, "scope-check-migration-pending.json")
    if pending is not None:
        _validate_migration_intent(pending[0], pending[0].get("migration_id"))
    return pending


def _ensure_migration_intent(root, fd, chain, migration_id):
    from bt_api_py import trading_calendar_scope_checks as checks

    pending = _migration_pending(root, fd, chain)
    existing = checks._read(fd, chain, f"scope-check-migration-{migration_id}.json")
    if pending is None:
        if existing is None:
            raise ValueError("original migration barrier not observed")
        body, digest = _migration_intent(root, fd, chain, migration_id)
        head = checks._read(fd, chain, checks._HEAD)
        if (
            head is None
            or head[0].get("state") != "READY"
            or head[0]
            != _gate(migration_id, digest, state="READY", database_id=head[0].get("database_id"))
        ):
            raise ValueError("migration barrier missing")
        return body, digest, None
    if pending[0]["migration_id"] != migration_id:
        raise MigrationPendingError(pending[0]["migration_id"])
    if existing is None:
        state, _ = runs._write_once(
            fd, chain, f"scope-check-migration-{migration_id}.json", storage._envelope(pending[0])
        )
        if state != "COMMITTED":
            raise ValueError("migration intent outcome unknown")
        os.fsync(fd)
        existing = checks._read(fd, chain, f"scope-check-migration-{migration_id}.json")
    if existing != pending:
        raise ValueError("migration intent and barrier differ")
    return pending[0], pending[1], pending


def _clear_migration_pending(root, fd, chain, pending):
    if pending is not None:
        if _migration_pending(root, fd, chain) != pending:
            raise ValueError("migration barrier changed")
        os.unlink("scope-check-migration-pending.json", dir_fd=fd)
        os.fsync(fd)


def check_migration_barrier(root, *, allow_migration_id=None):
    """Call while holding the Store lock; no nested lock or network IO."""
    from bt_api_py import trading_calendar_scope_checks as checks

    fds, chain = runs._open_runs(root, create=False)
    try:
        pending = _migration_pending(root, fds[-1], chain)
        head = checks._read(fds[-1], chain, checks._HEAD)
        if pending is not None:
            migration_id = pending[0]["migration_id"]
            if migration_id != allow_migration_id:
                raise MigrationPendingError(migration_id)
        if (
            head is not None
            and head[0].get("schema") == "bt_api.calendar-scope-check-head/2"
            and head[0].get("state") == "MIGRATING"
        ):
            migration_id = head[0].get("migration_id")
            if not runs._id(migration_id) or migration_id != allow_migration_id:
                raise MigrationPendingError(migration_id if runs._id(migration_id) else None)
            if pending is None or pending[0]["migration_id"] != migration_id:
                raise ValueError("migration barrier unavailable")
    finally:
        runs._close(fds)


def _gate(migration_id, intent_sha, *, state, database_id=None):
    value = {
        "schema": "bt_api.calendar-scope-check-head/2",
        "state": state,
        "migration_id": migration_id,
        "intent_sha256": intent_sha,
        "environment": "SIMULATION_ONLY",
        "old_writers": _DECLARATION,
    }
    if database_id is not None:
        value["database_id"] = database_id
    return value


def _check_context(operator, intent):
    from dataclasses import asdict

    if operator._store._pending_exists() or operator._store._policy_change_pending():
        raise ValueError("store operation pending")
    policy = operator._store._review_policy_locked()
    operator._check_policy_locked(policy, allow_scope_migration=True)
    current = operator._store.read_current()
    baseline = {
        "publication_id": current.publication_id,
        "publication_sha256": current.publication_sha256,
        "version_sha256": current.version_sha256,
    }
    if asdict(policy) != intent["policy"] or baseline != intent["baseline"]:
        raise ValueError("migration policy or CURRENT changed")


def begin(*, operator, migration_id, legacy_ids, declaration):
    """Install a migration gate after a declared maintenance window and full old scan."""
    from dataclasses import asdict

    from bt_api_py import trading_calendar_scope_checks as checks

    result = {
        **checks._LABEL,
        "migration_id": migration_id,
        "old_writers": _DECLARATION if declaration == _DECLARATION else "NOT_DECLARED",
        "publication": "NOT_ATTEMPTED",
    }
    if not runs._id(migration_id) or declaration != _DECLARATION or type(legacy_ids) is not list:
        return {**result, "status": "NOT_COMMITTED", "reason": "DECLARATION_OR_IDS_REQUIRED"}
    try:
        operator._guard()
        with operator._store._writer_lock():
            if operator._store._pending_exists() or operator._store._policy_change_pending():
                raise ValueError("store operation pending")
            policy = operator._store._review_policy_locked()
            operator._check_policy_locked(policy)
            current = operator._store.read_current()
            baseline = {
                "publication_id": current.publication_id,
                "publication_sha256": current.publication_sha256,
                "version_sha256": current.version_sha256,
            }
            fds, chain = runs._open_runs(operator.root, create=False)
            try:
                fd = fds[-1]
                _, previous = checks._load_locked(operator.root, fd, chain, legacy_ids=legacy_ids)
                body = {
                    "schema": "bt_api.calendar-scope-migration-intent/1",
                    **checks._LABEL,
                    "migration_id": migration_id,
                    "run_id": operator.run_id,
                    "declaration": declaration,
                    "legacy_ids": legacy_ids,
                    "legacy_head": None
                    if previous is None
                    else {"body": previous[0], "sha256": previous[1]},
                    "policy": asdict(policy),
                    "baseline": baseline,
                }
                pending = _migration_pending(operator.root, fd, chain)
                if pending is not None:
                    return {
                        **result,
                        "status": "UNKNOWN",
                        "reason": "MIGRATION_RECONCILE_ORIGINAL_ID",
                        "pending_migration_id": pending[0]["migration_id"],
                    }
                if (
                    checks._read(fd, chain, f"scope-check-migration-{migration_id}.json")
                    is not None
                ):
                    return {**result, "status": "UNKNOWN", "reason": "MIGRATION_ID_RECONCILE_FIRST"}
                state, reason = runs._write_once(
                    fd, chain, "scope-check-migration-pending.json", storage._envelope(body)
                )
                if state != "COMMITTED":
                    return {
                        **result,
                        "status": "UNKNOWN",
                        "reason": "MIGRATION_BARRIER_RECONCILE_FIRST",
                        "barrier_state": state,
                        "detail": reason,
                    }
                os.fsync(fd)
                pending = _migration_pending(operator.root, fd, chain)
                if pending is None or pending[0] != body:
                    raise ValueError("migration barrier uncertain")
                state, reason = runs._write_once(
                    fd, chain, f"scope-check-migration-{migration_id}.json", storage._envelope(body)
                )
                if state != "COMMITTED":
                    return {
                        **result,
                        "status": "UNKNOWN",
                        "reason": "MIGRATION_INTENT_RECONCILE_FIRST",
                        "intent_state": state,
                        "detail": reason,
                    }
                os.fsync(fd)
                stored, digest = _migration_intent(operator.root, fd, chain, migration_id)
                if stored != body:
                    raise ValueError("migration intent differs")
                _, current_head = checks._load_locked(
                    operator.root, fd, chain, legacy_ids=legacy_ids
                )
                _check_context(operator, body)
                if current_head != previous:
                    raise ValueError("legacy head changed")
                checks._replace_head(
                    fd, chain, previous, _gate(migration_id, digest, state="MIGRATING")
                )
                return {
                    **result,
                    "status": "PENDING_MIGRATION",
                    "reason": "MIGRATION_GATE_VISIBLE",
                    "intent_sha256": digest,
                }
            finally:
                runs._close(fds)
    except (KeyboardInterrupt, SystemExit) as exc:
        exc.scope_migration_outcome = {**result, "status": "UNKNOWN"}
        raise
    except Exception:
        return {**result, "status": "UNKNOWN", "reason": "MIGRATION_RECONCILE_ORIGINAL_ID"}


def _legacy_under_gate(root, fd, chain, intent, gate):
    from bt_api_py import trading_calendar_scope_checks as checks

    names, fingerprint = checks._scan_names(fd, chain, exclude_batch_id=None, allow_long_scan=True)
    if names != sorted(f"{checks._PREFIX}{item}.json" for item in intent["legacy_ids"]):
        raise ValueError("legacy manifest changed")
    records = []
    for name in names:
        item = checks._read(fd, chain, name)
        if item is None:
            raise ValueError("legacy record unavailable")
        records.append((checks._validate_record(root, name, item[0]), item[1]))
    records.sort(key=lambda item: item[0]["sequence"])
    previous = None
    for sequence, (record, digest) in enumerate(records, 1):
        if record["sequence"] != sequence or record["previous_sha256"] != previous:
            raise ValueError("legacy chain changed")
        previous = digest
    old = intent["legacy_head"]
    if not records:
        if old is not None:
            raise ValueError("legacy head changed")
    elif (
        type(old) is not dict
        or old.get("body")
        != {
            "schema": "bt_api.calendar-scope-check-head/1",
            "sequence": len(records),
            "record_sha256": records[-1][1],
            "batch_id": records[-1][0]["batch_id"],
        }
        or storage._unwrap(storage._envelope(old["body"]))[1] != old.get("sha256")
    ):
        raise ValueError("legacy head changed")
    again, fingerprint_after = checks._scan_names(
        fd, chain, exclude_batch_id=None, allow_long_scan=True
    )
    if (
        again != names
        or fingerprint != fingerprint_after
        or checks._read(fd, chain, checks._HEAD) != gate
    ):
        raise ValueError("legacy directory changed")
    return records


def resume(*, operator, migration_id, declaration):
    """Build a fresh unreferenced DB, then install READY under the same gate."""
    import uuid

    from bt_api_py import trading_calendar_scope_checks as checks

    result = {
        **checks._LABEL,
        "migration_id": migration_id,
        "old_writers": _DECLARATION if declaration == _DECLARATION else "NOT_DECLARED",
        "publication": "NOT_ATTEMPTED",
    }
    if not runs._id(migration_id) or declaration != _DECLARATION:
        return {**result, "status": "NOT_COMMITTED", "reason": "DECLARATION_OR_IDS_REQUIRED"}
    try:
        operator._guard()
        with operator._store._writer_lock():
            fds, chain = runs._open_runs(operator.root, create=False)
            try:
                fd = fds[-1]
                intent, digest, pending = _ensure_migration_intent(
                    operator.root, fd, chain, migration_id
                )
                if operator.run_id != intent["run_id"]:
                    raise ValueError("migration run changed")
                result["old_writers"] = _DECLARATION
                _check_context(operator, intent)
                current = checks._read(fd, chain, checks._HEAD)
                old_head = intent["legacy_head"]
                expected_old = None if old_head is None else (old_head["body"], old_head["sha256"])
                if current == expected_old:
                    _, verified_old = checks._load_locked(
                        operator.root, fd, chain, legacy_ids=intent["legacy_ids"]
                    )
                    if verified_old != expected_old:
                        raise ValueError("legacy head changed")
                    checks._replace_head(
                        fd, chain, expected_old, _gate(migration_id, digest, state="MIGRATING")
                    )
                    current = checks._read(fd, chain, checks._HEAD)
                if current is None:
                    raise ValueError("migration gate unavailable")
                if current[0].get("state") == "READY":
                    database_id = current[0].get("database_id")
                    if current[0] != _gate(
                        migration_id, digest, state="READY", database_id=database_id
                    ):
                        raise ValueError("migration gate changed")
                    with connect(
                        root=operator.root,
                        fd=fd,
                        chain=chain,
                        database_id=database_id,
                        migration_id=migration_id,
                        write=False,
                    ):
                        pass
                    _clear_migration_pending(operator.root, fd, chain, pending)
                    os.fsync(fd)
                    return {
                        **result,
                        "status": "VISIBLE_UNAUTHENTICATED",
                        "reason": "ALREADY_READY",
                        "history_audit": "NOT_PERFORMED",
                    }
                if current[0] != _gate(migration_id, digest, state="MIGRATING"):
                    raise ValueError("migration gate changed")
                records = _legacy_under_gate(operator.root, fd, chain, intent, current)
                database_id = uuid.uuid4().hex
                create(
                    root=operator.root,
                    fd=fd,
                    chain=chain,
                    database_id=database_id,
                    migration_id=migration_id,
                    records=records,
                )
                if _legacy_under_gate(operator.root, fd, chain, intent, current) != records:
                    raise ValueError("legacy chain changed")
                _check_context(operator, intent)
                checks._replace_head(
                    fd,
                    chain,
                    current,
                    _gate(migration_id, digest, state="READY", database_id=database_id),
                )
                _clear_migration_pending(operator.root, fd, chain, pending)
                return {
                    **result,
                    "status": "VISIBLE_UNAUTHENTICATED",
                    "reason": "MIGRATION_READY",
                    "database_id": database_id,
                    "history_audit": "NOT_PERFORMED",
                }
            finally:
                runs._close(fds)
    except (KeyboardInterrupt, SystemExit) as exc:
        exc.scope_migration_outcome = {**result, "status": "UNKNOWN"}
        raise
    except Exception:
        return {**result, "status": "UNKNOWN", "reason": "MIGRATION_RECONCILE_ORIGINAL_ID"}


def show_migration(*, operator, migration_id):
    from bt_api_py import trading_calendar_scope_checks as checks

    result = {
        **checks._LABEL,
        "migration_id": migration_id,
        "old_writers": "DECLARATION_NOT_OBSERVED",
        "publication": "NOT_ATTEMPTED",
        "history_audit": "NOT_PERFORMED",
    }
    if not runs._id(migration_id):
        return {**result, "status": "UNKNOWN", "reason": "INVALID_MIGRATION_ID"}
    try:
        operator._guard()
        with operator._store._readonly_lock():
            fds, chain = runs._open_runs(operator.root, create=False)
            try:
                fd = fds[-1]
                pending = _migration_pending(operator.root, fd, chain)
                existing = checks._read(fd, chain, f"scope-check-migration-{migration_id}.json")
                if existing is None:
                    if pending is None or pending[0]["migration_id"] != migration_id:
                        raise ValueError("migration identity unavailable")
                    intent, digest = pending
                else:
                    intent, digest = _migration_intent(operator.root, fd, chain, migration_id)
                    if pending is not None and pending != existing:
                        raise ValueError("migration barrier changed")
                if operator.run_id != intent["run_id"]:
                    raise ValueError("migration run changed")
                result["old_writers"] = _DECLARATION
                head = checks._read(fd, chain, checks._HEAD)
                expected_old = (
                    None
                    if intent["legacy_head"] is None
                    else (intent["legacy_head"]["body"], intent["legacy_head"]["sha256"])
                )
                if head == expected_old:
                    _, verified = checks._load_locked(
                        operator.root, fd, chain, legacy_ids=intent["legacy_ids"]
                    )
                    if verified != expected_old:
                        raise ValueError("legacy head changed")
                    return {**result, "status": "UNKNOWN", "reason": "MIGRATION_GATE_NOT_OBSERVED"}
                if head is None:
                    raise ValueError("migration gate unavailable")
                if head[0] == _gate(migration_id, digest, state="MIGRATING"):
                    _legacy_under_gate(operator.root, fd, chain, intent, head)
                    return {
                        **result,
                        "status": "PENDING_MIGRATION",
                        "reason": "MIGRATION_GATE_VISIBLE",
                    }
                database_id = head[0].get("database_id")
                if head[0] != _gate(migration_id, digest, state="READY", database_id=database_id):
                    raise ValueError("migration gate changed")
                with connect(
                    root=operator.root,
                    fd=fd,
                    chain=chain,
                    database_id=database_id,
                    migration_id=migration_id,
                    write=False,
                ):
                    pass
                if pending is not None:
                    return {
                        **result,
                        "status": "UNKNOWN",
                        "reason": "MIGRATION_CLEANUP_RECONCILE_FIRST",
                        "database_id": database_id,
                    }
                return {
                    **result,
                    "status": "VISIBLE_UNAUTHENTICATED",
                    "reason": "MIGRATION_READY",
                    "database_id": database_id,
                }
            finally:
                runs._close(fds)
    except Exception:
        return {**result, "status": "UNKNOWN", "reason": "MIGRATION_RECONCILE_ORIGINAL_ID"}


def _ready(root, fd, chain):
    from bt_api_py import trading_calendar_scope_checks as checks

    head = checks._read(fd, chain, checks._HEAD)
    if head is None or type(head[0]) is not dict or head[0].get("state") != "READY":
        raise ValueError("scope index not ready")
    marker = head[0]
    migration_id = marker.get("migration_id")
    intent, digest = _migration_intent(root, fd, chain, migration_id)
    database_id = marker.get("database_id")
    if marker != _gate(
        migration_id, digest, state="READY", database_id=database_id
    ) or not runs._id(database_id):
        raise ValueError("scope index marker changed")
    return marker, intent


def _indexed_record(connection, root, fd, chain, sequence):
    from bt_api_py import trading_calendar_scope_checks as checks

    row = connection.execute(
        "SELECT batch_id,record_sha256,previous_sha256,payload,legacy FROM records WHERE sequence=?",
        (sequence,),
    ).fetchone()
    if row is None:
        raise ValueError("scope index record unavailable")
    batch_id, digest, previous, payload, legacy = row
    if type(payload) is not bytes or len(payload) > _MAX_PAYLOAD:
        raise ValueError("invalid indexed record")
    body, actual = storage._unwrap(payload)
    if (
        actual != digest
        or body.get("batch_id") != batch_id
        or body.get("sequence") != sequence
        or body.get("previous_sha256") != previous
    ):
        raise ValueError("scope indexed record changed")
    head = connection.execute("SELECT sequence FROM metadata WHERE singleton=1").fetchone()
    if head is None or sequence > head[0]:
        raise ValueError("scope record exceeds indexed head")
    if sequence == 1:
        if previous is not None:
            raise ValueError("scope first predecessor changed")
    else:
        predecessor = connection.execute(
            "SELECT record_sha256 FROM records WHERE sequence=?", (sequence - 1,)
        ).fetchone()
        if predecessor != (previous,):
            raise ValueError("scope predecessor changed")
    if legacy:
        source = checks._read(fd, chain, f"{checks._PREFIX}{batch_id}.json")
        if source is None or source != (body, digest):
            raise ValueError("legacy original changed")
    checks._validate_record(root, f"{checks._PREFIX}{batch_id}.json", body)
    return body, digest


def _verify_success_indexes(connection, record):
    expected = [
        (_scope_key(entry["target"], entry["success_proof"]["requested_contract"]), position)
        for position, entry in enumerate(record["entries"])
        if entry["check_status"] == "SUCCESS"
    ]
    actual = connection.execute(
        "SELECT scope_key,entry_index FROM scope_success WHERE sequence=? ORDER BY entry_index",
        (record["sequence"],),
    ).fetchall()
    if actual != expected:
        raise ValueError("scope success index differs from source record")


def _result_for_targets(connection, root, fd, chain, targets):
    from bt_api_py import trading_calendar_scope_checks as checks

    values = []
    cached = {}
    for query in targets:
        if type(query) is not dict or set(query) != {"target", "requested_contract"}:
            raise ValueError("invalid scope query")
        key = _scope_key(query["target"], query["requested_contract"])
        found = connection.execute(
            "SELECT sequence,entry_index FROM scope_success WHERE scope_key=? ORDER BY sequence DESC LIMIT 1",
            (key,),
        ).fetchone()
        if found is None:
            values.append({**query, "last_success": {"status": "UNKNOWN"}})
            continue
        sequence, entry_index = found
        if (
            type(sequence) is not int
            or type(entry_index) is not int
            or sequence < 1
            or entry_index < 0
        ):
            raise ValueError("invalid scope index")
        if sequence not in cached:
            cached[sequence] = _indexed_record(connection, root, fd, chain, sequence)
        record, digest = cached[sequence]
        if entry_index >= len(record["entries"]):
            raise ValueError("scope entry unavailable")
        entry = record["entries"][entry_index]
        if (
            entry["check_status"] != "SUCCESS"
            or _scope_key(entry["target"], entry["success_proof"]["requested_contract"]) != key
        ):
            raise ValueError("scope entry mismatch")
        item = checks._result([(record, digest)], [query])["scopes"][0]
        values.append(item)
    return {
        **checks._LABEL,
        "status": "VISIBLE_UNAUTHENTICATED",
        "scopes": values,
        "venue_last_success": "UNKNOWN",
        "current_scope_evidence": "REVALIDATED_LOCALLY_UNAUTHENTICATED",
        "history_audit": "NOT_PERFORMED",
        "old_writers": _DECLARATION,
    }


def _pending(root, fd, chain):
    from bt_api_py import trading_calendar_scope_checks as checks

    item = checks._read(fd, chain, "scope-check-index-pending.json")
    if item is None:
        return None
    body, digest = item
    if (
        type(body) is not dict
        or set(body)
        != {
            "schema",
            "environment",
            "source_authentication",
            "batch_id",
            "database_id",
            "record",
            "record_sha256",
        }
        or body["schema"] != "bt_api.calendar-scope-index-pending/2"
        or body["environment"] != "SIMULATION_ONLY"
        or body["source_authentication"] != "NOT_PROVEN"
        or not runs._id(body["batch_id"])
        or not runs._id(body["database_id"])
        or type(body["record"]) is not dict
        or body["record"].get("batch_id") != body["batch_id"]
        or storage._unwrap(storage._envelope(body["record"]))[1] != body["record_sha256"]
    ):
        raise ValueError("scope pending changed")
    checks._validate_record(root, f"{checks._PREFIX}{body['batch_id']}.json", body["record"])
    return body, digest


def _apply_intent(connection, intent):
    record = intent["record"]
    existing = connection.execute(
        "SELECT record_sha256,payload FROM records WHERE batch_id=?", (record["batch_id"],)
    ).fetchone()
    payload = storage._envelope(record)
    if existing is not None:
        if existing != (intent["record_sha256"], payload):
            raise ValueError("original batch differs")
        return "ALREADY_RECORDED"
    connection.execute("BEGIN IMMEDIATE")
    try:
        insert_record(connection, record, intent["record_sha256"], legacy=False)
        connection.execute("COMMIT")
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            connection.execute("ROLLBACK")
        raise
    return "RECORDED"


def record_locked(*, operator, fd, chain, batch_id, evidence_sha256):
    from dataclasses import asdict

    from bt_api_py import trading_calendar_mixed_publication as mixed_publication
    from bt_api_py import trading_calendar_scope_checks as checks

    marker, _ = _ready(operator.root, fd, chain)
    if _pending(operator.root, fd, chain) is not None:
        raise ValueError("scope index reconciliation pending")
    evidence = mixed_publication._batch_record(operator.root, batch_id, evidence_sha256)
    if operator._store._pending_exists() or operator._store._policy_change_pending():
        raise ValueError("store operation pending")
    policy = operator._store._review_policy_locked()
    operator._check_policy_locked(policy)
    current = operator._store.read_current()
    if asdict(policy) != evidence["policy"] or evidence["baseline"] != {
        "publication_id": current.publication_id,
        "publication_sha256": current.publication_sha256,
        "version_sha256": current.version_sha256,
    }:
        raise ValueError("batch baseline changed")
    entries = checks._entries(
        operator.root, evidence, schema="bt_api.calendar-scope-check-record/2"
    )
    with connect(
        root=operator.root,
        fd=fd,
        chain=chain,
        database_id=marker["database_id"],
        migration_id=marker["migration_id"],
        write=True,
    ) as connection:
        if connection.execute("SELECT 1 FROM records WHERE batch_id=?", (batch_id,)).fetchone():
            raise ValueError("original batch ID already consumed")
        sequence, previous = connection.execute(
            "SELECT sequence,head_sha256 FROM metadata WHERE singleton=1"
        ).fetchone()
        body = {
            "schema": "bt_api.calendar-scope-check-record/2",
            **checks._LABEL,
            "batch_id": batch_id,
            "run_id": evidence["run_id"],
            "evidence_sha256": evidence_sha256,
            "sequence": sequence + 1,
            "previous_sha256": previous,
            "entries": entries,
        }
        digest = storage._unwrap(storage._envelope(body))[1]
        pending = {
            "schema": "bt_api.calendar-scope-index-pending/2",
            **checks._LABEL,
            "batch_id": batch_id,
            "database_id": marker["database_id"],
            "record": body,
            "record_sha256": digest,
        }
        state, _ = runs._write_once(
            fd, chain, "scope-check-index-pending.json", storage._envelope(pending)
        )
        if state != "COMMITTED":
            raise ValueError("scope pending uncertain")
        os.fsync(fd)
        saved_pending = _pending(operator.root, fd, chain)
        if saved_pending is None or saved_pending[0] != pending:
            raise ValueError("scope pending unavailable")
        _apply_intent(connection, pending)
        os.fsync(fd)
        saved = _indexed_record(connection, operator.root, fd, chain, body["sequence"])
        if saved != (body, digest) or _pending(operator.root, fd, chain)[0] != pending:
            raise ValueError("scope index write uncertain")
        targets = [
            {"target": entry["target"], "requested_contract": entry["requested_contract"]}
            for entry in entries
        ]
        result = _result_for_targets(connection, operator.root, fd, chain, targets)
        os.unlink("scope-check-index-pending.json", dir_fd=fd)
        os.fsync(fd)
        return {**result, "record_state": "COMMITTED", "batch_id": batch_id}


def show_locked(*, operator, fd, chain, targets, batch_id):
    from bt_api_py import trading_calendar_scope_checks as checks

    marker, _ = _ready(operator.root, fd, chain)
    pending = _pending(operator.root, fd, chain)
    if pending is not None:
        return {
            **checks._LABEL,
            "status": "UNKNOWN",
            "reason": "SCOPE_INDEX_RECONCILE_ORIGINAL_ID",
            "batch_id": pending[0]["batch_id"],
            "old_writers": _DECLARATION,
            "history_audit": "NOT_PERFORMED",
        }
    with connect(
        root=operator.root,
        fd=fd,
        chain=chain,
        database_id=marker["database_id"],
        migration_id=marker["migration_id"],
        write=False,
    ) as connection:
        if batch_id is not None:
            row = connection.execute(
                "SELECT sequence FROM records WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if row is None:
                return {
                    **checks._LABEL,
                    "status": "UNKNOWN",
                    "reason": "LEGACY_OR_UNRECORDED_BATCH",
                    "batch_id": batch_id,
                    "old_writers": _DECLARATION,
                    "history_audit": "NOT_PERFORMED",
                }
            _indexed_record(connection, operator.root, fd, chain, row[0])
        return _result_for_targets(connection, operator.root, fd, chain, targets)


def reconcile_locked(*, operator, fd, chain, batch_id):
    from bt_api_py import trading_calendar_scope_checks as checks

    result = {
        **checks._LABEL,
        "batch_id": batch_id,
        "old_writers": _DECLARATION,
        "publication": "NOT_ATTEMPTED",
        "history_audit": "NOT_PERFORMED",
    }
    marker, _ = _ready(operator.root, fd, chain)
    pending = _pending(operator.root, fd, chain)
    if pending is not None and pending[0]["batch_id"] != batch_id:
        raise ValueError("another scope intent pending")
    if pending is None:
        with connect(
            root=operator.root,
            fd=fd,
            chain=chain,
            database_id=marker["database_id"],
            migration_id=marker["migration_id"],
            write=False,
        ) as connection:
            row = connection.execute(
                "SELECT sequence FROM records WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if row is None:
                return {
                    **result,
                    "status": "NOT_OBSERVED",
                    "reason": "ORIGINAL_RECORD_NOT_OBSERVED",
                }
            _indexed_record(connection, operator.root, fd, chain, row[0])
            return {**result, "status": "VISIBLE_UNAUTHENTICATED", "reason": "ALREADY_RECORDED"}
    with connect(
        root=operator.root,
        fd=fd,
        chain=chain,
        database_id=marker["database_id"],
        migration_id=marker["migration_id"],
        write=True,
        recovery=True,
    ) as connection:
        body = pending[0]
        if body["database_id"] != marker["database_id"]:
            raise ValueError("scope index identity changed")
        from bt_api_py import trading_calendar_mixed_publication as mixed_publication

        source_intent, _ = mixed_publication._batch_intent(operator.root, batch_id)
        if operator.run_id not in {source_intent["run_id"], source_intent["original_run_id"]}:
            raise ValueError("scope run identity mismatch")
        outcome = _apply_intent(connection, body)
        os.fsync(fd)
        if _indexed_record(connection, operator.root, fd, chain, body["record"]["sequence"]) != (
            body["record"],
            body["record_sha256"],
        ):
            raise ValueError("scope reconciliation uncertain")
        if pending is not None:
            if _pending(operator.root, fd, chain) != pending:
                raise ValueError("scope pending changed")
            os.unlink("scope-check-index-pending.json", dir_fd=fd)
            os.fsync(fd)
        return {
            **result,
            "status": "VISIBLE_UNAUTHENTICATED",
            "reason": "RECONCILED" if outcome == "RECORDED" else "ALREADY_RECORDED",
        }


def audit(*, operator, audit_id, action, limit=16):
    """Explicitly audit a pinned history prefix in bounded, durable pages."""
    from bt_api_py import trading_calendar_scope_checks as checks

    result = {
        **checks._LABEL,
        "audit_id": audit_id,
        "old_writers": "DECLARATION_NOT_OBSERVED",
        "publication": "NOT_ATTEMPTED",
        "audit_scope": "PINNED_PREFIX_LOCAL_NOT_CONTINUOUS",
    }
    if (
        not runs._id(audit_id)
        or action not in {"begin", "page", "show"}
        or type(limit) is not int
        or not 1 <= limit <= 32
    ):
        return {**result, "status": "UNKNOWN", "reason": "INVALID_AUDIT_ARGUMENTS"}
    try:
        operator._guard()
        lock = (
            operator._store._readonly_lock() if action == "show" else operator._store._writer_lock()
        )
        with lock:
            if operator._store._pending_exists() or operator._store._policy_change_pending():
                raise ValueError("store operation pending")
            policy = operator._store._review_policy_locked()
            operator._check_policy_locked(policy)
            fds, chain = runs._open_runs(operator.root, create=False)
            try:
                fd = fds[-1]
                marker, _ = _ready(operator.root, fd, chain)
                result["old_writers"] = _DECLARATION
                if _pending(operator.root, fd, chain) is not None:
                    raise ValueError("scope index reconciliation pending")
                with connect(
                    root=operator.root,
                    fd=fd,
                    chain=chain,
                    database_id=marker["database_id"],
                    migration_id=marker["migration_id"],
                    write=action != "show",
                ) as connection:
                    row = connection.execute(
                        "SELECT head_sequence,head_sha256 FROM audits WHERE audit_id=?",
                        (audit_id,),
                    ).fetchone()
                    if action == "begin":
                        if row is not None:
                            return {
                                **result,
                                "status": "UNKNOWN",
                                "reason": "AUDIT_ID_RECONCILE_FIRST",
                            }
                        sequence, digest = connection.execute(
                            "SELECT sequence,head_sha256 FROM metadata WHERE singleton=1"
                        ).fetchone()
                        connection.execute("BEGIN IMMEDIATE")
                        try:
                            connection.execute(
                                "INSERT INTO audits VALUES(?,?,?)",
                                (audit_id, sequence, digest),
                            )
                            connection.execute("COMMIT")
                        except BaseException:
                            with contextlib.suppress(sqlite3.Error):
                                connection.execute("ROLLBACK")
                            raise
                        os.fsync(fd)
                        row = (sequence, digest)
                    elif row is None:
                        return {**result, "status": "UNKNOWN", "reason": "AUDIT_ID_NOT_OBSERVED"}
                    target, head_sha = row
                    last_page = connection.execute(
                        "SELECT page_number,start_sequence,next_sequence,previous_sha256,tail_sha256 FROM audit_pages WHERE audit_id=? ORDER BY page_number DESC LIMIT 1",
                        (audit_id,),
                    ).fetchone()
                    page_number = 1 if last_page is None else last_page[0] + 1
                    next_sequence = 1 if last_page is None else last_page[2]
                    previous = None if last_page is None else last_page[4]
                    if next_sequence > target + 1 or (
                        next_sequence == target + 1 and previous != head_sha
                    ):
                        raise ValueError("audit page coverage changed")
                    page_revalidated = False
                    if action == "page" and next_sequence <= target:
                        stop = min(target + 1, next_sequence + limit)
                        prior = previous
                        for sequence in range(next_sequence, stop):
                            record, digest = _indexed_record(
                                connection, operator.root, fd, chain, sequence
                            )
                            if (
                                record["sequence"] != sequence
                                or record["previous_sha256"] != previous
                            ):
                                raise ValueError("audit predecessor changed")
                            _verify_success_indexes(connection, record)
                            previous = digest
                        if stop == target + 1 and previous != head_sha:
                            raise ValueError("audit head changed")
                        authorization = (
                            audit_id,
                            page_number,
                            next_sequence,
                            stop,
                            prior,
                            previous,
                        )
                        connection.create_function(
                            "scope_audit_authorize", 6, lambda *items: int(items == authorization)
                        )
                        connection.execute("BEGIN IMMEDIATE")
                        try:
                            connection.execute(
                                "INSERT INTO audit_pages VALUES(?,?,?,?,?,?)", authorization
                            )
                            connection.execute("COMMIT")
                        except BaseException:
                            with contextlib.suppress(sqlite3.Error):
                                connection.execute("ROLLBACK")
                            raise
                        finally:
                            connection.create_function("scope_audit_authorize", 6, lambda *_: 0)
                        os.fsync(fd)
                        next_sequence = stop
                        page_revalidated = True
                    status = "COMPLETE" if next_sequence == target + 1 else "IN_PROGRESS"
                    return {
                        **result,
                        "status": "VISIBLE_UNAUTHENTICATED",
                        "audit_status": status,
                        "pinned_head_sequence": target,
                        "next_sequence": next_sequence,
                        "history_audit": "PAGED_AUDIT_COMPLETED_NOT_CONTINUOUS"
                        if status == "COMPLETE"
                        else "FULL_HISTORY_NOT_AUDITED",
                        "show_assurance": "LOCAL_PROGRESS_NOT_REPLAYED"
                        if not page_revalidated
                        else "PAGE_REVALIDATED_LOCALLY",
                        **(
                            {"reason": "ALREADY_COMPLETE"}
                            if action == "page" and not page_revalidated and status == "COMPLETE"
                            else {}
                        ),
                    }
            finally:
                runs._close(fds)
    except (KeyboardInterrupt, SystemExit) as exc:
        exc.scope_audit_outcome = {**result, "status": "UNKNOWN"}
        raise
    except Exception:
        return {
            **result,
            "status": "UNKNOWN",
            "reason": "AUDIT_RECONCILE_ORIGINAL_ID",
            "history_audit": "FULL_HISTORY_NOT_AUDITED",
        }
