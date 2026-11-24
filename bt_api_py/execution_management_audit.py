"""Private, bounded audit journal for local Execution management reads.

This optional component is not an authorization service, an RPC handler, an
anti-rollback witness or a cross-database transaction.  Its caller must use
authenticated server-side identity and scope, check authorization including the
returned instrument, and recheck revocation at its own disclosure boundary.
It must not disclose a snapshot unless ``disclose_after_audit`` returns it.
The only result returned on a failed or uncertain audit write is an exception.

One correlation ID is bound to one request for a particular authenticated
principal and scope; it is not proof of identity or permission. Repeated reads
with the same request append separate attempts and may return a newer snapshot.
An attempt may be durable even if its response is lost. No entry is evicted.
The root directory is trusted and private to the service OS user; cooperating
writers use SQLite locks. This does not defend against a process that can
modify the private directory or restore an old copy of the database. Target
digests are correlation labels, not confidentiality for guessable identifiers.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

_FILE = 'execution-management-read.sqlite3'
_APPLICATION_ID = 0x454D4131
_SCHEMA_VERSION = 1
_MAX_TEXT_BYTES = 256
_MAX_RESULT_BYTES = 32768
_MAX_ATTEMPTS = 1_000_000
_INCARNATION = re.compile(r'[0-9a-f]{32}\Z')
_SCHEMA = (
    'CREATE TABLE audit_meta (id INTEGER PRIMARY KEY CHECK (id = 1), max_attempts INTEGER NOT NULL)',
    'CREATE TABLE audit_requests (request_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL) WITHOUT ROWID',
    'CREATE TABLE audit_attempts (sequence INTEGER PRIMARY KEY, request_key TEXT NOT NULL, '
    'fingerprint TEXT NOT NULL, service_instance TEXT NOT NULL, service_epoch TEXT NOT NULL, '
    'caller_role TEXT NOT NULL, principal_ref TEXT NOT NULL, actor_ref TEXT NOT NULL, '
    'account_ref TEXT NOT NULL, strategy_ref TEXT NOT NULL, domain_ref TEXT NOT NULL, '
    'policy_revision TEXT NOT NULL, operation_version INTEGER NOT NULL, query_id TEXT NOT NULL, '
    'outcome TEXT NOT NULL, journal_incarnation TEXT, target_digest TEXT NOT NULL, '
    'result_digest TEXT NOT NULL, occurred_at_ns INTEGER NOT NULL)',
)


class ExecutionManagementAuditError(RuntimeError):
    """A finite error that never includes request, identity or account values."""


class AuditStartupCleanupError(ExecutionManagementAuditError):
    """A partially initialized audit remains owned until cleanup is confirmed."""

    def __init__(self, audit):
        self.audit = audit
        super().__init__('AUDIT_STARTUP_CLEANUP_UNKNOWN')


def _error(code: str) -> None:
    raise ExecutionManagementAuditError(code)


def _text(value: object) -> bool:
    if type(value) is not str or not value or value != value.strip():
        return False
    try:
        return (len(value.encode('utf-8')) <= _MAX_TEXT_BYTES
                and not any(ord(c) < 32 or ord(c) == 127 for c in value))
    except UnicodeError:
        return False


def _encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('ascii')


def _digest(value: object) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class ManagementReadIdentity:
    """Trusted composition input, never constructed as authentication from wire data."""

    service_instance: str
    service_epoch: str
    caller_role: str
    principal_ref: str
    actor_ref: str
    account_ref: str
    strategy_ref: str
    domain_ref: str
    policy_revision: str

    def __post_init__(self) -> None:
        if (type(self.caller_role) is not str or self.caller_role not in {'application_client', 'service'}
                or not all(_text(value) for value in (self.service_instance, self.service_epoch,
                    self.principal_ref, self.actor_ref, self.account_ref, self.strategy_ref,
                    self.domain_ref, self.policy_revision))):
            _error('INVALID_AUDIT_INPUT')


class ExecutionManagementReadAudit:
    """Audit before releasing a local snapshot; owns no account journal or service.

    The root must already exist as a private (0700), real directory owned by
    the effective user. The journal is created as 0600 only if absent. Existing
    files are never initialized or migrated silently; identity, ownership,
    schema and capacity configuration are checked on every use. The object is
    process-bound; close prevents future writes. The caller must independently
    bind the supplied identity to an authenticated request and hold/recheck its
    grant through audit and response publication.
    """

    def __init__(self, root: str | Path, *, max_attempts: int = 100_000) -> None:
        self._lock = threading.RLock()
        self._pid = os.getpid()
        self._closed = False
        self._root_fd = None
        self._file_fd = None
        self._root_handle = None
        self._file_handle = None
        self._fd_open_pending = False
        self._fd_close_uncertain = False
        self._sql_open_pending = False
        self._active_connection = None
        self._connection_thread = None
        self._is_windows = os.name == 'nt'
        if self._is_windows:
            from bt_api_py._win32_security import has_privileges_available

            platform_ok = has_privileges_available()
        else:
            platform_ok = hasattr(os, 'geteuid') and hasattr(os, 'O_NOFOLLOW')
        if (type(max_attempts) is not int or not 1 <= max_attempts <= _MAX_ATTEMPTS
                or not platform_ok
                or not isinstance(root, (str, Path)) or not Path(root).is_absolute()):
            _error('INVALID_AUDIT_CONFIGURATION')
        self._max_attempts = max_attempts
        try:
            supplied = Path(root)
            if self._is_windows:
                from bt_api_py._win32_security import (
                    Win32SecurityUnavailable,
                    _set_owner_only_acl,
                    is_reparse_point,
                    open_identity_handle,
                )

                try:
                    if is_reparse_point(supplied):
                        _error('INVALID_AUDIT_CONFIGURATION')
                except Win32SecurityUnavailable:
                    _error('INVALID_AUDIT_CONFIGURATION')
                self._root = supplied.resolve(strict=True)
                self._fd_open_pending = True
                self._root_handle = open_identity_handle(self._root, directory=True)
                self._fd_open_pending = False
                self._check_root()
                self._path = self._root / _FILE
                created = False
                try:
                    if self._path.exists() and is_reparse_point(self._path):
                        _error('AUDIT_UNAVAILABLE')
                except Win32SecurityUnavailable:
                    _error('AUDIT_UNAVAILABLE')
                try:
                    create_fd = os.open(
                        str(self._path), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600
                    )
                    try:
                        _set_owner_only_acl(self._path)
                    finally:
                        os.close(create_fd)
                    created = True
                except FileExistsError:
                    created = False
                self._fd_open_pending = True
                self._file_handle = open_identity_handle(self._path, directory=False)
                self._fd_open_pending = False
            else:
                if supplied.is_symlink():
                    _error('INVALID_AUDIT_CONFIGURATION')
                self._root = supplied.resolve(strict=True)
                directory_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_DIRECTORY', 0)
                self._fd_open_pending = True
                self._root_fd = os.open(str(self._root), directory_flags)
                self._fd_open_pending = False
                self._check_root()
                created = False
                try:
                    self._fd_open_pending = True
                    self._file_fd = os.open(_FILE, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                            0o600, dir_fd=self._root_fd)
                    self._fd_open_pending = False
                    created = True
                except FileExistsError:
                    self._fd_open_pending = False
                    self._fd_open_pending = True
                    self._file_fd = os.open(_FILE, os.O_RDWR | os.O_NOFOLLOW, dir_fd=self._root_fd)
                    self._fd_open_pending = False
                self._path = self._root / _FILE
            self._check_files()
            with self._connect() as connection:
                if created:
                    if connection.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() != 'wal':
                        _error('AUDIT_UNAVAILABLE')
                    connection.execute('BEGIN IMMEDIATE')
                    try:
                        connection.execute(f'PRAGMA application_id={_APPLICATION_ID}')
                        connection.execute(f'PRAGMA user_version={_SCHEMA_VERSION}')
                        for statement in _SCHEMA:
                            connection.execute(statement)
                        connection.execute('INSERT INTO audit_meta VALUES (1,?)', (max_attempts,))
                        connection.execute('COMMIT')
                    except BaseException:
                        if connection.in_transaction:
                            connection.execute('ROLLBACK')
                        raise
                    if self._is_windows:
                        from bt_api_py._win32_security import _set_owner_only_acl

                        for suffix in ('-wal', '-shm'):
                            side = self._root / (_FILE + suffix)
                            if side.exists():
                                _set_owner_only_acl(side)
                self._check_schema(connection)
                if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    _error('AUDIT_UNAVAILABLE')
                self._check_files()
        except BaseException as exc:
            try:
                self.close()
            except BaseException:
                raise AuditStartupCleanupError(self) from None
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, ExecutionManagementAuditError):
                raise
            raise ExecutionManagementAuditError('AUDIT_UNAVAILABLE') from None

    def _check_root(self) -> None:
        if self._is_windows:
            from bt_api_py._win32_security import (
                Win32SecurityUnavailable,
                close_handle,
                handle_stamp,
                inspect_owner,
                is_reparse_point,
                open_identity_handle,
            )

            try:
                if self._root_handle is None or is_reparse_point(self._root):
                    _error('AUDIT_UNAVAILABLE')
                held = handle_stamp(self._root_handle)
                owner = inspect_owner(self._root)
                check_h = open_identity_handle(self._root, directory=True)
                try:
                    current = handle_stamp(check_h)
                finally:
                    close_handle(check_h)
                if (not stat.S_ISDIR(held[2]) or not stat.S_ISDIR(current[2])
                        or not owner.is_current_user or owner.others_writable
                        or held[:2] != current[:2]):
                    _error('AUDIT_UNAVAILABLE')
            except ExecutionManagementAuditError:
                raise
            except (Win32SecurityUnavailable, OSError, ValueError):
                _error('AUDIT_UNAVAILABLE')
            return
        held = os.fstat(self._root_fd)
        current = os.stat(self._root, follow_symlinks=False)
        if (not stat.S_ISDIR(held.st_mode) or not stat.S_ISDIR(current.st_mode)
                or held.st_uid != os.geteuid() or stat.S_IMODE(held.st_mode) != 0o700
                or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)):
            _error('AUDIT_UNAVAILABLE')

    def _check_files(self) -> None:
        self._check_root()
        if self._is_windows:
            from bt_api_py._win32_security import (
                Win32SecurityUnavailable,
                close_handle,
                handle_stamp,
                inspect_owner,
                is_reparse_point,
                open_identity_handle,
            )

            try:
                if self._file_handle is None or is_reparse_point(self._path):
                    _error('AUDIT_UNAVAILABLE')
                held = handle_stamp(self._file_handle)
                owner = inspect_owner(self._path)
                check_h = open_identity_handle(self._path, directory=False)
                try:
                    actual = handle_stamp(check_h)
                finally:
                    close_handle(check_h)
                if (not stat.S_ISREG(held[2]) or not stat.S_ISREG(actual[2])
                        or not owner.is_current_user or owner.others_writable
                        or held[4] != 1 or actual[4] != 1
                        or held[:2] != actual[:2]):
                    _error('AUDIT_UNAVAILABLE')
                for name in (_FILE + '-wal', _FILE + '-shm'):
                    side = self._root / name
                    if not side.exists():
                        continue
                    if is_reparse_point(side):
                        _error('AUDIT_UNAVAILABLE')
                    info = side.stat(follow_symlinks=False)
                    side_owner = inspect_owner(side)
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or not side_owner.is_current_user or side_owner.others_writable):
                        _error('AUDIT_UNAVAILABLE')
            except ExecutionManagementAuditError:
                raise
            except (Win32SecurityUnavailable, OSError, ValueError):
                _error('AUDIT_UNAVAILABLE')
            return
        held = os.fstat(self._file_fd)
        actual = os.stat(_FILE, dir_fd=self._root_fd, follow_symlinks=False)
        if (not stat.S_ISREG(held.st_mode) or not stat.S_ISREG(actual.st_mode)
                or held.st_uid != os.geteuid() or held.st_nlink != 1
                or stat.S_IMODE(held.st_mode) != 0o600
                or (held.st_dev, held.st_ino) != (actual.st_dev, actual.st_ino)):
            _error('AUDIT_UNAVAILABLE')
        for name in (_FILE + '-wal', _FILE + '-shm'):
            try:
                info = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                _error('AUDIT_UNAVAILABLE')

    @contextmanager
    def _connect(self):
        if self._sql_open_pending or self._active_connection is not None:
            _error('AUDIT_UNAVAILABLE')
        self._sql_open_pending = True
        connection = sqlite3.connect(str(self._path), timeout=0.5, isolation_level=None)
        self._active_connection = connection
        self._connection_thread = threading.current_thread()
        self._sql_open_pending = False
        try:
            connection.execute('PRAGMA synchronous=FULL')
            if connection.execute('PRAGMA synchronous').fetchone()[0] != 2:
                _error('AUDIT_UNAVAILABLE')
            yield connection
        finally:
            connection.close()
            self._active_connection = None
            self._connection_thread = None

    def _check_schema(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        expected = sorted(_SCHEMA, key=lambda sql: sql.split()[2])
        if (rows != [(statement,) for statement in expected]
                or connection.execute("SELECT count(*) FROM sqlite_master WHERE type != 'table'").fetchone()[0] != 0
                or connection.execute('PRAGMA application_id').fetchone()[0] != _APPLICATION_ID
                or connection.execute('PRAGMA user_version').fetchone()[0] != _SCHEMA_VERSION
                or connection.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'wal'
                or connection.execute('SELECT id,max_attempts FROM audit_meta').fetchall() != [(1, self._max_attempts)]):
            _error('AUDIT_UNAVAILABLE')

    def close(self) -> None:
        """Close owned resources without reusing an uncertain OS descriptor.

        A failed os.close has unspecified descriptor state and is never retried;
        a failed SQLite close retains its connection for its owner-thread retry.
        A native open that raised without a returned handle remains unknown.
        """
        if os.getpid() != self._pid:
            _error('AUDIT_UNAVAILABLE')
        with self._lock:
            if self._closed:
                return
            if self._fd_open_pending or self._fd_close_uncertain or self._sql_open_pending:
                _error('AUDIT_CLEANUP_UNKNOWN')
            if self._active_connection is not None:
                if self._connection_thread is not threading.current_thread():
                    _error('AUDIT_CLEANUP_UNKNOWN')
                self._active_connection.close()
                self._active_connection = None
                self._connection_thread = None
            for name in ('_file_fd', '_root_fd'):
                fd = getattr(self, name)
                if fd is not None:
                    # Set uncertainty *before* close, including asynchronous
                    # interruption after the OS consumed the descriptor.
                    self._fd_close_uncertain = True
                    setattr(self, name, None)
                    os.close(fd)
                    self._fd_close_uncertain = False
            if self._is_windows:
                from bt_api_py._win32_security import close_handle

                for name in ('_file_handle', '_root_handle'):
                    handle = getattr(self, name)
                    if handle is not None:
                        self._fd_close_uncertain = True
                        setattr(self, name, None)
                        close_handle(handle)
                        self._fd_close_uncertain = False
            self._closed = True

    def disclose_after_audit(self, *, identity: ManagementReadIdentity, query_id: str,
                             parent_ref: str | None, operation_version: int, snapshot: object,
                             original_run_id: str | None = None, request_id: str | None = None,
                             request_sha256: str | None = None) -> object:
        """Return the supplied snapshot only after its local audit transaction commits.

        The caller must authorize before calling and again before publishing a
        response. A failed/uncertain COMMIT raises even if SQLite did commit;
        retrying the same ID records a new attempt rather than caching a read.
        """
        from bt_api_execution.local_parent_snapshot import LocalParentSnapshot
        from bt_api_base.market_protocol import MarketInstrumentKey
        from .execution_management_plan_contract import MAX_PLAN_AUDIT_RESULT_BYTES, plan_snapshot_payload

        try:
            if (type(identity) is not ManagementReadIdentity or not _text(query_id)
                    or type(operation_version) is not int or operation_version not in (1, 2, 3)
                    or (operation_version in (1, 2) and (not _text(parent_ref)
                        or original_run_id is not None or request_id is not None or request_sha256 is not None))
                    or (operation_version == 3 and (parent_ref is not None
                        or not _text(original_run_id) or not _text(request_id)))):
                _error('INVALID_AUDIT_INPUT')
            if operation_version in (1, 3):
                if (type(snapshot) is not LocalParentSnapshot or snapshot.source != 'LOCAL_JOURNAL'
                        or snapshot.status not in {'OBSERVED', 'UNKNOWN'}):
                    _error('INVALID_AUDIT_INPUT')
                if snapshot.status == 'UNKNOWN':
                    if snapshot != LocalParentSnapshot('UNKNOWN'):
                        _error('INVALID_AUDIT_INPUT')
                elif ((operation_version == 1 and snapshot.parent_ref != parent_ref)
                      or not _text(snapshot.parent_ref) or type(snapshot.journal_incarnation) is not str
                      or _INCARNATION.fullmatch(snapshot.journal_incarnation) is None
                      or type(snapshot.instrument) is not MarketInstrumentKey
                      or snapshot.intake_local_state != 'RECEIVED'
                      or (snapshot.risk_local_state, snapshot.gateway_local_state,
                          snapshot.cancel_local_state) != ('UNKNOWN', 'UNKNOWN', 'UNKNOWN')):
                    _error('INVALID_AUDIT_INPUT')
                if operation_version == 3:
                    if ((snapshot.status == 'UNKNOWN' and request_sha256 is not None)
                            or (snapshot.status == 'OBSERVED' and (type(request_sha256) is not str
                                or re.fullmatch(r'[0-9a-f]{64}', request_sha256) is None))):
                        _error('INVALID_AUDIT_INPUT')
                    result = _encoded({'snapshot': asdict(snapshot), 'request_sha256': request_sha256})
                else:
                    result = _encoded(asdict(snapshot))
                result_limit = _MAX_RESULT_BYTES
            else:
                result = _encoded(plan_snapshot_payload(snapshot, parent_ref=parent_ref))
                result_limit = MAX_PLAN_AUDIT_RESULT_BYTES
            if len(result) > result_limit:
                _error('INVALID_AUDIT_INPUT')
            scope = {'instance': identity.service_instance, 'role': identity.caller_role,
                     'principal': identity.principal_ref, 'actor': identity.actor_ref,
                     'account': identity.account_ref, 'strategy': identity.strategy_ref,
                     'domain': identity.domain_ref}
            request_key = _digest({'schema': 'execution.management.read.key.v1',
                                   'scope': scope, 'query_id': query_id})
            if operation_version == 3:
                fingerprint = _digest({'schema': 'execution.management.read.request.v1',
                    'scope': scope, 'query_id': query_id, 'original_run_id': original_run_id,
                    'request_id': request_id, 'operation_version': operation_version})
                target_digest = _digest({'schema': 'execution.management.read.request_target.v1',
                    'account': identity.account_ref, 'strategy': identity.strategy_ref,
                    'domain': identity.domain_ref, 'original_run_id': original_run_id, 'request_id': request_id})
            else:
                fingerprint = _digest({'schema': 'execution.management.read.request.v1',
                                       'scope': scope, 'query_id': query_id,
                                       'parent_ref': parent_ref, 'operation_version': operation_version})
                target_digest = _digest({'schema': 'execution.management.read.target.v1',
                                         'account': identity.account_ref, 'strategy': identity.strategy_ref,
                                         'domain': identity.domain_ref, 'parent_ref': parent_ref})
            result_digest = hashlib.sha256(result).hexdigest()
            with self._lock:
                if self._closed or os.getpid() != self._pid:
                    _error('AUDIT_UNAVAILABLE')
                self._check_files()
                with self._connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    try:
                        self._check_files()
                        self._check_schema(connection)
                        row = connection.execute('SELECT fingerprint FROM audit_requests WHERE request_key=?',
                                                 (request_key,)).fetchone()
                        if row is not None and row[0] != fingerprint:
                            _error('QUERY_ID_CONFLICT')
                        if connection.execute('SELECT count(*) FROM audit_attempts').fetchone()[0] >= self._max_attempts:
                            _error('AUDIT_CAPACITY')
                        if row is None:
                            connection.execute('INSERT INTO audit_requests VALUES (?,?)', (request_key, fingerprint))
                        connection.execute(
                            'INSERT INTO audit_attempts(request_key,fingerprint,service_instance,service_epoch,'
                            'caller_role,principal_ref,actor_ref,account_ref,'
                            'strategy_ref,domain_ref,policy_revision,operation_version,query_id,outcome,'
                            'journal_incarnation,target_digest,result_digest,occurred_at_ns) '
                            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (request_key, fingerprint, identity.service_instance, identity.service_epoch,
                             identity.caller_role, identity.principal_ref, identity.actor_ref,
                             identity.account_ref, identity.strategy_ref, identity.domain_ref,
                             identity.policy_revision, operation_version, query_id, snapshot.status,
                             snapshot.journal_incarnation, target_digest, result_digest, time.time_ns()))
                        self._check_files()
                        connection.execute('COMMIT')
                    except BaseException:
                        if connection.in_transaction:
                            connection.execute('ROLLBACK')
                        raise
                self._check_files()
            return snapshot
        except (KeyboardInterrupt, SystemExit):
            raise
        except ExecutionManagementAuditError:
            raise
        except Exception:
            raise ExecutionManagementAuditError('AUDIT_UNAVAILABLE') from None
