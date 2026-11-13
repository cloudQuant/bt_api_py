from dataclasses import replace
import threading

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_py.execution_parent_grant import ExecutionParentGrant, PORT, QUERY, SUBMIT
from bt_api_py.service_registry import (PrincipalGrant, ResourceGrant, ServiceCapability,
    ServiceDispatchAdmissionError, ServiceRegistry)
from tests.unit.test_service_registry import _identity, _strategy_scope, _register, _next_request

KEY = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
OTHER = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'hc', 'hc')


def fixture(*, installed=True, operation=SUBMIT, writes=True, actor='strategy-a',
            path=':memory:', migration=False, two=False):
    clock = [100.0]
    identity = _identity(principal_ref='principal-a', actor_ref=actor)
    scope = _strategy_scope(strategy='strategy-a')
    operations = frozenset({SUBMIT, QUERY})
    resource = ResourceGrant('exec-a', PORT, 'scope-a', _strategy_scope(strategy='strategy-a', run_id=None), operations)
    capability = ServiceCapability('exec-a', PORT, operations, frozenset({SUBMIT}) if writes else frozenset())
    trusted = {'principal-a': (PrincipalGrant(identity, (resource,)),)}
    if two:
        other_identity = _identity(principal_ref='principal-b', actor_ref='strategy-b')
        other_resource = ResourceGrant('exec-a', PORT, 'scope-b',
            _strategy_scope(strategy='strategy-b', run_id=None), operations)
        trusted['principal-b'] = (PrincipalGrant(other_identity, (other_resource,)),)
    if migration:
        from bt_api_py.service_migration_gate import arm_migration_cutover
        arm_migration_cutover(path, source_epoch='deploy-previous', new_epoch='deploy-current')
    registry = ServiceRegistry(path, service_id='exec-service', capabilities=(capability,),
        grants=trusted,
        policy_version='p1', requirements_version='r1', clock=lambda: clock[0], lease_seconds=10,
        migration_deployment_epoch='deploy-current' if migration else None)
    if migration:
        from tests.unit.test_service_migration_write_barrier import _enable_registry
        _enable_registry(registry, path)
    grant = ExecutionParentGrant('exec-a', 'scope-a', identity, 'account-a', 'strategy-a',
        scope.execution_domain_ref, 'v1', frozenset({KEY}), operations)
    if installed:
        registry.configure_execution_parents(grants=(grant,))
    received = []
    for op in operations:
        registry.register_handler('exec-a', op, lambda ctx, args: received.append((ctx, args)) or {'ok': True})
    registration = _register(registry, identity=identity, scope=scope, principal='principal-a',
        instance_id='exec-a', port=PORT, scope_ref='scope-a', operations=(operation,))
    return registry, clock, identity, scope, grant, received, registration


def call(registry, identity, scope, registration, operation=SUBMIT, instrument=KEY):
    args = {'spec': {'instrument': instrument.to_dict(), 'account_scope': scope.account_ref,
            'strategy_scope': scope.strategy_ref, 'execution_domain_ref': scope.execution_domain_ref}} if operation == SUBMIT else {'request_id': 'one'}
    r = registry.handle('principal-a', _next_request(operation, {
        'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
        'instance_id': 'exec-a', 'arguments': args}, identity=identity, scope=scope))
    return r, args


def test_no_operator_grant_and_bad_capability_fail():
    for installed, writes in ((False, True), (True, False)):
        registry, _, _, _, _, received, reg = fixture(installed=installed, writes=writes)
        try:
            assert not reg['ok'] and received == []
        finally:
            registry.close()


def test_real_submit_binding_and_instrument():
    registry, _, identity, scope, grant, received, response = fixture()
    try:
        assert response['ok'], response
        reg = response['result']
        denied, _ = call(registry, identity, scope, reg, instrument=OTHER)
        assert not denied['ok'] and not received
        result, args = call(registry, identity, scope, reg)
        assert result['ok'], result
        context = received[0][0]
        permit = context.dispatch_admission
        assert permit.matches_context(context) and permit.matches(args)
        assert not permit.matches({'spec': {'instrument': OTHER.to_dict()}})
        assert permit.matches_operation(SUBMIT) and not permit.matches_operation(QUERY)
        assert permit.matches_execution_parent_grant(grant)
        assert not permit.matches_execution_parent_grant(None)
        with permit.scope() as recheck:
            recheck()
        permit.close()
        assert not permit.matches_execution_parent_grant(grant)
    finally:
        registry.close()


