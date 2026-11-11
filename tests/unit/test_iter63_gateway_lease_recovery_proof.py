"""Synthetic authority only: lease recovery retains independent control causes."""
from contextlib import contextmanager
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_base.gateway_execution_stream_protocol import gateway_execution_consumer_ref
from bt_api_base.direct_write_protocol import NativeOrderIdentity
from bt_api_gateway.direct_dispatch import _identity
from bt_api_py.gateway_lease_recovery import GatewayLeaseRecovery, LeaseRecoveryEvidence
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.test_iter63_gateway_direct_owner import Harness, create
from tests.unit.test_service_registry import _next_request, _register


class SyntheticSource:
    def __init__(self):
        self.valid = True
        self.mutate = None
        self.during_recheck = None
        self.expiry = 1000.0
        self.evidence = None

    @contextmanager
    def hold_verified(self, binding):
        # Test authority represents a complete source/strategy reconciliation;
        # a real provider must establish and fence these facts itself.
        self.evidence = LeaseRecoveryEvidence(binding, 'proof-1', 'synthetic-cut', self.expiry)
        if self.mutate:
            self.mutate()
        yield self.evidence

    def recheck(self, evidence):
        if self.during_recheck:
            self.during_recheck()
        if not self.valid or evidence is not self.evidence:
            raise ValueError('stale source')
        return True


@pytest.fixture
def scenario(tmp_path, request):
    h = Harness(tmp_path)
    owner = create(h)
    assert owner.start(2)
    registry = None
    try:
        control = owner.submit_service_call(lambda: h.journal.enable_controls(h.guard)).result(timeout_seconds=2)
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
        scope = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
        ops = frozenset({'direct.reserve', 'direct.dispatch'})
        control_ops = frozenset({'direct.control.resume'})
        capability = ServiceCapability('gateway', 'service.direct_write', ops | control_ops, ops | control_ops)
        operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'operator-actor')
        operator_scope = WireScope('domain', account_ref='account', strategy_ref='strategy')
        operator_resource = ResourceGrant('gateway', 'service.direct_write', 'scope', operator_scope, control_ops)
        resource = ResourceGrant('gateway', 'service.direct_write', 'scope', replace(scope, run_id=None), ops)
        now = [100.0]
        registry = ServiceRegistry(':memory:', service_id='service', capabilities=(capability,),
            grants={'principal': (PrincipalGrant(identity, (resource,)),),
                    'operator': (PrincipalGrant(operator, (operator_resource,)),)}, policy_version='p', requirements_version='r',
            lease_seconds=10, clock=lambda: now[0])
        for op in ops:
            registry.register_handler('gateway', op, lambda *_: {})
        registry.configure_gateway_lease_drain(instance_id='gateway', account_ref='account', strategies=frozenset({'strategy'}), expiry_seconds=0)
        def register(key):
            result = _register(registry, principal='principal', identity=identity, scope=scope, key=key,
                instance_id='gateway', port='service.direct_write', scope_ref='scope', operations=tuple(ops))
            assert result['ok'], result
            return result['result']
        first = register('first')
        now[0] = 111
        second = register('second')
        event, = registry.pending_gateway_lease_expirations(instance_id='gateway', account_ref='account')
        assert event['registration_id'] == first['registration_id'] != second['registration_id']
        origin = 'system:lease:' + __import__('hashlib').sha256(b'gateway\0account').hexdigest()
        def initialize():
            if getattr(request, 'param', None) == 'unknown':
                command = h.journal.reserve('strategy', 'order', 'principal', {'symbol': 'X'})
                native = NativeOrderIdentity('SYN', 'ref', {'trading_day': '20300101', 'front_id': 1}, 1)
                identity = _identity(native, 'SYN')
                h.journal.begin_dispatch('strategy', 'order', command.reservation_id, command.digest,
                                         'principal', 1, identity, ('order-key',))
                h.journal.finish('strategy', 'order', command.reservation_id, command.digest,
                                 'principal', 2, identity, ('order-key',), 'UNKNOWN')
            control.apply(event['event_id'], 'DRAIN', scope_kind='strategy', scope_value='strategy', origin=origin)
            control.apply('other', 'FREEZE', scope_kind='strategy', scope_value='strategy', origin='operator')
            with h.journal._transaction() as db:
                db.execute('INSERT INTO direct_execution_consumers VALUES (?,?,?,?,?,?)',
                           ('account', 'principal', gateway_execution_consumer_ref(principal_ref='principal', actor_ref='strategy',
                            execution_domain_ref='domain', account_scope='account', strategy_scope='strategy'), 'strategy', 0, 0))
        owner.submit_service_call(initialize).result(timeout_seconds=2)
        registry.acknowledge_gateway_lease_expiration(instance_id='gateway', account_ref='account', event_id=event['event_id'])
        source = SyntheticSource()
        recovery = GatewayLeaseRecovery(registry=registry, instance_id='gateway', account_ref='account',
            owner=owner, control=control, source=source, clock=lambda: now[0])
        yield h, owner, registry, control, source, recovery, event, now
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)


