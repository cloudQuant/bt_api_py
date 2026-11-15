"""Local Registry checks for Gateway delegated strategy-run ownership; no network."""
from dataclasses import replace

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_delegation import GatewayDelegateGrant

from bt_api_py.gateway_parent_service import GatewayParentGrant
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_service_registry import _next_request, _register


SCOPE = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
TEMPLATE = replace(SCOPE, run_id=None)
STRATEGY = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-principal', 'strategy')
SECOND_STRATEGY = WireIdentity(WireRole.STRATEGY_CLIENT, 'second-principal', 'strategy')
ACTOR = WireIdentity(WireRole.SERVICE, 'actor-principal', 'actor')
KEY = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')


def _registry(kind, strategy_instance='gateway', strategy_port='service.direct_write', *, alternate=None):
    clock = [100.0]
    actor_op = 'direct.delegated_reserve' if kind == 'delegate' else 'direct.parent.cancel'
    strategy_op = 'direct.reserve' if strategy_port == 'service.direct_write' else 'orders.submit'
    capabilities = {(strategy_instance, strategy_port): {strategy_op},
                    ('gateway', 'service.direct_write'): {actor_op}}
    if (strategy_instance, strategy_port) == ('gateway', 'service.direct_write'):
        capabilities[('gateway', 'service.direct_write')].add(strategy_op)
    strategy_resources = [ResourceGrant(strategy_instance, strategy_port, 'scope', TEMPLATE,
                                        frozenset({strategy_op}))]
    if alternate is not None:
        alt_instance, alt_port = alternate
        alt_op = 'direct.reserve' if alt_port == 'service.direct_write' else 'orders.submit'
        capabilities.setdefault((alt_instance, alt_port), set()).add(alt_op)
        strategy_resources.append(ResourceGrant(alt_instance, alt_port, 'scope', TEMPLATE,
                                                frozenset({alt_op})))
    registry = ServiceRegistry(':memory:', service_id='gateway-service',
        capabilities=tuple(ServiceCapability(i, p, frozenset(ops), frozenset(ops))
                           for (i, p), ops in capabilities.items()),
        grants={STRATEGY.principal_ref: (PrincipalGrant(STRATEGY, tuple(strategy_resources)),),
                SECOND_STRATEGY.principal_ref: (PrincipalGrant(SECOND_STRATEGY, tuple(strategy_resources)),),
                ACTOR.principal_ref: (PrincipalGrant(ACTOR, (ResourceGrant(
                    'gateway', 'service.direct_write', 'scope', TEMPLATE, frozenset({actor_op})),)),)},
        policy_version='p', requirements_version='r', lease_seconds=10, clock=lambda: clock[0])
    if kind == 'delegate':
        grant = GatewayDelegateGrant(ACTOR, 'account', 'strategy', 'domain', 'risk', 'rev',
                                     frozenset({KEY}), frozenset({'direct.reserve'}))
        registry.configure_gateway_delegates(instance_id='gateway', scope_ref='scope', grants=(grant,))
    else:
        grant = GatewayParentGrant(ACTOR, 'account', 'strategy', 'domain', 'execution', 'rev',
                                   frozenset({KEY}), frozenset({actor_op}))
        registry.configure_gateway_parents(instance_id='gateway', scope_ref='scope', grants=(grant,))
    called = []
    registry.register_handler('gateway', actor_op, lambda *_: called.append(True) or {'accepted': True})
    strategy = _register(registry, principal=STRATEGY.principal_ref, identity=STRATEGY, scope=SCOPE,
        key='strategy', instance_id=strategy_instance, port=strategy_port, scope_ref='scope', operations=(strategy_op,))
    assert strategy['ok'], strategy
    return registry, clock, actor_op, strategy['result'], called


def _actor_register(registry, op):
    return _register(registry, principal=ACTOR.principal_ref, identity=ACTOR, scope=SCOPE,
        key='actor', instance_id='gateway', port='service.direct_write', scope_ref='scope', operations=(op,))


