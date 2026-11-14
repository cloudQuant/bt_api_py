"""Registry-issued control reads stay read-only across the migration gate."""

from contextlib import contextmanager
from types import MethodType, SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

import bt_api_py.service_registry as registry_module
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceDispatchAdmission,
    ServiceDispatchAdmissionError,
    ServiceRegistry,
)
from tests.unit.test_iter64_gateway_strategy_history_read_fake import historical_control
from tests.unit.test_service_registry import _next_request, _register

READS = ('direct.control.identity', 'direct.control.cancel_scope_status')
WRITE = 'direct.control.cancel_scope'


class FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def acquire(self, *_args, **_kwargs):
        return True

    def release(self):
        pass


@pytest.fixture
def registry_and_binding(monkeypatch):
    # The shared setup uses fake admission methods for its local Gateway
    # tests. Restore the actual methods before Registry dispatch in this test.
    names = ('matches', 'matches_context', 'matches_operation', 'close', 'scope')
    original = {name: getattr(ServiceDispatchAdmission, name) for name in names}
    with historical_control(monkeypatch) as (binding, _, _, db, _, _):
        journal = binding._journal
        journal._account, journal._journal_id, journal._minimum_revision = 'account', 'journal', 0
        journal._schema_version, journal._max_records = 17, 100
        journal._execution_max_facts = 10
        db.execute('CREATE TABLE direct_journal_meta '
                   '(account TEXT, schema_version INTEGER, max_records INTEGER, journal_id TEXT, revision INTEGER)')
        db.execute('INSERT INTO direct_journal_meta VALUES (?, ?, ?, ?, ?)', ('account', 17, 100, 'journal', 1))
        db.execute('CREATE TABLE direct_execution_meta (account TEXT, max_facts INTEGER)')
        db.execute('INSERT INTO direct_execution_meta VALUES (?, ?)', ('account', 10))
        journal._assert_anchor = MethodType(GatewayDirectCommandJournal._assert_anchor, journal)

        @contextmanager
        def anchored_transaction():
            journal._assert_anchor(db)
            yield db

        journal._transaction = anchored_transaction
        for name, method in original.items():
            monkeypatch.setattr(ServiceDispatchAdmission, name, method)
        thread = object()
        monkeypatch.setattr(registry_module, 'threading', SimpleNamespace(
            Lock=FakeLock, RLock=FakeLock, current_thread=lambda: thread,
        ))
        identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'risk-principal', 'actor')
        scope = WireScope('domain', account_ref='account')
        operations = frozenset((*READS, WRITE))
        capability = ServiceCapability('gateway-a', 'service.direct_write', operations, frozenset({WRITE}))
        resource = ResourceGrant('gateway-a', 'service.direct_write', 'scope-a', scope, operations)
        registry = ServiceRegistry(
            ':memory:', service_id='gateway-service', capabilities=(capability,),
            grants={'risk-principal': (PrincipalGrant(identity, (resource,)),)},
            policy_version='policy', requirements_version='requirements', lease_seconds=10, clock=lambda: 100.0,
        )
        try:
            registry._db.execute('PRAGMA temp_store=MEMORY')
            assert registry._db.execute('PRAGMA temp_store').fetchone()[0] == 2
            assert registry._db.execute('PRAGMA journal_mode').fetchone()[0] == 'memory'
            assert registry._db.execute('PRAGMA database_list').fetchone()[2] == ''
            # The real binding grant contains status/cancel; add identity as
            # a separate, explicitly granted read operation.
            from dataclasses import replace
            grant = binding._control_grants['risk-principal']
            binding._control_grants['risk-principal'] = replace(grant, operations=grant.operations | set(READS))
            for operation in READS:
                registry.register_handler('gateway-a', operation,
                                          lambda ctx, args, op=operation: binding.handle(op, context=ctx, arguments=args))
            write_calls = []

            def fake_write(context, arguments):
                write_calls.append(arguments)
                with context.dispatch_admission.scope() as recheck:
                    recheck()
                return {'status': 'OBSERVED'}

            registry.register_handler('gateway-a', WRITE, fake_write)
            registered = _register(registry, principal='risk-principal', identity=identity, scope=scope,
                                   instance_id='gateway-a', port='service.direct_write', scope_ref='scope-a',
                                   operations=tuple(operations))
            assert registered['ok'], registered
            yield registry, identity, scope, registered['result'], binding, db, write_calls
        finally:
            registry.close()


