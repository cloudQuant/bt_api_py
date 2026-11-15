"""Real Registry admission + cancel-stream handler; memory SQLite, fake owner only."""
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway import account_cancel_attempt_stream as stream
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_py.gateway_account_cancel_attempts import GatewayAccountCancelAttempts, OPERATIONS
from bt_api_py.gateway_direct_owner import GatewayDirectOwner
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_service_registry import _next_request, _register


@pytest.fixture
def environment(monkeypatch):
    db = sqlite3.connect(':memory:', isolation_level=None)
    db.row_factory = sqlite3.Row
    assert db.execute('PRAGMA journal_mode=MEMORY').fetchone()[0] == 'memory'
    db.execute('PRAGMA temp_store=MEMORY')
    assert all(r['file'] == '' for r in db.execute('PRAGMA database_list'))
    db.execute('CREATE TABLE direct_journal_meta (account TEXT, revision INTEGER)')
    db.execute("INSERT INTO direct_journal_meta VALUES ('account',0)")
    db.execute('BEGIN')
    stream.initialize(db, 'account', 'journal', 10)
    db.execute('COMMIT')
    @contextmanager
    def transaction():
        db.execute('BEGIN IMMEDIATE')
        try:
            yield db
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK')
            raise
    journal = object.__new__(GatewayDirectCommandJournal)
    journal._account, journal._journal_id, journal._generation = 'account', 'journal', 'generation'
    journal._cancel_attempt_stream_enabled, journal._transaction = True, transaction
    owner = object.__new__(GatewayDirectOwner)
    monkeypatch.setattr(GatewayDirectOwner, 'is_bound_to', lambda self, other: other is journal)
    monkeypatch.setattr(GatewayDirectOwner, 'submit_service_call',
                        lambda self, function: SimpleNamespace(result=lambda timeout_seconds: function()))
    observer = GatewayAccountCancelAttempts(owner, journal, account_scope='account',
                                            execution_domain_ref='domain', principals=frozenset({'risk'}))
    identity = WireIdentity(WireRole.SERVICE, 'risk', 'risk-actor')
    scope = WireScope('domain', account_ref='account')
    cap = ServiceCapability('gateway', 'service.account_read', OPERATIONS, frozenset())
    grant = ResourceGrant('gateway', 'service.account_read', 'scope', scope, OPERATIONS)
    clock = [100.0]
    registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(cap,),
        grants={'risk': (PrincipalGrant(identity, (grant,)),)}, policy_version='p', requirements_version='r',
        lease_seconds=10, clock=lambda: clock[0])
    registry._db.execute('PRAGMA temp_store=MEMORY')
    assert registry._db.execute('PRAGMA journal_mode').fetchone()[0] == 'memory'
    assert registry._db.execute('PRAGMA database_list').fetchone()[2] == ''
    for op in OPERATIONS:
        registry.register_handler('gateway', op, lambda c, a, op=op: observer.handle(op, c, a))
    reg = _register(registry, principal='risk', identity=identity, scope=scope, instance_id='gateway',
                    port='service.account_read', scope_ref='scope', operations=tuple(OPERATIONS))
    assert reg['ok'], reg
    yield registry, identity, scope, reg['result'], db, clock
    registry.close()
    db.close()


def call(registry, identity, scope, registration, operation, arguments):
    return registry.handle('risk', _next_request(operation, {'registration_id': registration['registration_id'],
        'service_epoch': registration['service_epoch'], 'instance_id': 'gateway', 'arguments': arguments},
        identity=identity, scope=scope))


def test_authorized_progress_and_read_with_registry_admission(environment):
    registry, identity, scope, reg, db, _ = environment
    progress = call(registry, identity, scope, reg, 'account.cancel_attempts.progress', {})
    assert progress['ok'] and progress['result']['progress']['history_complete'] is False
    page = call(registry, identity, scope, reg, 'account.cancel_attempts.read_after',
                {'after_sequence': 0, 'expected_epoch': None, 'limit': 1})
    assert page['ok'] and page['result']['page']['events'] == []
    ack = call(registry, identity, scope, reg, 'account.cancel_attempts.ack',
               {'sequence': 0, 'expected_epoch': page['result']['page']['epoch']})
    assert ack['ok'] and ack['result']['progress']['acknowledged_sequence'] == 0