def test_trusted_cut_clears_only_lease_cause(scenario):
    _, owner, _, control, _, recovery, event, _ = scenario
    assert recovery.recover_auto(event['event_id']) == 'RECOVERED'
    lease = owner.submit_service_call(lambda: control.get(event['event_id'])).result(timeout_seconds=2)
    other = owner.submit_service_call(lambda: control.get('other')).result(timeout_seconds=2)
    assert lease['active'] == 0 and lease['version'] == 2
    assert other['active'] == 1


@pytest.mark.parametrize('failure', ['no_source', 'expired_proof', 'stale_source', 'journal_changed', 'lease_expired', 'missing_consumer', 'expire_during_recheck'])
def test_recovery_fails_closed(scenario, failure):
    h, owner, _, control, source, recovery, event, now = scenario
    if failure == 'no_source':
        recovery.source = None
    elif failure == 'expired_proof':
        source.expiry = 111
    elif failure == 'stale_source':
        source.valid = False
    elif failure == 'journal_changed':
        source.mutate = lambda: h.journal.reserve('other-strategy', 'new', 'principal', {'symbol': 'X'})
    elif failure == 'lease_expired':
        now[0] = 122
    elif failure == 'expire_during_recheck':
        source.during_recheck = lambda: now.__setitem__(0, 122)
    else:
        # The strategy has an event it never read (no consumer row).
        def unread_event():
            with h.journal._transaction() as db:
                db.execute('DELETE FROM direct_execution_consumers WHERE account=?', ('account',))
                db.execute('INSERT INTO direct_execution_events VALUES (1,?,?,?,?)',
                           ('account', 'strategy', 'digest-1', '{}'))
        owner.submit_service_call(unread_event).result(timeout_seconds=2)
    assert recovery.recover_auto(event['event_id']) == 'NOT_COMPARABLE'
    cause = owner.submit_service_call(lambda: control.get(event['event_id'])).result(timeout_seconds=2)
    assert cause['active'] == 1 and cause['version'] == 1


def test_strategy_without_any_event_needs_no_consumer_row(scenario):
    """The journal creates the consumer row with the first offered event (found on SimNow 7x24)."""
    h, owner, _, control, _, recovery, event, _ = scenario

    def never_read():
        with h.journal._transaction() as db:
            db.execute('DELETE FROM direct_execution_consumers WHERE account=?', ('account',))
            assert db.execute('SELECT count(*) FROM direct_execution_events').fetchone()[0] == 0
    owner.submit_service_call(never_read).result(timeout_seconds=2)
    assert recovery.recover_auto(event['event_id']) == 'RECOVERED'
    cause = owner.submit_service_call(lambda: control.get(event['event_id'])).result(timeout_seconds=2)
    assert cause['active'] == 0 and cause['version'] == 2


