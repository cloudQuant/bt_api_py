"""Standalone, authenticated local Execution management reads.

READY denotes only a management listener, never account or execution readiness.
The two private roots and their ancestors are trusted local operator inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import signal
import sqlite3
import stat
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.account_journal_host import AccountJournalHost
from bt_api_execution.contracts import ExecutionScope
from bt_api_execution.store import SqliteExecutionStore
from bt_api_transport_zmq.server import CurveServerCredentials

from .execution_management_read_grant import (ExecutionManagementReadGrant, PARENT_READ_V1,
    PARENT_PLAN_READ_V1, PARENT_BY_REQUEST_READ_V1)
from .execution_management_read_host import ExecutionManagementReadHost, TrustedManagementReadListener
from .monitor_service_config import read_bounded

SCHEMA = 'bt_api_execution.management_read_service.v1'
OPERATIONS = frozenset({PARENT_READ_V1, PARENT_PLAN_READ_V1, PARENT_BY_REQUEST_READ_V1})


class ReadServiceError(ValueError):
    pass


def _map(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ReadServiceError('INVALID_CONFIGURATION')
    return value


def _text(value):
    if (type(value) is not str or not value or value != value.strip() or not value.isprintable()
            or len(value.encode('utf-8')) > 128):
        raise ReadServiceError('INVALID_CONFIGURATION')
    return value


def _path(value):
    if type(value) is not str or not value or value != value.strip() or not value.isprintable() or len(value.encode("utf-8")) > 4096:
        raise ReadServiceError("INVALID_CONFIGURATION")
    path = Path(value)
    if not path.is_absolute() or str(path) != os.path.normpath(value):
        raise ReadServiceError('INVALID_CONFIGURATION')
    return path


def _identity(value, expected):
    value = _map(value, ('role', 'principal_ref', 'actor_ref'))
    if value['role'] != expected.value:
        raise ReadServiceError('INVALID_CONFIGURATION')
    return WireIdentity(expected, _text(value['principal_ref']), _text(value['actor_ref']))


def _endpoint(value):
    if type(value) is not str or not value or value != value.strip() or not value.isprintable() or len(value.encode("utf-8")) > 4096:
        raise ReadServiceError("INVALID_CONFIGURATION")
    try:
        prefix, port = value.rsplit(':', 1)
        if (not prefix.startswith('tcp://') or not ipaddress.ip_address(prefix[6:]).is_loopback
                or ipaddress.ip_address(prefix[6:]).version != 4
                or not 1 <= int(port) <= 65535 or str(int(port)) != port):
            raise ValueError()
    except ValueError:
        raise ReadServiceError('INVALID_CONFIGURATION') from None
    return value


@dataclass(frozen=True)
class Config:
    local_scope: ExecutionScope
    wire_scope: WireScope
    journal_root: Path
    audit_root: Path
    storage_id: str
    service_identity: WireIdentity
    client_identity: WireIdentity
    service_id: str
    instance_id: str
    scope_ref: str
    revision: str
    instruments: frozenset
    endpoint: str
    server_public: Path
    server_secret: Path
    client_public: Path


def parse_config(raw):
    top = _map(raw, ('schema', 'mode', 'execution_scope', 'wire_scope', 'storage',
                     'service_identity', 'client_identity', 'grant', 'listener'))
    if top['schema'] != SCHEMA or top['mode'] != 'management_read_only':
        raise ReadServiceError('INVALID_CONFIGURATION')
    local = _map(top['execution_scope'], ('provider', 'environment', 'account_ref', 'strategy_id'))
    local = ExecutionScope(**{k: _text(v) for k, v in local.items()})
    wire = _map(top['wire_scope'], ('domain', 'account_ref', 'strategy_ref', 'run_id'))
    if wire['run_id'] is not None:
        raise ReadServiceError('INVALID_CONFIGURATION')
    wire = WireScope(_text(wire['domain']), account_ref=_text(wire['account_ref']),
                     strategy_ref=_text(wire['strategy_ref']))
    if wire.account_ref != local.account_ref or wire.strategy_ref != local.strategy_id:
        raise ReadServiceError('INVALID_CONFIGURATION')
    storage = _map(top['storage'], ('journal_root', 'audit_root', 'storage_id'))
    root, audit = _path(storage['journal_root']), _path(storage['audit_root'])
    if root == audit or root in audit.parents or audit in root.parents:
        raise ReadServiceError('INVALID_CONFIGURATION')
    sid = _text(storage['storage_id'])
    if str(uuid.UUID(sid)) != sid:
        raise ReadServiceError('INVALID_CONFIGURATION')
    service = _identity(top['service_identity'], WireRole.SERVICE)
    client = _identity(top['client_identity'], WireRole.APPLICATION_CLIENT)
    if service.principal_ref == client.principal_ref:
        raise ReadServiceError('INVALID_CONFIGURATION')
    grant = _map(top['grant'], ('service_id', 'instance_id', 'scope_ref', 'revision', 'instruments', 'operations'))
    operations = grant['operations']
    if type(operations) is not list or len(operations) != len(OPERATIONS) or frozenset(operations) != OPERATIONS:
        raise ReadServiceError('INVALID_CONFIGURATION')
    items = grant['instruments']
    if type(items) is not list or not 1 <= len(items) <= 1024:
        raise ReadServiceError('INVALID_CONFIGURATION')
    instruments = frozenset(MarketInstrumentKey.from_dict(item) for item in items)
    if len(instruments) != len(items) or any(i.provider != local.provider or i.environment != local.environment for i in instruments):
        raise ReadServiceError('INVALID_CONFIGURATION')
    listener = _map(top['listener'], ('endpoint', 'server_public_key_file', 'server_secret_key_file', 'client_public_key_file'))
    return Config(local, wire, root, audit, sid, service, client,
                  *(_text(grant[k]) for k in ('service_id', 'instance_id', 'scope_ref', 'revision')),
                  instruments, _endpoint(listener['endpoint']), *(_path(listener[k]) for k in
                  ('server_public_key_file', 'server_secret_key_file', 'client_public_key_file')))


def load_config(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        return parse_config(json.loads(read_bounded(Path(path), 1024 * 1024, protected=False).decode('utf-8'),
                                       object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())))
    except Exception:
        raise ReadServiceError('INVALID_CONFIGURATION') from None


def _directory(path):
    if os.name == "nt":
        # Windows parity of the POSIX canonical-path + nofollow + private-owner
        # checks: refuse reparse points, require current-user ownership and no
        # other-writable ACE.  read_bounded uses the same owner/ACL semantics.
        from bt_api_py._win32_security import (
            Win32SecurityUnavailable,
            inspect_owner,
            is_reparse_point,
        )

        try:
            if path.resolve(strict=True) != path or is_reparse_point(path):
                raise ValueError
            owner = inspect_owner(path)
            if (not owner.is_current_user or owner.others_writable
                    or not stat.S_ISDIR(path.stat(follow_symlinks=False).st_mode)):
                raise ValueError
        except (OSError, ValueError, Win32SecurityUnavailable):
            raise ReadServiceError('INVALID_STORAGE') from None
        return
    if os.name != 'posix' or path.resolve(strict=True) != path:
        raise ReadServiceError('INVALID_STORAGE')
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ReadServiceError('INVALID_STORAGE')


def _check_existing(config, *, borrowed=None):
    """Reject an old/mismatched existing host before its constructor can migrate."""
    root = config.journal_root
    account_hash = hashlib.sha256(config.local_scope.account_key.encode()).hexdigest()
    marker = root / f'.execution-intake-{account_hash}.owner'
    database = root / f'.execution-journal-{account_hash}.sqlite3'
    if not marker.exists():
        # The host rejects orphaned or unrecognized entries before creating anything.
        return
    if os.name == "nt" and borrowed is not None:
        # On Windows, AccountJournalHost holds a mandatory byte-range lock on
        # marker via its own _owner_fd, which rejects os.read() on a second
        # handle even in the same process. Verify the marker's path/reparse/
        # owner/ACL/link-count directly and read the manifest via the already
        # verified borrowed owner handle.
        from bt_api_py._win32_security import (
            Win32SecurityUnavailable,
            inspect_owner,
            is_reparse_point,
        )

        try:
            minfo = marker.stat(follow_symlinks=False)
            mowner = inspect_owner(marker)
            if (is_reparse_point(marker) or not stat.S_ISREG(minfo.st_mode)
                    or not mowner.is_current_user or mowner.others_writable
                    or minfo.st_nlink != 1):
                raise ValueError
            data = borrowed._read_manifest()
        except (OSError, ValueError, Win32SecurityUnavailable):
            raise ReadServiceError('INVALID_STORAGE') from None
    else:
        data = json.loads(read_bounded(marker, 4096, protected=True))
    if (type(data) is not dict or data.get('storage_id') != config.storage_id
            or data.get('account') != account_hash or data.get('database') != database.name):
        raise ReadServiceError('INVALID_STORAGE')
    if os.name == "nt":
        # Windows parity: read_bounded(protected=True) already enforced the
        # marker's nofollow/owner/ACL invariants.  Check the database with the
        # same owner/ACL and reparse rules instead of uid/mode bits (Windows
        # st_uid is always 0); device/inode identity is still enforced.
        from bt_api_py._win32_security import (
            Win32SecurityUnavailable,
            inspect_owner,
            is_reparse_point,
        )

        try:
            info = database.stat(follow_symlinks=False)
            same_file = (info.st_dev, info.st_ino) == (data.get('device'), data.get('inode'))
            owner = inspect_owner(database)
            if (is_reparse_point(database) or not stat.S_ISREG(info.st_mode)
                    or not owner.is_current_user or owner.others_writable
                    or info.st_nlink != 1 or not same_file):
                raise ValueError
        except (OSError, ValueError, Win32SecurityUnavailable):
            raise ReadServiceError('INVALID_STORAGE') from None
    else:
        info = database.stat(follow_symlinks=False)
        root_info = root.stat(follow_symlinks=False)
        same_file = (
            (info.st_dev, info.st_ino) == (data.get('device'), data.get('inode'))
            or (sys.platform == 'darwin' and info.st_ino == data.get('inode') and info.st_dev == root_info.st_dev)
        )
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                or info.st_nlink != 1 or not same_file):
            raise ReadServiceError('INVALID_STORAGE')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute("SELECT value FROM execution_meta WHERE key='schema_version'").fetchone()
        if row != (str(SqliteExecutionStore._SCHEMA_VERSION),):
            raise ReadServiceError('INVALID_STORAGE')


def _keys(config):
    import zmq
    from zmq.utils import z85
    keys = [read_bounded(path, 4096, protected=True).strip() for path in
            (config.server_public, config.server_secret, config.client_public)]
    for key in keys:
        if len(key) != 40 or len(z85.decode(key)) != 32:
            raise ReadServiceError('INVALID_KEYS')
    if zmq.curve_public(keys[1]) != keys[0] or keys[0] == keys[2]:
        raise ReadServiceError('INVALID_KEYS')
    return keys


class ReadServiceHost:
    """Own a read listener; an optional running parent remains journal owner.

    Borrowing requires the exact parent owner because a bare account journal
    does not establish the parent strategy binding. Close never closes a borrow.
    """
    def __init__(self, config, *, borrowed_from=None):
        if type(config) is not Config:
            raise ReadServiceError('INVALID_CONFIGURATION')
        self.config = config
        self._borrowed_from = borrowed_from
        self.journal = None
        self.listener = None
        self.state = 'new'

    def _borrowed_journal(self):
        """Validate a running parent and its exact local journal/strategy binding."""
        from .execution_parent_service_host import ParentServiceHost

        owner, config = self._borrowed_from, self.config
        try:
            if (type(owner) is not ParentServiceHost or owner.state != 'running'
                    or owner.host is None or type(owner.journal) is not AccountJournalHost
                    or owner.config.local_scope != config.local_scope
                    or owner.config.journal_root != config.journal_root
                    or owner.config.storage_id != config.storage_id
                    or (owner.config.wire_scope.execution_domain_ref, owner.config.wire_scope.account_ref,
                        owner.config.wire_scope.strategy_ref) != (config.wire_scope.execution_domain_ref,
                        config.wire_scope.account_ref, config.wire_scope.strategy_ref)):
                raise ValueError
            journal = owner.journal
            services = owner.host._atomic_intake_services
            if (journal._pid != os.getpid() or journal._closed or journal._closing
                    or journal._root != str(config.journal_root)
                    or journal._storage_id != config.storage_id
                    or journal._account_hash != hashlib.sha256(config.local_scope.account_key.encode()).hexdigest()
                    or len(services) != 1 or services[0]._intake._host is not journal
                    or services[0]._intake._store is not journal._store
                    or len(services[0]._intake._routes) != 1
                    or next(iter(services[0]._intake._routes)) != owner.config.wire_scope
                    or next(iter(services[0]._intake._routes.values())).scope != config.local_scope):
                raise ValueError
            journal._verify_paths()
            return journal
        except Exception:
            raise ReadServiceError('INVALID_BORROWED_JOURNAL') from None

    def start(self):
        if self.state != 'new':
            raise ReadServiceError('INVALID_STATE')
        # A start attempt is single-use even if validation or construction fails.
        # Mark ownership uncertain before acquiring any resources.
        self.state = 'close_uncertain'
        try:
            config = self.config
            borrowed = self._borrowed_journal() if self._borrowed_from is not None else None
            _directory(config.journal_root)
            _directory(config.audit_root)
            _check_existing(config, borrowed=borrowed)
            public, secret, client_key = _keys(config)
            self.journal = borrowed if borrowed is not None else AccountJournalHost(
                root=str(config.journal_root), scope=config.local_scope, storage_id=config.storage_id)
            grant = ExecutionManagementReadGrant(config.instance_id, config.client_identity, config.wire_scope,
                                                  config.revision, config.instruments, OPERATIONS)
            self.listener = ExecutionManagementReadHost(TrustedManagementReadListener(
                host=self.journal, local_scope=config.local_scope, grant=grant, scope_ref=config.scope_ref,
                service_id=config.service_id, service_identity=config.service_identity, audit_root=config.audit_root,
                endpoint=config.endpoint, principals={client_key: config.client_identity.principal_ref},
                credentials=CurveServerCredentials(public, secret)))
            status = self.listener.start(timeout=10)
            if status.outcome != 'RUNNING':
                raise ReadServiceError('START_FAILED')
            self.state = 'running'
        except BaseException:
            self.close()
            raise

    def poll(self):
        if (self.state != 'running' or self.listener is None
                or self.listener.status().outcome != 'RUNNING'):
            raise ReadServiceError('LISTENER_UNAVAILABLE')

    def close(self):
        if self.state == 'closed':
            return
        self.state = 'close_uncertain'
        try:
            if self.listener is not None:
                status = self.listener.stop(timeout=10)
                if status.cleanup != 'CONFIRMED' and status.outcome != 'NOT_STARTED':
                    raise ReadServiceError('CLEANUP_UNKNOWN')
                self.listener = None
            if self.journal is not None:
                if self._borrowed_from is None:
                    self.journal.close()
                self.journal = None
        except BaseException:
            # Keep every unresolved object reachable for a same-owner retry.
            raise ReadServiceError('CLEANUP_UNKNOWN') from None
        self.state = 'closed'


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('validate', 'run'):
        sub.add_parser(command).add_argument('--config', required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == 'validate':
            print('EXECUTION_MANAGEMENT_CONFIG_VALID', flush=True)
            return 0
        host = ReadServiceHost(config)
        signals = 0
        failed = False

        def stop(_signum, _frame):
            nonlocal signals
            signals += 1

        previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            try:
                host.start()
                if not signals:
                    print('EXECUTION_MANAGEMENT_LISTENER_READY', flush=True)
                while not signals:
                    host.poll()
                    time.sleep(0.1)
            except Exception:
                failed = True
            finally:
                # Never abandon an uncertain owner: a subsequent signal asks
                # for another close on the original listener and journal.
                while True:
                    before = signals
                    try:
                        host.close()
                        break
                    except Exception:
                        failed = True
                        print('EXECUTION_MANAGEMENT_CLEANUP_PENDING', flush=True)
                        while signals == before:
                            time.sleep(0.1)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        if failed:
            print('EXECUTION_MANAGEMENT_FAILED', flush=True)
            return 1
        print('EXECUTION_MANAGEMENT_STOPPED', flush=True)
        return 0
    except Exception:
        print('EXECUTION_MANAGEMENT_FAILED', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
