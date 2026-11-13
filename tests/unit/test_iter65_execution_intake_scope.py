"""Closed Execution intake permit around synthetic local SQLite transactions."""
import os
import sqlite3
import threading
import time

import pytest

from bt_api_py.service_registry import ServiceDispatchAdmissionError
from tests.unit.test_iter65_execution_parent_registry_grant import fixture, call, SUBMIT, QUERY, _other_grant


def captured(*, operation=SUBMIT, system_clock=True):
    data = fixture(operation=operation, system_clock=system_clock, two=True)
    registry, _, identity, scope, grant, received, response = data
    assert response['ok'], response
    assert call(registry, identity, scope, response['result'], operation)[0]['ok']
    return data, received[-1][0].dispatch_admission


def journal():
    conn = sqlite3.connect(':memory:', isolation_level=None)
    conn.execute('CREATE TABLE parents (request_id TEXT PRIMARY KEY)')
    return conn


def test_sealed_check_has_no_registry_sql_clock_or_sidecar_in_journal(monkeypatch):
    data, permit = captured()
    registry = data[0]
    conn = journal()
    inside = [False]
    sql_inside, gate_inside, gate_calls = [], [], []
    registry._db.set_trace_callback(lambda sql: sql_inside.append(sql) if inside[0] else None)
    original_gate = registry._record_migration_write_attempt
    def gate(*, fact_kind):
        gate_calls.append(fact_kind)
        if inside[0]:
            gate_inside.append(fact_kind)
            raise AssertionError('sidecar was called under the journal lock')
        return original_gate(fact_kind=fact_kind)
    monkeypatch.setattr(registry, '_record_migration_write_attempt', gate)
    try:
        with permit.intake_scope() as check:
            conn.execute('BEGIN IMMEDIATE')
            try:
                inside[0] = True
                conn.execute('INSERT INTO parents VALUES (?)', ('request-a',))
                # This potentially reentrant policy routine is outside the
                # closed recheck and must not be called at commit.
                monkeypatch.setattr(registry, '_execution_parent_admission_error',
                    lambda *args: pytest.fail('reentered Registry authorization'))
                check()
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                inside[0] = False
        assert conn.execute('SELECT * FROM parents').fetchall() == [('request-a',)]
        assert sql_inside == [] and gate_inside == [] and gate_calls == ['dispatch']
    finally:
        registry._db.set_trace_callback(None)
        permit.close()
        registry.close()
        conn.close()


def test_untrusted_injected_clock_fails_before_journal_or_callback():
    data, permit = captured(system_clock=False)
    registry = data[0]
    conn = journal()
    try:
        with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_CLOCK_UNTRUSTED'):
            with permit.intake_scope():
                conn.execute('INSERT INTO parents VALUES (?)', ('must-not-run',))
        assert conn.execute('SELECT COUNT(*) FROM parents').fetchone()[0] == 0
    finally:
        permit.close()
        registry.close()
        conn.close()


def test_replacing_clock_in_journal_never_calls_it_and_rolls_back():
    data, permit = captured()
    registry = data[0]
    conn = journal()
    calls = []
    try:
        with permit.intake_scope() as check:
            conn.execute('BEGIN IMMEDIATE')
            try:
                conn.execute('INSERT INTO parents VALUES (?)', ('request-a',))
                registry._clock = lambda: calls.append(True) or time.time()
                with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
                    check()
            finally:
                conn.rollback()
                registry._clock = time.time
        assert calls == [] and conn.execute('SELECT COUNT(*) FROM parents').fetchone()[0] == 0
    finally:
        permit.close()
        registry.close()
        conn.close()


def test_sidecar_failure_prevents_journal_entry(monkeypatch):
    data, permit = captured()
    registry = data[0]
    conn = journal()
    entered = []
    def fail(*, fact_kind):
        raise OSError('synthetic marker failure')
    monkeypatch.setattr(registry, '_record_migration_write_attempt', fail)
    try:
        with pytest.raises(ServiceDispatchAdmissionError, match='UNAVAILABLE'):
            with permit.intake_scope():
                entered.append(True)
                conn.execute('INSERT INTO parents VALUES (?)', ('must-not-run',))
        assert entered == [] and conn.execute('SELECT COUNT(*) FROM parents').fetchone()[0] == 0
    finally:
        permit.close()
        registry.close()
        conn.close()