def test_cross_account_and_expired_registration_denied(environment):
    registry, identity, scope, reg, db, clock = environment
    before = db.total_changes
    wrong = call(registry, identity, replace(scope, account_ref='other'), reg, 'account.cancel_attempts.progress', {})
    assert not wrong['ok']
    clock[0] = 110.0
    expired = call(registry, identity, scope, reg, 'account.cancel_attempts.progress', {})
    assert not expired['ok']
    assert db.total_changes == before


def test_migration_blocks_cursor_writes_but_permits_progress(environment):
    registry, identity, scope, reg, db, _ = environment
    registry._migration_writes_enabled = False
    before = db.total_changes
    for operation, arguments in [('account.cancel_attempts.read_after', {'after_sequence': 0, 'expected_epoch': None, 'limit': 1}),
                                 ('account.cancel_attempts.ack', {'sequence': 0, 'expected_epoch': '0' * 64})]:
        reply = call(registry, identity, scope, reg, operation, arguments)
        assert not reply['ok'] and reply['code'] == 'MIGRATION_WRITE_DISABLED'
    progress = call(registry, identity, scope, reg, 'account.cancel_attempts.progress', {})
    assert progress['ok']
    assert db.total_changes == before


def test_real_registry_refuses_ungranted_operation_and_lease_expiry_during_handler(environment, monkeypatch):
    registry, identity, scope, reg, db, clock = environment
    denied = call(registry, identity, scope, reg, 'account.facts.progress', {})
    assert not denied['ok']
    original = stream.read_after
    def expire_after_write(*args, **kwargs):
        page = original(*args, **kwargs)
        clock[0] = 110.0
        return page
    monkeypatch.setattr(stream, 'read_after', expire_after_write)
    failed = call(registry, identity, scope, reg, 'account.cancel_attempts.read_after',
                  {'after_sequence': 0, 'expected_epoch': None, 'limit': 1})
    assert not failed['ok']
    assert db.execute('SELECT count(*) FROM account_cancel_stream_consumers').fetchone()[0] == 0


def test_client_checks_bound_peer_key_and_registration(monkeypatch):
    import bt_api_py.gateway_account_cancel_attempts_client as client_module
    from bt_api_py.gateway_account_observer_client import GatewayAccountObserverRoute
    from bt_api_py.service_runtime_thread import BoundCallObservation, BoundCallResult, BoundCallRoute
    identity = WireIdentity(WireRole.SERVICE, 'risk', 'risk-actor')
    peer = WireIdentity(WireRole.SERVICE, 'gateway', 'gateway-actor')
    scope = WireScope('domain', account_ref='account')
    route = GatewayAccountObserverRoute('upstream.account', 'gateway-service', 'gateway', 'scope', scope, identity)
    operation = 'account.cancel_attempts.progress'
    observed = BoundCallObservation(identity, 'run', scope, 'registration', 'epoch', route.mount,
        route.service_id, route.instance_id, route.scope_ref, frozenset({operation}), peer, 'a'*64,
        1000.0, True, True, operation)
    class Lock:
        def acquire(self, blocking=False): return True
        def release(self): pass
    thread = object()
    monkeypatch.setattr(client_module.threading, 'current_thread', lambda: thread)
    c = object.__new__(client_module.GatewayAccountCancelAttemptsClient)
    c._pid, c._thread, c._lock = client_module.os.getpid(), thread, Lock()
    c._route, c._bound_route, c._expected_fingerprint = route, BoundCallRoute(identity, scope, 'scope', 'gateway-service', 'gateway', peer), 'a'*64
    c._consumer_ref = 'consumer'
    c._registration = lambda op: ('registration', 'epoch')
    raw = {'schema_version': 1, 'account_scope': 'account', 'consumer_ref': 'consumer', 'epoch': 'b'*64,
           'offered_sequence': 0, 'acknowledged_sequence': 0, 'history_complete': False, 'overflow': False}
    state = [BoundCallResult({'status': 'OK', 'progress': raw}, observed, observed)]
    c._runtime = SimpleNamespace(call_bound=lambda *args, **kwargs: state[0])
    assert c.progress().outcome == 'OBSERVED'
    bad = replace(observed, fingerprint='c'*64)
    state[0] = BoundCallResult({'status': 'OK', 'progress': raw}, bad, bad)
    assert c.progress().outcome == 'UNKNOWN'
    state[0] = BoundCallResult({'status': 'OK', 'progress': raw}, observed, replace(observed, registration_id='new'))
    assert c.progress().outcome == 'UNKNOWN'
    state[0] = BoundCallResult({'status': 'OK', 'progress': raw}, replace(observed, peer=identity), replace(observed, peer=identity))
    assert c.progress().outcome == 'UNKNOWN'
