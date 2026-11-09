"""Registry expiry to original-order cancellation, with synthetic sources."""

from dataclasses import replace

import pytest
from bt_api_base.direct_write_protocol import NativeOrderIdentity, PreDispatchRecord, WriteOperation
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_dispatch import NativeDispatchOutcome, _identity
from bt_api_py.gateway_lease_drain import GatewayLeaseDrain, GatewayLeaseDrainSettings
from bt_api_py.gateway_direct_service import GatewayControlGrant, GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.test_iter63_gateway_direct_owner import Harness
from tests.unit.test_iter63_gateway_cancel_owner import owner_for
from tests.unit.test_service_registry import _next_request, _register
from test_iter63_direct_cancel_journal import prepared


@pytest.mark.parametrize('control_proof', [False, True])
def test_true_expiry_persists_then_drains_once_and_new_registration_is_fenced(tmp_path, monkeypatch, control_proof):
    harness = Harness(tmp_path)
    calls = []

    def factory():
        bundle = harness.factory()
        def native(target, symbol, port, scope):
            prep = prepared(target)
            record = PreDispatchRecord(WriteOperation.CANCEL, scope, 'SYN', symbol, None, target, {},
                                       prep.trading_day, prep.session_generation, 'synthetic', prep)
            marker = port.commit(record)
            calls.append(marker)
            return NativeDispatchOutcome(True, target, marker)
        cancel = GatewayDirectCancelDispatcher(GatewayDirectCancelJournal(bundle.journal), bundle.dispatcher,
                                                harness.guard, 'SYN', native, lambda _: True)
        return replace(bundle, cancel_dispatcher=cancel)

    owner = owner_for(factory)
    assert owner.start(2)
    registry = None
    try:
        control = owner.submit_service_call(lambda: harness.journal.enable_controls(harness.guard)).result(timeout_seconds=2)
        command = harness.journal.reserve('strategy', 'order', 'principal', {'symbol':'X','side':'buy','quantity':'1'})
        native = NativeOrderIdentity('SYN', 'ref', {'trading_day':'20300101','front_id':1}, 1)
        started = harness.journal.begin_dispatch('strategy','order',command.reservation_id,command.digest,'principal',1,
                                                 _identity(native,'SYN'),('order-key',))
        if control_proof:
            harness.journal.finish('strategy','order',command.reservation_id,command.digest,'principal',started.record.version,
                                   _identity(native,'SYN'),('order-key',),'SENT')
            def upgrade():
                harness.journal.enable_strategy_ledger(harness.guard)
                harness.journal.enable_account_extensions(harness.guard)
                harness.journal.enable_parent_authority(harness.guard)
                harness.journal.enable_account_facts_financial(harness.guard)
                harness.journal.enable_actor_stream(harness.guard)
                harness.journal.enable_strategy_origin(harness.guard)
                harness.journal.enable_control_cancel(harness.guard)
            owner.submit_service_call(upgrade).result(timeout_seconds=2)
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
        scope = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
        grant_scope = replace(scope, run_id=None)
        ops = frozenset({'direct.reserve', 'direct.dispatch'})
        control_ops = frozenset({'direct.control.resume'})
        capability = ServiceCapability('gateway', 'service.direct_write', ops | control_ops, ops | control_ops)
        resource = ResourceGrant('gateway', 'service.direct_write', 'scope', grant_scope, ops)
        operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'operator-actor')
        operator_scope = WireScope('domain', account_ref='account', strategy_ref='strategy')
        operator_resource = ResourceGrant('gateway', 'service.direct_write', 'scope', operator_scope, control_ops)
        clock = [100.0]
        registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(capability,),
            grants={'principal':(PrincipalGrant(identity,(resource,)),),
                    'operator':(PrincipalGrant(operator,(operator_resource,)),)}, policy_version='p', requirements_version='r',
            lease_seconds=10, clock=lambda: clock[0])
        registry.register_handler('gateway','direct.reserve',lambda *_: {'unexpected':True})
        registry.register_handler('gateway','direct.dispatch',lambda *_: {'unexpected':True})
        manager = GatewayLeaseDrain(GatewayLeaseDrainSettings(frozenset({'strategy'})),
            instance_id='gateway', account_ref='account', owner=owner, control=control,
            service_identity=WireIdentity(WireRole.SERVICE, 'gateway-service', 'gateway-actor'))
        manager.configure(registry)
        def register(key):
            return _register(registry, principal='principal',identity=identity,scope=scope,key=key,
                instance_id='gateway',port='service.direct_write',scope_ref='scope',operations=tuple(ops))
        first = register('first')
        assert first['ok']
        clock[0] = 111.0
        second = register('second')
        assert second['ok'], second
        event, = registry.pending_gateway_lease_expirations(instance_id='gateway',account_ref='account')
        assert event['registration_id'] == first['result']['registration_id'] and event['state'] == 'PENDING'
        rec = second['result']
        fenced = registry.handle('principal', _next_request('direct.reserve', {
            'registration_id':rec['registration_id'],'service_epoch':rec['service_epoch'],
            'instance_id':'gateway','arguments':{}}, identity=identity,scope=scope))
        assert fenced['ok'] is False and fenced['code'] == 'STRATEGY_STOPPED'
        submit = owner.submit_service_call
        def unavailable(_callback):
            raise RuntimeError('synthetic owner unavailable')
        monkeypatch.setattr(owner, 'submit_service_call', unavailable)
        with pytest.raises(RuntimeError):
            manager.maintain(registry)
        assert len(registry.pending_gateway_lease_expirations(instance_id='gateway',account_ref='account')) == 1
        monkeypatch.setattr(owner, 'submit_service_call', submit)
        manager.maintain(registry)
        assert not registry.pending_gateway_lease_expirations(instance_id='gateway',account_ref='account')
        with registry.gateway_lease_control_fence(instance_id='gateway', account_ref='account',
                                                  event_id=event['event_id']) as (proven, recheck):
            assert proven['registration_id'] == first['result']['registration_id']
            assert proven['strategy_ref'] == 'strategy' and proven['state'] == 'APPLIED'
            recheck()
        cause = owner.submit_service_call(lambda: control.get(event['event_id'])).result(timeout_seconds=2)
        assert cause['mode'] == 'DRAIN' and cause['scope_value'] == 'strategy' and cause['active'] == 1
        binding = GatewayDirectServiceBinding(harness.journal,
            {'principal':GatewayDirectGrant('strategy',frozenset({'X'}))}, owner=owner, control=control,
            control_grants={'operator':GatewayControlGrant('operator-actor',False,frozenset({'strategy'}),control_ops)})
        registry.register_handler('gateway','direct.control.resume',lambda ctx,args:binding.handle('direct.control.resume',context=ctx,arguments=args))
        operator_registration = _register(registry,principal='operator',identity=operator,scope=operator_scope,key='operator',
            instance_id='gateway',port='service.direct_write',scope_ref='scope',operations=tuple(control_ops))
        assert operator_registration['ok']
        oprec = operator_registration['result']
        blocked_resume = registry.handle('operator',_next_request('direct.control.resume',{
            'registration_id':oprec['registration_id'],'service_epoch':oprec['service_epoch'],'instance_id':'gateway',
            'arguments':{'cause_id':event['event_id'],'expected_version':1,'operation_id':'manual'}},
            identity=operator,scope=operator_scope))
        assert blocked_resume['ok'] and blocked_resume['result']['code'] == 'RECOVERY_REQUIRED'
        manager.maintain(registry)
        assert len(calls) == (1 if control_proof else 0)
        target, = owner.submit_service_call(lambda: control.targets(event['event_id'])).result(timeout_seconds=2)
        assert target['state'] == ('UNKNOWN' if control_proof else 'PENDING')
        manager.maintain(registry)
        assert len(calls) == (1 if control_proof else 0)
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)