@pytest.mark.parametrize('condition', ['revoke', 'reinstall', 'expire', 'freeze', 'principal', 'run', 'readiness'])
def test_delayed_permit_rechecks_authority(condition):
    registry, clock, identity, scope, grant, received, response = fixture()
    try:
        assert response['ok']
        assert call(registry, identity, scope, response['result'])[0]['ok']
        permit = received[0][0].dispatch_admission
        if condition in {'revoke', 'reinstall'}:
            assert registry.revoke_execution_parent(grant)
            if condition == 'reinstall':
                registry.configure_execution_parents(grants=(grant,))
        elif condition == 'expire':
            clock[0] += 10
        elif condition == 'freeze':
            assert registry.freeze_strategy(scope, 'stop-1')['status'] == 'applied'
        elif condition == 'principal':
            assert registry.revoke_principal('principal-a')
        elif condition == 'run':
            with registry._transaction():
                registry._db.execute("UPDATE strategy_write_claims SET run_id='different'")
        elif condition == 'readiness':
            registry.set_ready(False)
        with pytest.raises(ServiceDispatchAdmissionError), permit.scope():
            pytest.fail('stale grant entered the transaction')
        permit.close()
    finally:
        registry.close()


def test_query_is_read_only_and_does_not_create_write_claim():
    registry, clock, identity, scope, grant, received, response = fixture(operation=QUERY)
    try:
        assert response['ok'], response
        with registry._read_snapshot():
            assert registry._db.execute('SELECT COUNT(*) FROM strategy_write_claims').fetchone()[0] == 0
        with registry._transaction():
            assert registry._claim_strategy_write(scope, clock[0], 'separate-write', frozenset({SUBMIT})) is None
        assert registry.freeze_strategy(scope, 'stop-1')['status'] == 'applied'
        assert call(registry, identity, scope, response['result'], QUERY)[0]['ok']
        permit = received[0][0].dispatch_admission
        assert permit.matches_execution_parent_grant(grant)
        with permit.scope() as check:
            check()
        permit.close()
    finally:
        registry.close()


def test_delayed_scope_reentry_and_cross_thread():
    registry, _, identity, scope, _, received, response = fixture()
    try:
        assert call(registry, identity, scope, response['result'])[0]['ok']
        permit = received[0][0].dispatch_admission
        errors = []
        with permit.scope() as check:
            with pytest.raises(ServiceDispatchAdmissionError, match='ADMISSION_BUSY'), permit.scope():
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
        permit.close()
    finally:
        registry.close()


def test_grant_rejects_wrong_identity_and_atomic_install():
    registry, _, identity, _, grant, _, _ = fixture()
    try:
        from bt_api_base.service_protocol import WireRole
        with pytest.raises(ValueError):
            replace(grant, identity=_identity(role=WireRole.SERVICE))
        new = replace(grant, scope_ref='other')
        with pytest.raises(ValueError):
            registry.configure_execution_parents(grants=(new, grant))
        assert registry.revoke_execution_parent(new) is False
    finally:
        registry.close()


def test_explicit_different_actor_and_strategy_are_allowed():
    registry, _, identity, scope, _, received, response = fixture(actor='trusted-actor-alias')
    try:
        assert response['ok'], response
        assert call(registry, identity, scope, response['result'])[0]['ok']
        with received[0][0].dispatch_admission.scope() as check:
            check()
        received[0][0].dispatch_admission.close()
    finally:
        registry.close()


@pytest.mark.parametrize('field,value', [
    ('account_scope', 'other-account'), ('strategy_scope', 'other-strategy'),
    ('execution_domain_ref', 'other-domain'),
])
def test_submit_cannot_cross_scope_in_arguments(field, value):
    registry, _, identity, scope, _, received, response = fixture()
    try:
        spec = {'instrument': KEY.to_dict(), 'account_scope': scope.account_ref,
                'strategy_scope': scope.strategy_ref, 'execution_domain_ref': scope.execution_domain_ref}
        spec[field] = value
        reg = response['result']
        result = registry.handle('principal-a', _next_request(SUBMIT, {
            'registration_id': reg['registration_id'], 'service_epoch': reg['service_epoch'],
            'instance_id': 'exec-a', 'arguments': {'spec': spec}}, identity=identity, scope=scope))
        assert not result['ok'] and received == []
    finally:
        registry.close()