def _unregister(registry, registration):
    return registry.handle(STRATEGY.principal_ref, _next_request('service.unregister', {
        'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
    }, identity=STRATEGY, scope=SCOPE))


def _call(registry, op, registration):
    return registry.handle(ACTOR.principal_ref, _next_request(op, {
        'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
        'instance_id': 'gateway', 'arguments': {},
    }, identity=ACTOR, scope=SCOPE))


@pytest.mark.parametrize('kind', ['delegate', 'parent'])
@pytest.mark.parametrize('instance,port,expected', [
    ('other', 'service.direct_write', False),
    ('gateway', 'service.account_read', False),
    ('gateway', 'service.direct_write', True),
])
def test_actor_registration_requires_strategy_gateway_instance_and_write_port(kind, instance, port, expected):
    registry, _, op, _, _ = _registry(kind, instance, port)
    try:
        result = _actor_register(registry, op)
        assert result['ok'] is expected, result
        if not expected:
            assert result['code'] == 'SCOPE_DENIED'
    finally:
        registry.close()


@pytest.mark.parametrize('kind', ['delegate', 'parent'])
@pytest.mark.parametrize('end', ['unregister', 'expire'])
def test_actor_call_refuses_ended_strategy_registration(kind, end):
    registry, clock, op, strategy, called = _registry(kind)
    try:
        clock[0] = 105
        actor = _actor_register(registry, op)
        assert actor['ok'], actor
        assert _call(registry, op, actor['result'])['ok']
        if end == 'unregister':
            assert _unregister(registry, strategy)['ok']
        else:
            clock[0] = 110
        result = _call(registry, op, actor['result'])
        assert not result['ok'] and result['code'] == 'DUPLICATE_ACTIVE_RUN', result
        assert called == [True]
    finally:
        registry.close()


@pytest.mark.parametrize('kind', ['delegate', 'parent'])
@pytest.mark.parametrize('alternate', [('other', 'service.direct_write'), ('gateway', 'service.account_read')])
def test_actor_call_refuses_replacement_on_other_instance_or_port(kind, alternate):
    registry, _, op, strategy, called = _registry(kind, alternate=alternate)
    try:
        actor = _actor_register(registry, op)
        assert actor['ok'], actor
        assert _unregister(registry, strategy)['ok']
        instance, port = alternate
        replacement = _register(registry, principal=STRATEGY.principal_ref, identity=STRATEGY,
            scope=SCOPE, key='replacement', instance_id=instance, port=port, scope_ref='scope',
            operations=(('direct.reserve' if port == 'service.direct_write' else 'orders.submit'),))
        assert replacement['ok'], replacement
        result = _call(registry, op, actor['result'])
        assert not result['ok'] and result['code'] == 'DUPLICATE_ACTIVE_RUN', result
        assert not called
    finally:
        registry.close()


def test_same_account_strategy_cannot_register_a_second_live_writer():
    registry, _, _, _, _ = _registry('delegate')
    try:
        result = _register(registry, principal=SECOND_STRATEGY.principal_ref, identity=SECOND_STRATEGY, scope=SCOPE,
            key='second', client_session_id='second', instance_id='gateway', port='service.direct_write',
            scope_ref='scope', operations=('direct.reserve',))
        assert not result['ok'] and result['code'] == 'DUPLICATE_ACTIVE_RUN', result
    finally:
        registry.close()


@pytest.mark.parametrize('kind', ['delegate', 'parent'])
def test_actor_registration_rechecks_owner_within_transaction(kind, monkeypatch):
    registry, _, op, strategy, _ = _registry(kind)
    original = registry._authorized_resource
    first = [True]
    def unregister_after_initial_check(*args, **kwargs):
        result = original(*args, **kwargs)
        if first[0] and args[0] == ACTOR.principal_ref:
            first[0] = False
            assert _unregister(registry, strategy)['ok']
        return result
    monkeypatch.setattr(registry, '_authorized_resource', unregister_after_initial_check)
    try:
        result = _actor_register(registry, op)
        assert not result['ok'] and result['code'] == 'SCOPE_DENIED', result
    finally:
        registry.close()
