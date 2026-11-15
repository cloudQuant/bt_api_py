"""Local-only management read audit; no service, provider or account access."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import os
import sqlite3
import threading

import pytest

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_execution.local_parent_snapshot import LocalParentSnapshot
from bt_api_py.execution_management_audit import (
    ExecutionManagementAuditError, ExecutionManagementReadAudit, ManagementReadIdentity,
)
from bt_api_py.execution_parent_grant import SUBMIT
from bt_api_execution.local_parent_snapshot import LocalParentSnapshotReader
from test_iter65_atomic_submission_intake import atomic


@pytest.fixture
def identity():
    return ManagementReadIdentity('service-instance', 'epoch-1', 'application_client',
                                  'principal', 'actor', 'account', 'strategy', 'domain', 'policy-1')


def observed(parent='parent-1', quantity='3'):
    return LocalParentSnapshot(
        'OBSERVED', journal_incarnation='a' * 32, parent_ref=parent,
        instrument=MarketInstrumentKey('ctp', 'SHFE', 'simulation', 'future', 'rb2610', 'rb2610'),
        quantity=quantity, intake_local_state='RECEIVED', risk_local_state='UNKNOWN',
        gateway_local_state='UNKNOWN', cancel_local_state='UNKNOWN',
    )


def record(audit, identity, *, query_id='query-1', parent='parent-1', snapshot=None):
    return audit.disclose_after_audit(identity=identity, query_id=query_id, parent_ref=parent,
                                      operation_version=1, snapshot=snapshot or observed(parent))


def rows(root):
    with sqlite3.connect(str(root / 'execution-management-read.sqlite3')) as db:
        return db.execute('SELECT query_id, policy_revision, outcome, journal_incarnation, '
                          'result_digest FROM audit_attempts ORDER BY sequence').fetchall()


def test_repeated_read_appends_attempt_and_never_caches_prior_snapshot(tmp_path, identity):
    journal = ExecutionManagementReadAudit(tmp_path)
    first, second = observed(quantity='3'), observed(quantity='4')
    assert record(journal, identity, snapshot=first) is first
    assert record(journal, replace(identity, policy_revision='policy-2'), snapshot=second) is second
    saved = rows(tmp_path)
    assert len(saved) == 2
    assert [r[:4] for r in saved] == [('query-1', 'policy-1', 'OBSERVED', 'a' * 32),
                                       ('query-1', 'policy-2', 'OBSERVED', 'a' * 32)]
    assert saved[0][4] != saved[1][4]
    journal.close()
    journal = ExecutionManagementReadAudit(tmp_path)
    assert record(journal, identity, snapshot=LocalParentSnapshot('UNKNOWN')).status == 'UNKNOWN'
    assert rows(tmp_path)[-1][2:4] == ('UNKNOWN', None)
    journal.close()


def test_actual_local_snapshot_is_audited_without_writing_account_journal(tmp_path, identity, atomic):
    rig, store, _ = atomic
    request = rig.parent()
    assert rig.call(SUBMIT, request)['ok']
    before = store._connection.total_changes
    reader = LocalParentSnapshotReader(host=rig.host, scope=rig.target)
    journal = ExecutionManagementReadAudit(tmp_path)
    try:
        snapshot = reader.query(request['spec']['parent_ref'])
        scoped_identity = replace(identity, account_ref=request['spec']['account_scope'],
                                  strategy_ref=request['spec']['strategy_scope'],
                                  domain_ref=request['spec']['execution_domain_ref'])
        assert snapshot.status == 'OBSERVED'
        assert record(journal, scoped_identity, parent=snapshot.parent_ref, snapshot=snapshot) is snapshot
        assert store._connection.total_changes == before
        assert rows(tmp_path)[0][2:4] == ('OBSERVED', snapshot.journal_incarnation)
    finally:
        journal.close()
        reader.close()


def test_correlation_conflict_is_scoped_and_does_not_append(tmp_path, identity):
    journal = ExecutionManagementReadAudit(tmp_path)
    record(journal, identity)
    with pytest.raises(ExecutionManagementAuditError, match='^QUERY_ID_CONFLICT$'):
        record(journal, identity, parent='different-parent')
    assert len(rows(tmp_path)) == 1
    # An independently authenticated principal/scope has its own namespace.
    other = replace(identity, principal_ref='other-principal', strategy_ref='other-strategy')
    record(journal, other, parent='different-parent')
    assert len(rows(tmp_path)) == 2
    journal.close()


def test_service_instance_and_role_are_recorded_with_domain_separated_target(tmp_path, identity):
    journal = ExecutionManagementReadAudit(tmp_path)
    record(journal, identity)
    other = replace(identity, service_instance='second-instance', service_epoch='epoch-2', caller_role='service')
    record(journal, other, parent='different-parent')
    # Epoch changes are new attempts for the same request, not a new ID scope.
    record(journal, replace(identity, service_epoch='epoch-3'))
    with pytest.raises(ExecutionManagementAuditError, match='^QUERY_ID_CONFLICT$'):
        record(journal, replace(identity, service_epoch='epoch-3'), parent='different-parent')
    with sqlite3.connect(str(tmp_path / 'execution-management-read.sqlite3')) as db:
        saved = db.execute('SELECT service_instance,service_epoch,caller_role,target_digest '
                           'FROM audit_attempts ORDER BY sequence').fetchall()
        assert [row[:3] for row in saved] == [
            ('service-instance', 'epoch-1', 'application_client'),
            ('second-instance', 'epoch-2', 'service'),
            ('service-instance', 'epoch-3', 'application_client'),
        ]
        assert len(saved[0][3]) == 64 and saved[0][3] == saved[2][3] != saved[1][3]
        assert 'parent-1' not in repr(saved)
    journal.close()


def test_capacity_failure_keeps_prior_attempt_and_returns_no_snapshot(tmp_path, identity):
    journal = ExecutionManagementReadAudit(tmp_path, max_attempts=1)
    record(journal, identity)
    published = []
    with pytest.raises(ExecutionManagementAuditError, match='^AUDIT_CAPACITY$'):
        published.append(record(journal, identity))
    assert published == [] and len(rows(tmp_path)) == 1
    journal.close()
    with pytest.raises(ExecutionManagementAuditError, match='^AUDIT_UNAVAILABLE$'):
        ExecutionManagementReadAudit(tmp_path, max_attempts=2)


def test_commit_reply_lost_does_not_publish_and_retry_records_new_attempt(tmp_path, identity, monkeypatch):
    journal = ExecutionManagementReadAudit(tmp_path)
    original = journal._connect

    class LostReply:
        def __init__(self, db):
            self.db = db

        def execute(self, statement, *args):
            result = self.db.execute(statement, *args)
            if statement == 'COMMIT':
                raise sqlite3.OperationalError('synthetic lost commit reply')
            return result

        @property
        def in_transaction(self):
            return self.db.in_transaction

    @contextmanager
    def uncertain():
        with original() as connection:
            yield LostReply(connection)

    monkeypatch.setattr(journal, '_connect', uncertain)
    published = []
    with pytest.raises(ExecutionManagementAuditError, match='^AUDIT_UNAVAILABLE$'):
        published.append(record(journal, identity))
    assert published == [] and len(rows(tmp_path)) == 1
    monkeypatch.setattr(journal, '_connect', original)
    record(journal, identity)
    assert len(rows(tmp_path)) == 2
    journal.close()


def test_failure_before_commit_rolls_back_request_and_attempt(tmp_path, identity, monkeypatch):
    journal = ExecutionManagementReadAudit(tmp_path)
    original = journal._connect

    class FailedInsert:
        def __init__(self, db):
            self.db = db

        def execute(self, statement, *args):
            if statement.startswith('INSERT INTO audit_attempts'):
                raise sqlite3.OperationalError('synthetic write failure')
            return self.db.execute(statement, *args)

        @property
        def in_transaction(self):
            return self.db.in_transaction

    @contextmanager
    def broken():
        with original() as connection:
            yield FailedInsert(connection)

    monkeypatch.setattr(journal, '_connect', broken)
    with pytest.raises(ExecutionManagementAuditError, match='^AUDIT_UNAVAILABLE$'):
        record(journal, identity)
    with sqlite3.connect(str(tmp_path / 'execution-management-read.sqlite3')) as db:
        assert db.execute('SELECT count(*) FROM audit_requests').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM audit_attempts').fetchone()[0] == 0
    journal.close()


def test_concurrent_handles_serialize_attempts_and_detect_same_id_conflict(tmp_path, identity):
    bootstrap = ExecutionManagementReadAudit(tmp_path)
    bootstrap.close()
    barrier = threading.Barrier(6)

    def invoke(index):
        journal = ExecutionManagementReadAudit(tmp_path)
        try:
            barrier.wait(timeout=5)
            record(journal, identity, query_id='same', parent='parent-1' if index < 5 else 'other')
            return 'ok'
        except ExecutionManagementAuditError as error:
            return str(error)
        finally:
            journal.close()

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(invoke, range(6)))
    # Whichever parent wins establishes the content; no mixed content is kept.
    assert set(outcomes) <= {'ok', 'QUERY_ID_CONFLICT'}
    assert outcomes.count('ok') in (1, 5)
    assert len(rows(tmp_path)) == outcomes.count('ok')


def test_private_root_permissions_symlink_and_file_replacement_fail_closed(tmp_path, identity):
    bad = tmp_path / 'public'
    bad.mkdir(mode=0o755)
    bad.chmod(0o755)
    with pytest.raises(ExecutionManagementAuditError):
        ExecutionManagementReadAudit(bad)
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    link = tmp_path / 'link'
    link.symlink_to(root)
    with pytest.raises(ExecutionManagementAuditError):
        ExecutionManagementReadAudit(link)
    journal = ExecutionManagementReadAudit(root)
    record(journal, identity)
    path = root / 'execution-management-read.sqlite3'
    path.rename(root / 'original.sqlite3')
    path.write_bytes(b'not a database')
    path.chmod(0o600)
    with pytest.raises(ExecutionManagementAuditError, match='^AUDIT_UNAVAILABLE$'):
        record(journal, identity)
    journal.close()


def test_existing_unrecognized_database_and_schema_change_are_not_migrated(tmp_path, identity):
    path = tmp_path / 'execution-management-read.sqlite3'
    path.touch(mode=0o600)
    with pytest.raises(ExecutionManagementAuditError, match='^AUDIT_UNAVAILABLE$'):
        ExecutionManagementReadAudit(tmp_path)
    path.unlink()
    journal = ExecutionManagementReadAudit(tmp_path)
    record(journal, identity)
    with sqlite3.connect(str(path)) as db:
        db.execute('CREATE TABLE unexpected (value TEXT)')
    with pytest.raises(ExecutionManagementAuditError, match='^AUDIT_UNAVAILABLE$'):
        record(journal, identity)
    journal.close()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='fork unavailable')
def test_inherited_handle_cannot_write_or_close_in_child(tmp_path, identity):
    journal = ExecutionManagementReadAudit(tmp_path)
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        results = []
        for action in (lambda: record(journal, identity), journal.close):
            try:
                action()
                results.append('unexpected')
            except ExecutionManagementAuditError as error:
                results.append(str(error))
        os.write(write_fd, ','.join(results).encode())
        os.close(write_fd)
        os._exit(0)
    os.close(write_fd)
    child_result = os.read(read_fd, 1024).decode()
    os.close(read_fd)
    _, status = os.waitpid(pid, 0)
    assert status == 0 and child_result == 'AUDIT_UNAVAILABLE,AUDIT_UNAVAILABLE'
    record(journal, identity)
    assert len(rows(tmp_path)) == 1
    journal.close()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='fork unavailable')
def test_separate_processes_bind_same_query_id_atomically(tmp_path, identity):
    ExecutionManagementReadAudit(tmp_path).close()
    start_read, start_write = os.pipe()
    result_read, result_write = os.pipe()
    children = []
    for parent in ('parent-1', 'different-parent'):
        pid = os.fork()
        if pid == 0:
            os.close(start_write)
            os.close(result_read)
            try:
                journal = ExecutionManagementReadAudit(tmp_path)
                os.read(start_read, 1)
                record(journal, identity, parent=parent)
                result = 'ok'
            except ExecutionManagementAuditError as error:
                result = str(error)
            os.write(result_write, (result + '\n').encode())
            os._exit(0)
        children.append(pid)
    os.close(start_read)
    os.close(result_write)
    os.write(start_write, b'xx')
    os.close(start_write)
    output = b''
    while True:
        block = os.read(result_read, 1024)
        if not block:
            break
        output += block
    os.close(result_read)
    for pid in children:
        assert os.waitpid(pid, 0)[1] == 0
    assert sorted(output.decode().splitlines()) == ['QUERY_ID_CONFLICT', 'ok']
    assert len(rows(tmp_path)) == 1


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='fork unavailable')
def test_process_exit_before_commit_rolls_back_without_disclosure(tmp_path, identity):
    ExecutionManagementReadAudit(tmp_path).close()
    pid = os.fork()
    if pid == 0:
        journal = ExecutionManagementReadAudit(tmp_path)
        original = journal._connect

        class ExitAfterInsert:
            def __init__(self, db):
                self.db = db

            def execute(self, statement, *args):
                result = self.db.execute(statement, *args)
                if statement.startswith('INSERT INTO audit_attempts'):
                    os._exit(61)
                return result

            @property
            def in_transaction(self):
                return self.db.in_transaction

        @contextmanager
        def terminating():
            with original() as connection:
                yield ExitAfterInsert(connection)

        journal._connect = terminating
        record(journal, identity)
        os._exit(1)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 61
    assert rows(tmp_path) == []
    journal = ExecutionManagementReadAudit(tmp_path)
    record(journal, identity)
    assert len(rows(tmp_path)) == 1
    journal.close()


def test_only_local_verified_shape_can_be_disclosed(tmp_path, identity):
    journal = ExecutionManagementReadAudit(tmp_path)
    invalid = [replace(observed(), parent_ref='different'),
               replace(observed(), gateway_local_state='ACCEPTED'),
               replace(observed(), journal_incarnation='invalid'),
               replace(LocalParentSnapshot('UNKNOWN'), parent_ref='parent-1')]
    for snapshot in invalid:
        with pytest.raises(ExecutionManagementAuditError, match='^INVALID_AUDIT_INPUT$'):
            record(journal, identity, snapshot=snapshot)
    assert rows(tmp_path) == []
    journal.close()
    with pytest.raises(ExecutionManagementAuditError, match='^AUDIT_UNAVAILABLE$'):
        record(journal, identity)