@pytest.mark.parametrize('change', [
    {'account_ref': 'other-account'}, {'strategy_ref': 'other-strategy'},
    {'execution_domain_ref': 'other-domain'},
])
def test_registration_cannot_cross_grant_scope(change):
    registry, _, identity, scope, _, received, _ = fixture()
    try:
        result = _register(registry, identity=identity, scope=replace(scope, **change),
            principal='principal-a', instance_id='exec-a', port=PORT, scope_ref='scope-a',
            operations=(SUBMIT,), key='other-key')
        assert not result['ok'] and received == []
    finally:
        registry.close()


def test_untrusted_actor_cannot_reuse_registration_or_grant():
    registry, _, identity, scope, _, received, response = fixture(actor='real-actor')
    try:
        different = replace(identity, actor_ref='other-actor')
        reg = response['result']
        result = registry.handle('principal-a', _next_request(SUBMIT, {
            'registration_id': reg['registration_id'], 'service_epoch': reg['service_epoch'],
            'instance_id': 'exec-a', 'arguments': {'spec': {'instrument': KEY.to_dict(),
                'account_scope': scope.account_ref, 'strategy_scope': scope.strategy_ref,
                'execution_domain_ref': scope.execution_domain_ref}}}, identity=different, scope=scope))
        assert not result['ok'] and received == []
    finally:
        registry.close()


def test_revocation_serializes_against_local_commit_scope():
    registry, _, identity, scope, grant, received, response = fixture()
    try:
        assert call(registry, identity, scope, response['result'])[0]['ok']
        permit = received[0][0].dispatch_admission
        attempted, done = threading.Event(), threading.Event()
        def revoke():
            attempted.set()
            registry.revoke_execution_parent(grant)
            done.set()
        with permit.scope() as check:
            thread = threading.Thread(target=revoke)
            thread.start()
            assert attempted.wait(2) and not done.wait(0.05)
            check()
        thread.join(2)
        assert done.is_set()
        with pytest.raises(ServiceDispatchAdmissionError), permit.scope():
            pytest.fail('revoked permit remained live')
        permit.close()
    finally:
        registry.close()


def test_revision_replacement_invalidates_old_permit_but_new_grant_is_used():
    registry, _, identity, scope, grant, received, response = fixture()
    try:
        reg = response['result']
        assert call(registry, identity, scope, reg)[0]['ok']
        old_permit = received[0][0].dispatch_admission
        assert registry.revoke_execution_parent(grant)
        new_grant = replace(grant, revision='v2')
        registry.configure_execution_parents(grants=(new_grant,))
        with pytest.raises(ServiceDispatchAdmissionError), old_permit.scope():
            pytest.fail('old grant revision survived replacement')
        assert call(registry, identity, scope, reg)[0]['ok']
        new_permit = received[1][0].dispatch_admission
        assert new_permit.matches_execution_parent_grant(new_grant)
        assert not new_permit.matches_execution_parent_grant(grant)
        with new_permit.scope() as check:
            check()
        old_permit.close()
        new_permit.close()
    finally:
        registry.close()


def test_grant_operations_restrict_registration():
    registry, _, identity, scope, grant, _, _ = fixture()
    try:
        assert registry.revoke_execution_parent(grant)
        registry.configure_execution_parents(grants=(replace(grant, operations=frozenset({QUERY})),))
        submit = _register(registry, identity=identity, scope=scope, principal='principal-a',
            instance_id='exec-a', port=PORT, scope_ref='scope-a', operations=(SUBMIT,), key='submit2')
        assert not submit['ok']
        query = _register(registry, identity=identity, scope=scope, principal='principal-a',
            instance_id='exec-a', port=PORT, scope_ref='scope-a', operations=(QUERY,), key='query2')
        assert query['ok']
    finally:
        registry.close()


def test_recheck_expires_inside_permit_scope():
    registry, clock, identity, scope, _, received, response = fixture()
    try:
        assert call(registry, identity, scope, response['result'])[0]['ok']
        permit = received[0][0].dispatch_admission
        with permit.scope() as check:
            clock[0] += 10
            with pytest.raises(ServiceDispatchAdmissionError, match='NOT_REGISTERED'):
                check()
        permit.close()
    finally:
        registry.close()