def test_query_scope_does_not_record_migration_write(monkeypatch):
    data, permit = captured(operation=QUERY)
    registry = data[0]
    monkeypatch.setattr(registry, '_record_migration_write_attempt', lambda **_: pytest.fail('query called write gate'))
    try:
        with permit.intake_scope() as check:
            check()
    finally:
        permit.close()
        registry.close()


def test_concurrent_revoke_waits_until_transaction_and_scope_exit():
    data, permit = captured()
    registry, _, _, _, grant = data[:5]
    conn = journal()
    attempted, done = threading.Event(), threading.Event()
    def revoke():
        attempted.set()
        assert registry.revoke_execution_parent(grant)
        done.set()
    thread = threading.Thread(target=revoke)
    try:
        with permit.intake_scope() as check:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT INTO parents VALUES (?)', ('request-a',))
            thread.start()
            assert attempted.wait(2) and not done.wait(.05)
            check()
            conn.commit()
        thread.join(2)
        assert done.is_set() and not thread.is_alive()
        assert conn.execute('SELECT * FROM parents').fetchall() == [('request-a',)]
        with pytest.raises(ServiceDispatchAdmissionError):
            with permit.intake_scope():
                pass
    finally:
        if thread.is_alive():
            thread.join(2)
        permit.close()
        registry.close()
        conn.close()


def test_same_thread_reentrant_mutation_rejected_before_change():
    data, permit = captured()
    registry, _, _, _, grant = data[:5]
    conn = journal()
    try:
        with permit.intake_scope() as check:
            conn.execute('BEGIN IMMEDIATE')
            try:
                conn.execute('INSERT INTO parents VALUES (?)', ('request-a',))
                with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_REENTRANCY'):
                    registry.revoke_execution_parent(grant)
                with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_REENTRANCY'):
                    registry.set_ready(False)
                with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_REENTRANCY'):
                    registry.close()
                with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_REENTRANCY'):
                    registry._now()
                with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_REENTRANCY'):
                    registry._record_migration_write_attempt(fact_kind='dispatch')
                with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_REENTRANCY'):
                    registry.configure_gateway_parents(instance_id='a', scope_ref='a', grants=())
                with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_REENTRANCY'):
                    with registry._read_snapshot():
                        pass
                check()
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        assert conn.execute('SELECT * FROM parents').fetchall() == [('request-a',)]
        assert registry.revoke_execution_parent(grant)
    finally:
        permit.close()
        registry.close()
        conn.close()


def test_close_during_transaction_invalidates_recheck_and_rolls_back():
    data, permit = captured()
    registry = data[0]
    conn = journal()
    try:
        with permit.intake_scope() as check:
            conn.execute('BEGIN IMMEDIATE')
            try:
                conn.execute('INSERT INTO parents VALUES (?)', ('request-a',))
                thread = threading.Thread(target=permit.close)
                thread.start()
                thread.join(2)
                assert not thread.is_alive()
                with pytest.raises(ServiceDispatchAdmissionError, match='ADMISSION_CLOSED'):
                    check()
            finally:
                conn.rollback()
        assert conn.execute('SELECT COUNT(*) FROM parents').fetchone()[0] == 0
    finally:
        permit.close()
        registry.close()
        conn.close()


def test_expiry_during_transaction_rolls_back():
    data, permit = captured()
    registry, _, _, _, _, received = data[:6]
    registration = received[-1][0].registration_id
    conn = journal()
    try:
        with registry._transaction():
            registry._db.execute('UPDATE sessions SET expires_at=? WHERE registration_id=?',
                                 (time.time() + .5, registration))
        with permit.intake_scope() as check:
            conn.execute('BEGIN IMMEDIATE')
            try:
                conn.execute('INSERT INTO parents VALUES (?)', ('request-a',))
                time.sleep(.6)
                with pytest.raises(ServiceDispatchAdmissionError, match='NOT_REGISTERED'):
                    check()
            finally:
                conn.rollback()
        assert conn.execute('SELECT COUNT(*) FROM parents').fetchone()[0] == 0
    finally:
        permit.close()
        registry.close()
        conn.close()