def test_consumer_behind_the_latest_event_is_not_restored(scenario):
    h, owner, _, control, _, recovery, event, _ = scenario

    def one_unacknowledged_event():
        with h.journal._transaction() as db:
            db.execute('INSERT INTO direct_execution_events VALUES (1,?,?,?,?)',
                       ('account', 'strategy', 'digest-1', '{}'))
    owner.submit_service_call(one_unacknowledged_event).result(timeout_seconds=2)
    assert recovery.recover_auto(event['event_id']) == 'NOT_COMPARABLE'

    def acknowledge():
        with h.journal._transaction() as db:
            db.execute('UPDATE direct_execution_consumers SET offered_sequence=1, acknowledged_sequence=1 '
                       'WHERE account=?', ('account',))
    owner.submit_service_call(acknowledge).result(timeout_seconds=2)
    assert recovery.recover_auto(event['event_id']) == 'RECOVERED'


@pytest.mark.parametrize('scenario', ['unknown'], indirect=True)
def test_unknown_order_remains_blocked_even_with_source(scenario):
    _, owner, _, control, _, recovery, event, _ = scenario
    assert recovery.recover_auto(event['event_id']) == 'NOT_COMPARABLE'
    cause = owner.submit_service_call(lambda: control.get(event['event_id'])).result(timeout_seconds=2)
    assert cause['active'] == 1


def test_manual_resume_uses_real_admission_and_derived_consumer(scenario):
    h, owner, registry, control, source, recovery, event, _ = scenario
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'operator-actor')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy')
    def handler(ctx, args):
        try:
            result = owner.submit_service_call(lambda: recovery.recover_manual_owned(
                args['cause_id'], args['expected_version'], args['operation_id'],
                admission=ctx.dispatch_admission, context=ctx, arguments=args)).result(timeout_seconds=2)
            return {'status': 'OBSERVED' if result else 'RECOVERY_REQUIRED'}
        finally:
            ctx.dispatch_admission.close()
    registry.register_handler('gateway', 'direct.control.resume', handler)
    registration = _register(registry, principal='operator', identity=identity, scope=scope, key='operator',
        instance_id='gateway', port='service.direct_write', scope_ref='scope',
        operations=('direct.control.resume',))['result']
    args = {'cause_id': event['event_id'], 'expected_version': 1, 'operation_id': 'manual',
            'expected_journal_id': h.journal.journal_id}
    def resume():
        return registry.handle('operator', _next_request('direct.control.resume', {
            'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
            'instance_id': 'gateway', 'arguments': args}, identity=identity, scope=scope))
    assert resume()['result']['status'] == 'OBSERVED'
    assert source.evidence.binding.consumer_ref.startswith('gexec:')
    assert resume()['result']['status'] == 'OBSERVED'
    assert owner.submit_service_call(lambda: control.get('other')).result(timeout_seconds=2)['active'] == 1


def test_manual_requires_real_admission(scenario):
    _, _, _, _, _, recovery, event, _ = scenario
    with pytest.raises(ValueError, match='ADMISSION'):
        recovery.recover_manual_owned(event['event_id'], 1, 'manual', admission=None,
                                      context=None, arguments={})


def test_manual_admission_revocation_during_source_recheck_rolls_back(scenario):
    _, owner, registry, control, source, recovery, event, _ = scenario
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'operator-actor')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy')
    def handler(ctx, args):
        source.during_recheck = ctx.dispatch_admission.close
        try:
            result = owner.submit_service_call(lambda: recovery.recover_manual_owned(
                args['cause_id'], args['expected_version'], args['operation_id'],
                admission=ctx.dispatch_admission, context=ctx, arguments=args)).result(timeout_seconds=2)
            return {'status': 'OBSERVED' if result else 'RECOVERY_REQUIRED'}
        except Exception:
            return {'status': 'UNKNOWN'}
    registry.register_handler('gateway', 'direct.control.resume', handler)
    registration = _register(registry, principal='operator', identity=identity, scope=scope, key='operator',
        instance_id='gateway', port='service.direct_write', scope_ref='scope',
        operations=('direct.control.resume',))['result']
    args = {'cause_id': event['event_id'], 'expected_version': 1, 'operation_id': 'manual'}
    result = registry.handle('operator', _next_request('direct.control.resume', {
        'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
        'instance_id': 'gateway', 'arguments': args}, identity=identity, scope=scope))
    assert result['result']['status'] != 'OBSERVED'
    assert owner.submit_service_call(lambda: control.get(event['event_id'])).result(timeout_seconds=2)['active'] == 1