def test_mutating_external_payload_during_instrument_check_cannot_change_permit(monkeypatch):
    """A WireEnvelope owns a recursive frozen copy, independent of source dicts."""
    registry, _, identity, scope, _, received, response = fixture()
    changed, checked = threading.Event(), threading.Event()
    spec = {'instrument': KEY.to_dict(), 'account_scope': scope.account_ref,
            'strategy_scope': scope.strategy_ref, 'execution_domain_ref': scope.execution_domain_ref}
    raw_args = {'spec': spec}
    reg = response['result']
    request = _next_request(SUBMIT, {'registration_id': reg['registration_id'],
        'service_epoch': reg['service_epoch'], 'instance_id': 'exec-a', 'arguments': raw_args},
        identity=identity, scope=scope)
    original = MarketInstrumentKey.from_dict
    def checked_from_dict(cls, value):
        result = original(value)
        checked.set()
        assert changed.wait(3)
        return result
    monkeypatch.setattr(MarketInstrumentKey, 'from_dict', classmethod(checked_from_dict))
    def mutate():
        assert checked.wait(3)
        spec['instrument'] = OTHER.to_dict()
        spec['account_scope'] = 'other-account'
        changed.set()
    thread = threading.Thread(target=mutate)
    thread.start()
    try:
        result = registry.handle('principal-a', request)
        assert result['ok'], result
        context, args = received[0]
        assert args['spec']['instrument'] == KEY.to_dict()
        assert args['spec']['account_scope'] == scope.account_ref
        with pytest.raises(TypeError):
            args['spec']['account_scope'] = 'other-account'
        assert context.dispatch_admission.matches(args)
        assert not context.dispatch_admission.matches(raw_args)
        with context.dispatch_admission.scope() as check:
            check()
        context.dispatch_admission.close()
    finally:
        changed.set()
        thread.join(3)
        registry.close()
    assert not thread.is_alive()


def test_existing_gateway_permit_does_not_match_missing_execution_grant():
    from tests.unit.test_iter63_registry_dispatch_admission import _fixture
    registry, _, _, _, _, _, context = _fixture()
    try:
        permit = context.dispatch_admission
        assert not permit.matches_execution_parent_grant(None)
        permit.close()
        assert not permit.matches_execution_parent_grant(None)
    finally:
        registry.close()


@pytest.mark.parametrize('failure', ['expire', 'revoke', 'timeout', 'exception', 'close_then_expire'])
def test_post_handler_uncertainty_is_never_definite_refusal(failure):
    import sqlite3
    registry, clock, identity, scope, grant, received, response = fixture()
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE parents (request_id TEXT PRIMARY KEY)')
    calls = []
    def handler(context, arguments):
        permit = context.dispatch_admission
        assert permit.matches_context(context) and permit.matches(arguments)
        with permit.scope() as check:
            db.execute('INSERT INTO parents VALUES (?)', ('original-request',))
            check()
            db.commit()
        calls.append('original-request')
        if failure == 'close_then_expire':
            permit.close()
        if failure in {'expire', 'close_then_expire'}:
            clock[0] += 10
        elif failure == 'revoke':
            assert registry.revoke_execution_parent(grant)
        elif failure == 'timeout':
            permit.close()
            raise TimeoutError('synthetic timeout after commit')
        else:
            permit.close()
            raise RuntimeError('synthetic failure after commit')
        permit.close()
        return {'status': 'ACCEPTED'}
    registry._handlers[('exec-a', SUBMIT)] = handler
    try:
        result, _ = call(registry, identity, scope, response['result'])
        assert result == {'ok': False, 'code': 'HANDLER_FAILED', 'result': {}}
        assert calls == ['original-request']
        assert db.execute('SELECT request_id FROM parents').fetchall() == [('original-request',)]
    finally:
        registry.close()
        db.close()


def test_frozen_submit_seals_migration_attempt_before_refusal(tmp_path, monkeypatch):
    from bt_api_py.service_migration_gate import read_migration_marker
    path = tmp_path / 'registry.sqlite'
    registry, _, identity, scope, _, received, response = fixture(path=path, migration=True)
    try:
        assert response['ok']
        assert registry.freeze_strategy(scope, 'stop-1')['status'] == 'applied'
        marker = read_migration_marker(path)
        assert marker is not None and marker.sealed
        calls = []
        real_marker = registry._record_migration_write_attempt
        def record(*, fact_kind):
            calls.append(fact_kind)
            return real_marker(fact_kind=fact_kind)
        monkeypatch.setattr(registry, '_record_migration_write_attempt', record)
        result, _ = call(registry, identity, scope, response['result'])
        assert result['code'] == 'STRATEGY_STOPPED' and received == []
        assert calls == ['dispatch']
        assert read_migration_marker(path).sealed
    finally:
        registry.close()


