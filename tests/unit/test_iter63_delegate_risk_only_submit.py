"""D63-37: optional setting that leaves a strategy's SUBMIT to its Risk delegate only.

Default off keeps the present permissions. The Gateway never checks a Risk approval;
this is an ordinary grant. Synthetic CTP composition and Registry only, no provider.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
import zmq
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_delegation import GatewayDelegateGrant

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError, build_ctp_gateway_trading_profile
from bt_api_py.gateway_delegation_service import GatewayDelegationSettings
from bt_api_py.gateway_direct_service import (
    GatewayControlGrant,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from tests.test_iter63_ctp_gateway_trading import CLIENT_A, make
from tests.unit.test_iter63_gateway_direct_service import _context, _order, _reserve
from tests.unit.test_service_registry import _next_request, _register

RB = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
RISK = WireIdentity(WireRole.SERVICE, 'risk-principal', 'risk-actor')
EXECUTION = WireIdentity(WireRole.SERVICE, 'execution-principal', 'execution-actor')
SUBMIT = frozenset({'direct.reserve', 'direct.dispatch'})


def _grant(identity=RISK, kind='risk', operations=SUBMIT, strategy='strategy-a'):
    return GatewayDelegateGrant(identity, 'acct', strategy, 'domain', kind, 'rev1', frozenset({RB}), operations)


def test_setting_is_off_by_default_and_needs_a_risk_submit_grant():
    assert GatewayDelegationSettings((_grant(),), lambda _: True).risk_only_submit_strategies == frozenset()
    assert GatewayDelegationSettings((_grant(),), lambda _: True,
        risk_only_submit_strategies=frozenset({'strategy-a'})).risk_only_submit_strategies == {'strategy-a'}
    for grants, only in (
            ((_grant(),), frozenset({'strategy-b'})),                         # no Risk grant for it
            ((_grant(operations=frozenset({'direct.reserve'})),), frozenset({'strategy-a'})),  # no dispatch
            ((_grant(), _grant(EXECUTION, 'execution')), frozenset({'strategy-a'})),       # another SUBMIT holder
            ((_grant(), _grant(EXECUTION, 'execution', frozenset({'direct.dispatch'}))), frozenset({'strategy-a'})),
            ((_grant(),), {'strategy-a'}),                                     # not a frozenset
            ((_grant(),), frozenset({''}))):
        with pytest.raises(ValueError, match='INVALID_GATEWAY_DELEGATION'):
            GatewayDelegationSettings(grants, lambda _: True, risk_only_submit_strategies=only)
    # Execution without SUBMIT for that strategy (cancel and status only) is allowed.
    reads = frozenset({'direct.command_status', 'direct.cancel'})
    assert GatewayDelegationSettings((_grant(), _grant(EXECUTION, 'execution', reads)), lambda _: True,
                                     risk_only_submit_strategies=frozenset({'strategy-a'}))


def _registry_for(binding, operations):
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal-a', 'strategy-a')
    scope = WireScope('paper:demo', account_ref='account-a', strategy_ref='strategy-a', run_id='run-a')
    resource = ResourceGrant('gateway-a', 'service.direct_write', 'scope-a', replace(scope, run_id=None), operations)
    registry = ServiceRegistry(':memory:', service_id='gateway-service',
        capabilities=(ServiceCapability('gateway-a', 'service.direct_write', operations, operations),),
        grants={'principal-a': (PrincipalGrant(identity, (resource,)),)},
        policy_version='p', requirements_version='r')
    for operation in operations:
        registry.register_handler('gateway-a', operation,
            lambda ctx, args, operation=operation: binding.handle(operation, context=ctx, arguments=args))
    result = _register(registry, principal='principal-a', identity=identity, scope=scope,
        instance_id='gateway-a', port='service.direct_write', scope_ref='scope-a', operations=tuple(operations))
    assert result['ok'], result
    registration = result['result']

    def call(operation, arguments):
        response = registry.handle('principal-a', _next_request(operation, {
            'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
            'instance_id': 'gateway-a', 'arguments': arguments}, identity=identity, scope=scope))
        assert response['ok'], response
        return response['result']
    return registry, call


def test_binding_refuses_the_listed_strategys_own_submit_even_with_a_registry_grant(tmp_path):
    journal = GatewayDirectCommandJournal.create(tmp_path / 'direct.sqlite', 'account-a', generation='g1')
    grants = {'principal-a': GatewayDirectGrant('strategy-a', frozenset({'rb'})),
              'principal-b': GatewayDirectGrant('strategy-b', frozenset({'sa'}))}
    with pytest.raises(ValueError, match='INVALID_DIRECT_GRANTS'):
        GatewayDirectServiceBinding(journal, grants, risk_only_submit_strategies=frozenset({'strategy-c'}))
    with pytest.raises(ValueError, match='INVALID_DIRECT_GRANTS'):
        GatewayDirectServiceBinding(journal, grants, risk_only_submit_strategies={'strategy-a'})
    off = GatewayDirectServiceBinding(journal, grants)
    assert _reserve(off, _context(), key='off-1')['status'] == 'OK'
    on = GatewayDirectServiceBinding(journal, grants, risk_only_submit_strategies=frozenset({'strategy-a'}))
    assert _reserve(on, _context(), key='on-1') == {'status': 'REQUEST_REJECTED', 'code': 'SUBMIT_NOT_GRANTED'}
    other = _context(principal='principal-b', strategy='strategy-b')
    assert _reserve(on, other, key='b-1', payload=_order('b-1', symbol='sa'))['status'] == 'OK'
    # Through a Registry-issued admission as well: reserve and dispatch are both SUBMIT.
    registry, call = _registry_for(on, frozenset({'direct.reserve', 'direct.dispatch', 'direct.command_status'}))
    try:
        refused = call('direct.reserve', {'request_key': 'on-2', 'payload': _order('on-2'), 'ttl_seconds': 10})
        assert refused == {'status': 'REQUEST_REJECTED', 'code': 'SUBMIT_NOT_GRANTED'}
        dispatch = call('direct.dispatch', {'request_key': 'off-1', 'reservation_id': 'r', 'digest': 'd',
                                            'expected_version': 1})
        assert dispatch == {'status': 'REQUEST_REJECTED', 'code': 'SUBMIT_NOT_GRANTED'}
        assert call('direct.command_status', {'request_key': 'off-1'})['status'] == 'OK'
    finally:
        registry.close()


def _profile(tmp_path, monkeypatch, *, strategy_ops, risk_only):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    raw['components']['gateway']['settings']['options'].update(control_enabled=True, strategy_ledger_enabled=True,
        account_extensions_enabled=True, delegation_enabled=True)
    config = parse_service_config(raw)
    domain = authority.scope.execution_domain_ref
    account_scope = WireScope(domain, account_ref=settings.account_scope)
    template = WireScope(domain, account_ref=settings.account_scope, strategy_ref=CLIENT_A.actor_ref)
    operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'operator-actor')
    risk = WireIdentity(WireRole.SERVICE, 'risk-principal', 'risk-actor')
    control_ops = frozenset({'direct.control.status'})
    delegate_ops = frozenset({'direct.delegated_reserve', 'direct.delegated_dispatch'})
    strategy_entry = authority.grants[CLIENT_A.principal_ref][0]
    strategy_resource = replace(strategy_entry.resources[0], operations=strategy_ops)
    op_key, _ = zmq.curve_keypair()
    risk_key, _ = zmq.curve_keypair()
    authority = replace(authority, principals={**authority.principals, op_key: 'operator', risk_key: 'risk-principal'},
        grants={CLIENT_A.principal_ref: (replace(strategy_entry, resources=(strategy_resource,)),),
                'operator': (PrincipalGrant(operator, (ResourceGrant('gateway-a', 'service.direct_write',
                    config.scope_ref, account_scope, control_ops),)),),
                'risk-principal': (PrincipalGrant(risk, (ResourceGrant('gateway-a', 'service.direct_write',
                    config.scope_ref, template, delegate_ops),)),)})
    key = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
    grant = GatewayDelegateGrant(risk, settings.account_scope, CLIENT_A.actor_ref, domain, 'risk', 'rev1',
                                 frozenset({key}), SUBMIT)
    delegation = GatewayDelegationSettings((grant,), lambda instrument: instrument == key,
        risk_only_submit_strategies=frozenset({CLIENT_A.actor_ref}) if risk_only else frozenset())
    profile = build_ctp_gateway_trading_profile(config, authority, tmp_path / 'registry.sqlite',
        profile_ref='risk-only-profile', settings=settings, grants=grants, request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None, assert_admitted=lambda _: True,
        control_grants={'operator': GatewayControlGrant('operator-actor', True, frozenset(), control_ops)},
        upgrade_controls_from_v9=True, upgrade_strategy_ledger_from_v10=True,
        upgrade_account_extensions_from_v11=True, delegation_settings=delegation)
    return profile, settings, config, template, risk, key, delegate_ops


def _exercise(profile, settings, config, template, risk, key, delegate_ops, strategy_ops):
    deployment = profile.deployment_factory()
    deployment.start()
    try:
        registry = deployment._host._registry
        scope = replace(template, run_id='run')

        def register(principal, identity, ops, name):
            return _register(registry, principal=principal, identity=identity, scope=scope, key=name,
                instance_id='gateway-a', port='service.direct_write', scope_ref=config.scope_ref,
                operations=tuple(sorted(ops)))
        own_submit = register(CLIENT_A.principal_ref, CLIENT_A, {'direct.reserve', 'direct.command_status'}, 'own')
        strategy = own_submit if own_submit['ok'] else register(CLIENT_A.principal_ref, CLIENT_A, strategy_ops, 'kept')
        assert strategy['ok'], strategy
        actor = register('risk-principal', risk, delegate_ops, 'risk')
        assert actor['ok'], actor
        payload = OrderRequest(symbol='rb', side=Side.BUY, order_type=OrderType.LIMIT, quantity=Decimal('1'),
            price=Decimal('1'), account_id=settings.account_scope, client_order_id='client', idempotency_key='order',
            offset='open', quantity_unit='lots', exchange_id='SHFE').to_dict()
        command = GatewayDelegatedCommand(settings.account_scope, CLIENT_A.actor_ref, template.execution_domain_ref,
            risk.principal_ref, risk.actor_ref, 'risk', 'run', 'order', gateway_payload_digest(payload), key, 'rev1')
        rec = actor['result']
        delegated = registry.handle(risk.principal_ref, _next_request('direct.delegated_reserve', {
            'registration_id': rec['registration_id'], 'service_epoch': rec['service_epoch'],
            'instance_id': 'gateway-a', 'arguments': {'command': command.to_mapping(), 'payload': payload,
                                                      'ttl_seconds': 10}}, identity=risk, scope=scope))
        assert delegated['ok'] and delegated['result']['actor_principal'] == risk.principal_ref, delegated
        assert delegated['result']['strategy_scope'] == CLIENT_A.actor_ref
        return own_submit
    finally:
        deployment.close()


def _own_submit_on_binding(profile, settings, config, template):
    """The composed strategy binding itself, below the Registry (defence in depth)."""
    deployment = profile.deployment_factory()
    resources = deployment._provide_resources('gateway', 'gateway-a',
        dict(config.components['gateway']['settings']['options']), WireScope(template.execution_domain_ref))
    try:
        context = _context(principal=CLIENT_A.principal_ref, strategy=CLIENT_A.actor_ref,
                           account=settings.account_scope)
        return resources.values['binding'].handle('direct.reserve', context=context, arguments={
            'request_key': 'own-1', 'payload': _order('own-1', account=settings.account_scope), 'ttl_seconds': 10})
    finally:
        resources.close()


def test_profile_default_keeps_the_strategys_own_submit(tmp_path, monkeypatch):
    ops = frozenset({'direct.reserve', 'direct.release', 'direct.command_status', 'direct.dispatch'})
    parts = _profile(tmp_path, monkeypatch, strategy_ops=ops, risk_only=False)
    own_submit = _exercise(*parts, ops)
    assert own_submit['ok'], own_submit   # the strategy still registers its own SUBMIT
    # Off: the ordinary reserve path runs (here it stops at the synthetic ledger's missing baseline).
    assert _own_submit_on_binding(*parts[:4]) == {'status': 'REQUEST_REJECTED', 'code': 'STRATEGY_LEDGER_UNAVAILABLE'}


def test_profile_with_the_setting_leaves_submit_to_risk_only(tmp_path, monkeypatch):
    kept = frozenset({'direct.release', 'direct.command_status'})   # release keeps the run's write claim
    parts = _profile(tmp_path, monkeypatch, strategy_ops=kept, risk_only=True)
    own_submit = _exercise(*parts, kept)
    assert not own_submit['ok'] and own_submit['code'] == 'SCOPE_DENIED', own_submit
    assert _own_submit_on_binding(*parts[:4]) == {'status': 'REQUEST_REJECTED', 'code': 'SUBMIT_NOT_GRANTED'}


@pytest.mark.parametrize('extra', ['direct.reserve', 'direct.dispatch'])
def test_profile_refuses_a_strategy_grant_that_contradicts_the_setting(tmp_path, monkeypatch, extra):
    ops = frozenset({'direct.release', 'direct.command_status', extra})
    (tmp_path / 'off').mkdir()
    _profile(tmp_path / 'off', monkeypatch, strategy_ops=ops, risk_only=False)   # valid while the setting is off
    with pytest.raises(CtpGatewayTradingError, match='INVALID_TRADING_CONFIGURATION'):
        _profile(tmp_path, monkeypatch, strategy_ops=ops, risk_only=True)