def dispatch(registry, identity, scope, registration, operation, arguments):
    request = _next_request(operation, {
        'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
        'instance_id': 'gateway-a', 'arguments': arguments,
    }, identity=identity, scope=scope)
    return registry.handle('risk-principal', request)


def read_arguments(operation):
    if operation == READS[0]:
        return {'strategy': 'strategy-a'}
    return {'strategy': 'strategy-a', 'operation_id': 'operation', 'after': '', 'limit': 1,
            'expected_journal_id': 'journal', 'expected_generation': 'generation',
            'history_version': 1, 'original_generation': 'old-generation'}


@pytest.mark.parametrize('operation', READS)
def test_control_reads_do_not_expire_other_sessions_or_advance_lease_events(registry_and_binding, operation):
    registry, identity, scope, registration, _, journal_db, _ = registry_and_binding
    registry.configure_gateway_lease_drain(instance_id='gateway-a', account_ref='account',
                                           strategies=frozenset({'strategy-a'}), expiry_seconds=0)
    db = registry._db
    row = dict(db.execute('SELECT * FROM sessions WHERE registration_id=?',
                          (registration['registration_id'],)).fetchone())
    row.update(registration_id='expired-other', registration_key='expired-other', expires_at=99.0)
    db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', tuple(row.values()))
    db.execute("INSERT INTO gateway_lease_expirations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               ('event', 'gateway-a', 'account', 'strategy-a', 'run', 'principal', 'expired-other',
                registry._epoch, 99.0, 99.0, 'WAITING', 99.0))
    sessions_before = [tuple(row) for row in db.execute('SELECT * FROM sessions ORDER BY registration_id')]
    events_before = [tuple(row) for row in db.execute('SELECT * FROM gateway_lease_expirations')]
    before, journal_before = db.total_changes, journal_db.total_changes
    statements = []
    db.set_trace_callback(statements.append)
    try:
        reply = dispatch(registry, identity, scope, registration, operation, read_arguments(operation))
    finally:
        db.set_trace_callback(None)
    assert reply['ok'] and reply['result']['status'] == 'OBSERVED', reply
    assert db.total_changes == before and journal_db.total_changes == journal_before
    assert [tuple(row) for row in db.execute('SELECT * FROM sessions ORDER BY registration_id')] == sessions_before
    assert [tuple(row) for row in db.execute('SELECT * FROM gateway_lease_expirations')] == events_before
    assert not any(statement.upper().startswith('BEGIN IMMEDIATE') for statement in statements)


@pytest.mark.parametrize('operation', READS)
def test_expired_control_reader_is_rejected_without_cleanup(registry_and_binding, operation):
    registry, identity, scope, registration, _, journal_db, _ = registry_and_binding
    db = registry._db
    db.execute('UPDATE sessions SET expires_at=? WHERE registration_id=?', (100.0, registration['registration_id']))
    before, journal_before = db.total_changes, journal_db.total_changes
    reply = dispatch(registry, identity, scope, registration, operation, read_arguments(operation))
    assert not reply['ok'] and reply['code'] == 'NOT_REGISTERED'
    assert db.total_changes == before and journal_db.total_changes == journal_before
    assert db.execute('SELECT count(*) FROM sessions WHERE registration_id=?',
                      (registration['registration_id'],)).fetchone()[0] == 1