def test_same_key_reinstall_cannot_revive_but_other_grant_does_not_invalidate():
    data, permit = captured()
    registry, _, _, _, grant = data[:5]
    try:
        _, _, other = _other_grant()
        registry.configure_execution_parents(grants=(other,))
        with permit.intake_scope() as check:
            check()
        assert registry.revoke_execution_parent(other)
        with permit.intake_scope() as check:
            check()
        assert registry.revoke_execution_parent(grant)
        registry.configure_execution_parents(grants=(grant,))
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            with permit.intake_scope():
                pass
    finally:
        permit.close()
        registry.close()


def test_same_scope_reentry_and_foreign_thread_recheck_fail():
    data, permit = captured()
    registry = data[0]
    errors = []
    try:
        with permit.intake_scope() as check:
            with pytest.raises(ServiceDispatchAdmissionError, match='ADMISSION_BUSY'):
                with permit.intake_scope():
                    pass
            def foreign():
                try:
                    check()
                except ServiceDispatchAdmissionError as exc:
                    errors.append(exc.code)
            thread = threading.Thread(target=foreign)
            thread.start()
            thread.join(2)
            assert errors == ['ADMISSION_CLOSED']
    finally:
        permit.close()
        registry.close()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='fork is unavailable')
def test_forked_process_cannot_recheck_inherited_permit():
    data, permit = captured()
    registry = data[0]
    read_fd, write_fd = os.pipe()
    child = None
    try:
        with permit.intake_scope() as check:
            child = os.fork()
            if child == 0:
                try:
                    os.close(read_fd)
                    try:
                        check()
                        code = 'unexpected-success'
                    except ServiceDispatchAdmissionError as exc:
                        code = exc.code
                    os.write(write_fd, code.encode('ascii'))
                finally:
                    os._exit(0)
            os.close(write_fd)
            write_fd = None
            assert os.read(read_fd, 100) == b'WRONG_PROCESS'
            assert os.waitpid(child, 0)[1] == 0
            child = None
            check()
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if child is not None:
            os.waitpid(child, 0)
        permit.close()
        registry.close()


def test_foreign_thread_cannot_unwind_owner_scope():
    data, permit = captured()
    registry = data[0]
    context = permit.intake_scope()
    errors = []
    check = context.__enter__()
    try:
        def foreign():
            try:
                context.__exit__(None, None, None)
            except ServiceDispatchAdmissionError as exc:
                errors.append(exc.code)
        thread = threading.Thread(target=foreign)
        thread.start()
        thread.join(2)
        assert not thread.is_alive() and errors == ['WRONG_OWNER_THREAD']
        check()
    finally:
        context.__exit__(None, None, None)
        permit.close()
        registry.close()


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='fork is unavailable')
def test_forked_scope_unwind_never_touches_inherited_sqlite():
    data, permit = captured()
    registry = data[0]
    context = permit.intake_scope()
    check = context.__enter__()
    read_fd, write_fd = os.pipe()
    child = None
    try:
        # Any SQL in the child would emit a distinct sentinel on the pipe.
        registry._db.set_trace_callback(lambda _sql: os.write(write_fd, b'BAD_SQL;'))
        child = os.fork()
        if child == 0:
            try:
                os.close(read_fd)
                try:
                    context.__exit__(None, None, None)
                    result = b'UNEXPECTED;'
                except ServiceDispatchAdmissionError as exc:
                    result = (exc.code + ';').encode('ascii')
                # Simulate child-side generator finalization after bad exit.
                context._manager.gen.close()
                os.write(write_fd, result)
            finally:
                os._exit(0)
        os.close(write_fd)
        write_fd = None
        assert os.read(read_fd, 100) == b'WRONG_PROCESS;'
        assert os.waitpid(child, 0)[1] == 0
        child = None
        registry._db.set_trace_callback(None)
        check()
    finally:
        registry._db.set_trace_callback(None)
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if child is not None:
            os.waitpid(child, 0)
        context.__exit__(None, None, None)
        permit.close()
        registry.close()