def test_unregister_and_new_service_epoch_are_not_expiry_evidence(tmp_path):
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
    ops = frozenset({'direct.reserve'})
    capability = ServiceCapability('gateway', 'service.direct_write', ops, ops)
    resource = ResourceGrant('gateway', 'service.direct_write', 'scope', replace(scope, run_id=None), ops)
    clock = [100.0]
    def make():
        return ServiceRegistry(tmp_path/'registry', service_id='gateway-service', capabilities=(capability,),
            grants={'principal':(PrincipalGrant(identity,(resource,)),)}, policy_version='p', requirements_version='r',
            lease_seconds=10, clock=lambda:clock[0])
    def register(registry, key):
        result = _register(registry, principal='principal', identity=identity, scope=scope, key=key,
            instance_id='gateway', port='service.direct_write', scope_ref='scope', operations=tuple(ops))
        assert result['ok'], result
        return result['result']
    registry = make()
    registry.configure_gateway_lease_drain(instance_id='gateway',account_ref='account',strategies=frozenset({'strategy'}))
    first = register(registry, 'first')
    result = registry.handle('principal', _next_request('service.unregister', {
        'registration_id': first['registration_id'], 'service_epoch': first['service_epoch']}, identity=identity,scope=scope))
    assert result['ok']
    clock[0] = 111.0
    assert not registry.pending_gateway_lease_expirations(instance_id='gateway',account_ref='account')
    second = register(registry, 'second')
    registry.close()
    clock[0] = 122.0
    registry = make()
    try:
        registry.configure_gateway_lease_drain(instance_id='gateway',account_ref='account',strategies=frozenset({'strategy'}))
        assert not registry.pending_gateway_lease_expirations(instance_id='gateway',account_ref='account')
        assert second['service_epoch'] != registry.service_epoch
    finally:
        registry.close()