@pytest.mark.parametrize('operation', READS)
def test_control_reader_lease_expiring_during_handler_is_rechecked(registry_and_binding, operation):
    registry, identity, scope, registration, _, _, _ = registry_and_binding
    observed = []

    def handler(context, arguments):
        with context.dispatch_admission.scope() as recheck:
            registry._clock = lambda: 110.0
            with pytest.raises(ServiceDispatchAdmissionError) as failure:
                recheck()
            observed.append(str(failure.value))
        return {'status': 'OBSERVED'}

    registry._handlers.pop(('gateway-a', operation))
    registry.register_handler('gateway-a', operation, handler)
    before = registry._db.total_changes
    reply = dispatch(registry, identity, scope, registration, operation, read_arguments(operation))
    assert not reply['ok'] and reply['code'] == 'NOT_REGISTERED' and observed == ['NOT_REGISTERED']
    assert registry._db.total_changes == before


@pytest.mark.parametrize('enabled', [False, True])
def test_real_admission_history_and_identity_do_not_mark_migration_write(registry_and_binding, monkeypatch, enabled):
    registry, identity, scope, registration, _, db, _ = registry_and_binding
    registry._migration_writes_enabled = enabled
    original = registry._record_migration_write_attempt
    marks = []

    def marker(*, fact_kind):
        marks.append(fact_kind)
        return original(fact_kind=fact_kind)

    monkeypatch.setattr(registry, '_record_migration_write_attempt', marker)
    before = db.total_changes
    identity_reply = dispatch(registry, identity, scope, registration, READS[0], {'strategy': 'strategy-a'})
    assert identity_reply['ok'], identity_reply
    assert identity_reply['result']['journal_id'] == 'journal'
    reply = dispatch(registry, identity, scope, registration, READS[1], {
        'strategy': 'strategy-a', 'operation_id': 'operation', 'after': '', 'limit': 1,
        'expected_journal_id': 'journal', 'expected_generation': 'generation',
        'history_version': 1, 'original_generation': 'old-generation',
    })
    assert reply['ok'], reply
    assert reply['result']['status'] == 'OBSERVED' and reply['result']['read_only'] is True
    assert reply['result']['original_generation'] == 'old-generation'
    assert marks == [] and db.total_changes == before


@pytest.mark.parametrize('enabled', [False, True])
def test_control_write_still_crosses_migration_gate(registry_and_binding, monkeypatch, enabled):
    registry, identity, scope, registration, _, _, write_calls = registry_and_binding
    registry._migration_writes_enabled = enabled
    original = registry._record_migration_write_attempt
    marks = []

    def marker(*, fact_kind):
        marks.append(fact_kind)
        return original(fact_kind=fact_kind)

    monkeypatch.setattr(registry, '_record_migration_write_attempt', marker)
    reply = dispatch(registry, identity, scope, registration, WRITE, {'operation_id': 'write-op', 'limit': 1})
    assert marks and set(marks) == {'dispatch'}
    if enabled:
        assert reply['ok'] and len(write_calls) == 1
    else:
        assert not reply['ok'] and reply['code'] == 'MIGRATION_WRITE_DISABLED' and not write_calls


def test_read_admission_rechecks_authorization_after_grant_revocation(registry_and_binding):
    registry, identity, scope, registration, _, _, _ = registry_and_binding
    observed = []

    def handler(context, arguments):
        admission = context.dispatch_admission
        assert type(admission) is ServiceDispatchAdmission
        with admission.scope() as recheck:
            registry._grants = {}
            with pytest.raises(ServiceDispatchAdmissionError) as failure:
                recheck()
            observed.append(str(failure.value))
        return {'status': 'UNKNOWN'}

    registry._handlers.pop(('gateway-a', READS[1]))
    registry.register_handler('gateway-a', READS[1], handler)
    reply = dispatch(registry, identity, scope, registration, READS[1], {
        'strategy': 'strategy-a', 'operation_id': 'operation', 'after': '', 'limit': 1,
        'expected_journal_id': 'journal', 'expected_generation': 'generation',
        'history_version': 1, 'original_generation': 'old-generation',
    })
    assert not reply['ok'] and reply['code'] == 'DENIED' and observed == ['DENIED']