def test_gate_callback_revocation_is_rechecked_before_journal(monkeypatch):
    data, permit = captured()
    registry, _, _, _, grant = data[:5]
    conn = journal()
    def revoke_during_gate(*, fact_kind):
        assert registry.revoke_execution_parent(grant)
        return False
    monkeypatch.setattr(registry, '_record_migration_write_attempt', revoke_during_gate)
    try:
        with pytest.raises(ServiceDispatchAdmissionError, match='SCOPE_DENIED'):
            with permit.intake_scope():
                conn.execute('INSERT INTO parents VALUES (?)', ('must-not-run',))
        assert conn.execute('SELECT COUNT(*) FROM parents').fetchone()[0] == 0
    finally:
        permit.close()
        registry.close()
        conn.close()


def test_fake_replacement_of_captured_builtin_clock_is_refused(monkeypatch):
    import bt_api_py.service_registry as registry_module
    data, permit = captured()
    registry = data[0]
    calls = []
    def fake():
        calls.append(True)
        return 100.0
    try:
        monkeypatch.setattr(registry_module, '_INTAKE_REALTIME', fake)
        registry._clock = fake
        with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_CLOCK_UNTRUSTED'):
            with permit.intake_scope():
                pass
        assert calls == []
    finally:
        registry._clock = time.time
        permit.close()
        registry.close()


def test_real_migration_gate_completes_before_local_journal(tmp_path, monkeypatch):
    from bt_api_py.service_migration_gate import read_migration_marker
    path = tmp_path / 'registry.sqlite'
    data = fixture(path=path, migration=True, system_clock=True)
    registry, _, identity, scope, _, received, response = data
    assert call(registry, identity, scope, response['result'])[0]['ok']
    permit = received[-1][0].dispatch_admission
    conn = journal()
    inside, observed = [False], []
    original = registry._record_migration_write_attempt
    def record(*, fact_kind):
        assert not inside[0]
        result = original(fact_kind=fact_kind)
        observed.append((fact_kind, read_migration_marker(path).status))
        return result
    monkeypatch.setattr(registry, '_record_migration_write_attempt', record)
    try:
        with permit.intake_scope() as check:
            conn.execute('BEGIN IMMEDIATE')
            try:
                inside[0] = True
                conn.execute('INSERT INTO parents VALUES (?)', ('request-a',))
                check()
                conn.commit()
            finally:
                inside[0] = False
        assert observed == [('dispatch', 'SEALED')]
        assert conn.execute('SELECT * FROM parents').fetchall() == [('request-a',)]
    finally:
        permit.close()
        registry.close()
        conn.close()


def test_real_migration_sidecar_competing_lock_exits_bounded_before_journal(tmp_path, monkeypatch):
    import bt_api_py.service_migration_gate as gate
    path = tmp_path / 'registry.sqlite'
    data = fixture(path=path, migration=True, system_clock=True)
    registry, _, identity, scope, _, received, response = data
    assert call(registry, identity, scope, response['result'])[0]['ok']
    permit = received[-1][0].dispatch_admission
    conn = journal()
    held, request_registry, finished = threading.Event(), threading.Event(), threading.Event()
    original = registry._record_migration_write_attempt
    monkeypatch.setattr(gate, '_MIGRATION_LOCK_TIMEOUT_SECONDS', .2)
    def record(*, fact_kind):
        request_registry.set()
        return original(fact_kind=fact_kind)
    monkeypatch.setattr(registry, '_record_migration_write_attempt', record)
    def other_host():
        with gate.migration_start_guard(path, deployment_epoch='deploy-current'):
            held.set()
            assert request_registry.wait(2)
            registry.set_ready(False)
        finished.set()
    thread = threading.Thread(target=other_host)
    thread.start()
    try:
        assert held.wait(2)
        started = time.monotonic()
        with pytest.raises(ServiceDispatchAdmissionError, match='UNAVAILABLE'):
            with permit.intake_scope():
                conn.execute('BEGIN IMMEDIATE')
                conn.execute('INSERT INTO parents VALUES (?)', ('must-not-run',))
                conn.commit()
        assert time.monotonic() - started < 2
        thread.join(2)
        assert finished.is_set() and not thread.is_alive()
        assert conn.execute('SELECT COUNT(*) FROM parents').fetchone()[0] == 0
    finally:
        request_registry.set()
        thread.join(2)
        permit.close()
        registry.close()
        conn.close()