def test_pending_expiry_still_fences_after_restart_even_if_option_is_removed(tmp_path):
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
    ops = frozenset({'direct.reserve'})
    capability = ServiceCapability('gateway', 'service.direct_write', ops, ops)
    resource = ResourceGrant('gateway', 'service.direct_write', 'scope', replace(scope, run_id=None), ops)
    clock = [100.0]
    def make():
        return ServiceRegistry(tmp_path/'registry', service_id='gateway-service', capabilities=(capability,),
            grants={'principal':(PrincipalGrant(identity,(resource,)),)}, policy_version='p', requirements_version='r',
            lease_seconds=10, clock=lambda:clock[0])
    def register(registry, key):
        result = _register(registry, principal='principal', identity=identity, scope=scope, key=key,
            instance_id='gateway', port='service.direct_write', scope_ref='scope', operations=tuple(ops))
        assert result['ok'], result
        return result['result']
    registry = make()
    registry.configure_gateway_lease_drain(instance_id='gateway',account_ref='account',strategies=frozenset({'strategy'}))
    register(registry, 'first')
    clock[0] = 111.0
    assert len(registry.pending_gateway_lease_expirations(instance_id='gateway',account_ref='account')) == 1
    registry.close()
    registry = make()
    try:
        registry.register_handler('gateway','direct.reserve',lambda *_: {'unexpected':True})
        second = register(registry, 'second')
        result = registry.handle('principal', _next_request('direct.reserve', {
            'registration_id':second['registration_id'],'service_epoch':second['service_epoch'],
            'instance_id':'gateway','arguments':{}}, identity=identity,scope=scope))
        assert result['ok'] is False and result['code'] == 'STRATEGY_STOPPED'
    finally:
        registry.close()


def test_revoked_principal_is_not_recorded_as_lease_expiry():
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
    ops = frozenset({'direct.reserve'})
    capability = ServiceCapability('gateway', 'service.direct_write', ops, ops)
    resource = ResourceGrant('gateway', 'service.direct_write', 'scope', replace(scope,run_id=None), ops)
    clock = [100.0]
    registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(capability,),
        grants={'principal':(PrincipalGrant(identity,(resource,)),)}, policy_version='p',requirements_version='r',
        lease_seconds=10,clock=lambda:clock[0])
    try:
        registry.configure_gateway_lease_drain(instance_id='gateway',account_ref='account',strategies=frozenset({'strategy'}))
        result = _register(registry, principal='principal',identity=identity,scope=scope,
            instance_id='gateway',port='service.direct_write',scope_ref='scope',operations=tuple(ops))
        assert result['ok']
        registry._grants = {}  # Synthetic trusted authority revocation, never a wire claim.
        clock[0] = 111.0
        assert not registry.pending_gateway_lease_expirations(instance_id='gateway',account_ref='account')
    finally:
        registry.close()