@pytest.mark.parametrize('failure', ['frozen', 'migration_disabled', 'migration_io', 'readiness', 'freeze_error'])
def test_pre_handler_rejection_closes_and_removes_execution_permit(failure, monkeypatch):
    import sqlite3
    from bt_api_py.service_registry import ServiceDispatchAdmission
    registry, _, identity, scope, _, received, response = fixture()
    created = []
    original_init = ServiceDispatchAdmission.__init__
    def capture(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        created.append(self)
    monkeypatch.setattr(ServiceDispatchAdmission, '__init__', capture)
    try:
        if failure == 'frozen':
            assert registry.freeze_strategy(scope, 'stop-1')['status'] == 'applied'
        elif failure == 'migration_disabled':
            registry._migration_writes_enabled = False
        elif failure == 'migration_io':
            def failed_marker(*, fact_kind):
                raise OSError('synthetic marker failure')
            monkeypatch.setattr(registry, '_record_migration_write_attempt', failed_marker)
        elif failure == 'readiness':
            original_check = registry._execution_parent_admission_error
            def lose_readiness(*args):
                result = original_check(*args)
                registry._ready = False
                return result
            monkeypatch.setattr(registry, '_execution_parent_admission_error', lose_readiness)
        else:
            original_freeze = registry._strategy_write_is_frozen
            invocations = []
            def fail_second_check(scope):
                invocations.append(True)
                if len(invocations) == 2:
                    raise sqlite3.OperationalError('synthetic check failure')
                return original_freeze(scope)
            monkeypatch.setattr(registry, '_strategy_write_is_frozen', fail_second_check)
        result, _ = call(registry, identity, scope, response['result'])
        assert not result['ok'] and received == [] and len(created) == 1
        permit = created[0]
        assert permit not in registry._dispatch_admissions
        with pytest.raises(ServiceDispatchAdmissionError, match='ADMISSION_CLOSED'), permit.scope():
            pass
    finally:
        registry.close()


def test_real_runtime_maps_post_commit_stale_reply_to_unknown(monkeypatch):
    import sqlite3
    import bt_api_py.service_runtime as runtime_module
    from bt_api_py.service_runtime import ServiceCallOutcomeUnknownError
    from tests.unit.test_service_runtime import (_config, _runtime, LocalTransport, CLIENT, SCOPE)
    # The new mount/port is not yet product-wired; this test configures it only
    # in the test to exercise the actual SDK runtime's wire error handling.
    mounts = dict(runtime_module.MOUNT_PORTS)
    mounts['store.account'] = (PORT,)
    operations_by_port = dict(runtime_module.PORT_OPERATIONS)
    operations_by_port[PORT] = frozenset({SUBMIT})
    monkeypatch.setattr(runtime_module, 'MOUNT_PORTS', mounts)
    monkeypatch.setattr(runtime_module, 'PORT_OPERATIONS', operations_by_port)
    clock = [100.0]
    capability = ServiceCapability('gateway-a', PORT, frozenset({SUBMIT}), frozenset({SUBMIT}))
    resource = ResourceGrant('gateway-a', PORT, 'account-a', SCOPE, frozenset({SUBMIT}))
    registry = ServiceRegistry(':memory:', service_id='account-service', capabilities=(capability,),
        grants={CLIENT.principal_ref: (PrincipalGrant(CLIENT, (resource,)),)},
        policy_version='p1', requirements_version='r1', clock=lambda: clock[0], lease_seconds=10)
    grant = ExecutionParentGrant('gateway-a', 'account-a', CLIENT, SCOPE.account_ref,
        SCOPE.strategy_ref, SCOPE.execution_domain_ref, 'v1', frozenset({KEY}), frozenset({SUBMIT}))
    registry.configure_execution_parents(grants=(grant,))
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE parent_requests (request_id TEXT PRIMARY KEY)')
    def handler(context, arguments):
        permit = context.dispatch_admission
        assert permit.matches_context(context) and permit.matches(arguments)
        with permit.scope() as check:
            db.execute('INSERT INTO parent_requests VALUES (?)', ('original-request',))
            check()
            db.commit()
        permit.close()
        clock[0] += 10
        return {'status': 'ACCEPTED'}
    registry.register_handler('gateway-a', SUBMIT, handler)
    transport = LocalTransport(registry)
    runtime = _runtime(_config(), lambda *_: transport,
        requested_operations={'store.account': frozenset({SUBMIT})})
    try:
        runtime.start()
        args = {'spec': {'instrument': KEY.to_dict(), 'account_scope': SCOPE.account_ref,
                         'strategy_scope': SCOPE.strategy_ref, 'execution_domain_ref': SCOPE.execution_domain_ref}}
        with pytest.raises(ServiceCallOutcomeUnknownError) as error:
            runtime.call('store.account', SUBMIT, args)
        assert error.value.operation == SUBMIT
        assert db.execute('SELECT request_id FROM parent_requests').fetchall() == [('original-request',)]
        assert sum(req.header.operation == SUBMIT for req in transport.requests) == 1
    finally:
        runtime.close()
        registry.close()
        db.close()


def _other_grant():
    identity = _identity(principal_ref='principal-b', actor_ref='strategy-b')
    scope = _strategy_scope(strategy='strategy-b')
    grant = ExecutionParentGrant('exec-a', 'scope-b', identity, scope.account_ref,
        scope.strategy_ref, scope.execution_domain_ref, 'v1', frozenset({KEY}), frozenset({SUBMIT, QUERY}))
    return identity, scope, grant


def test_independent_strategies_grant_changes_do_not_invalidate_each_other():
    registry, _, identity_a, scope_a, grant_a, received, response_a = fixture(two=True)
    try:
        assert call(registry, identity_a, scope_a, response_a['result'])[0]['ok']
        old_a = received[-1][0].dispatch_admission
        identity_b, scope_b, grant_b = _other_grant()
        registry.configure_execution_parents(grants=(grant_b,))
        response_b = _register(registry, identity=identity_b, scope=scope_b, principal='principal-b',
            instance_id='exec-a', port=PORT, scope_ref='scope-b', operations=(SUBMIT,), key='b-key')
        assert response_b['ok'], response_b
        reg_b = response_b['result']
        args_b = {'spec': {'instrument': KEY.to_dict(), 'account_scope': scope_b.account_ref,
            'strategy_scope': scope_b.strategy_ref, 'execution_domain_ref': scope_b.execution_domain_ref}}
        result_b = registry.handle('principal-b', _next_request(SUBMIT, {
            'registration_id': reg_b['registration_id'], 'service_epoch': reg_b['service_epoch'],
            'instance_id': 'exec-a', 'arguments': args_b}, identity=identity_b, scope=scope_b))
        assert result_b['ok'], result_b
        permit_b = received[-1][0].dispatch_admission
        with old_a.scope() as check:
            check()
        assert registry.revoke_execution_parent(grant_a)
        registry.configure_execution_parents(grants=(grant_a,))
        with pytest.raises(ServiceDispatchAdmissionError), old_a.scope():
            pytest.fail('same-key revoke/reinstall revived prior permit')
        with permit_b.scope() as check:
            check()
        assert call(registry, identity_a, scope_a, response_a['result'])[0]['ok']
        new_a = received[-1][0].dispatch_admission
        assert registry.revoke_execution_parent(grant_b)
        with pytest.raises(ServiceDispatchAdmissionError), permit_b.scope():
            pytest.fail('revoked B permit remained usable')
        with new_a.scope() as check:
            check()
        old_a.close()
        permit_b.close()
        new_a.close()
    finally:
        registry.close()


@pytest.mark.parametrize('phase', ['before_commit', 'after_commit'])
def test_other_strategy_grant_change_does_not_reject_a_submit(phase):
    import sqlite3
    registry, _, identity_a, scope_a, _, _, response = fixture(two=True)
    _, _, grant_b = _other_grant()
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE parent_requests (request_id TEXT PRIMARY KEY)')
    def change_b():
        registry.configure_execution_parents(grants=(grant_b,))
        assert registry.revoke_execution_parent(grant_b)
    def handler(context, arguments):
        permit = context.dispatch_admission
        assert permit.matches_context(context) and permit.matches(arguments)
        if phase == 'before_commit':
            change_b()
        with permit.scope() as check:
            db.execute('INSERT INTO parent_requests VALUES (?)', ('request-a',))
            check()
            db.commit()
        if phase == 'after_commit':
            change_b()
        permit.close()
        return {'status': 'ACCEPTED'}
    registry._handlers[('exec-a', SUBMIT)] = handler
    try:
        result, _ = call(registry, identity_a, scope_a, response['result'])
        assert result['ok'] and result['result'] == {'status': 'ACCEPTED'}
        assert db.execute('SELECT request_id FROM parent_requests').fetchall() == [('request-a',)]
    finally:
        registry.close()
        db.close()