def test_journal_failure_propagates_and_rolls_back_both_scopes():
    data, permit = captured()
    registry = data[0]
    conn = journal()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with permit.intake_scope() as check:
                conn.execute('BEGIN IMMEDIATE')
                try:
                    conn.execute('INSERT INTO parents VALUES (?)', ('duplicate',))
                    conn.execute('INSERT INTO parents VALUES (?)', ('duplicate',))
                    check()
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
        assert conn.execute('SELECT COUNT(*) FROM parents').fetchone()[0] == 0
        # Registry snapshot and permit can be correctly re-entered on owner.
        with permit.intake_scope() as check:
            check()
    finally:
        permit.close()
        registry.close()
        conn.close()


def test_final_check_uses_captured_functions_not_replaced_globals(monkeypatch):
    import bt_api_py.service_registry as registry_module
    data, permit = captured()
    registry = data[0]
    conn = journal()
    calls = []
    def injected():
        calls.append(True)
        raise AssertionError('injected callback entered final check')
    try:
        with permit.intake_scope() as check:
            conn.execute('BEGIN IMMEDIATE')
            try:
                conn.execute('INSERT INTO parents VALUES (?)', ('request-a',))
                with monkeypatch.context() as patcher:
                    patcher.setattr(registry_module, '_INTAKE_REALTIME', injected)
                    patcher.setattr(registry_module, '_INTAKE_MONOTONIC', injected)
                    patcher.setattr(registry_module, '_INTAKE_GETPID', injected)
                    patcher.setattr(registry_module, '_INTAKE_CURRENT_THREAD', injected)
                    patcher.setattr(registry_module, '_INTAKE_GET_IDENT', injected)
                    check()
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        assert calls == [] and conn.execute('SELECT * FROM parents').fetchall() == [('request-a',)]
    finally:
        permit.close()
        registry.close()
        conn.close()


@pytest.mark.parametrize('name', ['_INTAKE_GETPID', '_INTAKE_GET_IDENT', '_INTAKE_CURRENT_THREAD'])
def test_fake_runtime_function_is_refused_before_journal(name, monkeypatch):
    import bt_api_py.service_registry as registry_module
    data, permit = captured()
    registry = data[0]
    calls = []
    def fake():
        calls.append(True)
        return os.getpid()
    try:
        monkeypatch.setattr(registry_module, name, fake)
        with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_RUNTIME_UNTRUSTED'):
            with permit.intake_scope():
                pytest.fail('untrusted runtime function entered intake')
        # The context wrapper may inspect pid before entering, but the closed
        # intake rejects before yielding or taking the caller's journal lock.
        assert len(calls) <= 1
    finally:
        # Restore before permit/Registry cleanup.
        monkeypatch.undo()
        permit.close()
        registry.close()


def test_global_function_replacements_through_scope_exit_release_resources(monkeypatch):
    import bt_api_py.service_registry as registry_module
    data, permit = captured()
    registry = data[0]
    conn = journal()
    calls = []
    def injected():
        calls.append(True)
        raise AssertionError('injected global used during check or cleanup')
    context = permit.intake_scope()
    check = context.__enter__()
    try:
        with monkeypatch.context() as patcher:
            for name in ('_INTAKE_REALTIME', '_INTAKE_MONOTONIC', '_INTAKE_GETPID',
                         '_INTAKE_CURRENT_THREAD', '_INTAKE_GET_IDENT'):
                patcher.setattr(registry_module, name, injected)
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT INTO parents VALUES (?)', ('request-a',))
            with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_REENTRANCY'):
                registry.set_ready(False)
            with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_REENTRANCY'):
                registry._now()
            with pytest.raises(ServiceDispatchAdmissionError, match='INTAKE_REENTRANCY'):
                registry._record_migration_write_attempt(fact_kind='dispatch')
            check()
            conn.commit()
            context.__exit__(None, None, None)
        assert calls == [] and not registry._db.in_transaction
        with registry._read_snapshot():
            assert registry._db.execute('SELECT 1').fetchone()[0] == 1
        assert conn.execute('SELECT * FROM parents').fetchall() == [('request-a',)]
    finally:
        if registry._db.in_transaction:
            context.__exit__(None, None, None)
        permit.close()
        registry.close()
        conn.close()
