"""Synthetic CTP Host installs explicit delegated SERVICE authority."""

from dataclasses import replace
from decimal import Decimal
import zmq

from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_delegation import GatewayDelegateGrant
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.ctp_gateway_trading import build_ctp_gateway_trading_profile
from bt_api_py.gateway_delegation_service import GatewayDelegationSettings
from bt_api_py.gateway_direct_service import GatewayControlGrant
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.test_iter63_ctp_gateway_trading import CLIENT_A, make
from tests.unit.test_service_registry import _next_request, _register


def test_ctp_host_installs_only_explicit_service_delegation(tmp_path, monkeypatch):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    raw=config.to_public_dict(); opts=raw['components']['gateway']['settings']['options']
    opts.update(control_enabled=True,strategy_ledger_enabled=True,account_extensions_enabled=True,delegation_enabled=True)
    config=parse_service_config(raw)
    account_scope=WireScope(authority.scope.execution_domain_ref,account_ref=settings.account_scope)
    strategy_template=WireScope(authority.scope.execution_domain_ref,account_ref=settings.account_scope,
                                strategy_ref=CLIENT_A.actor_ref)
    operator=WireIdentity(WireRole.APPLICATION_CLIENT,'operator','operator-actor')
    risk=WireIdentity(WireRole.SERVICE,'risk-principal','risk-actor')
    control_ops=frozenset({'direct.control.status'})
    delegate_ops=frozenset({'direct.delegated_reserve','direct.delegated_dispatch'})
    op_grant=ResourceGrant('gateway-a','service.direct_write',config.scope_ref,account_scope,control_ops)
    risk_grant=ResourceGrant('gateway-a','service.direct_write',config.scope_ref,strategy_template,delegate_ops)
    op_key,_=zmq.curve_keypair(); risk_key,_=zmq.curve_keypair()
    authority=replace(authority,principals={**authority.principals,op_key:'operator',risk_key:'risk-principal'},
        grants={**authority.grants,'operator':(PrincipalGrant(operator,(op_grant,)),),
                'risk-principal':(PrincipalGrant(risk,(risk_grant,)),)})
    key=MarketInstrumentKey('ctp','SHFE','simnow','future','rb','rb')
    grant=GatewayDelegateGrant(risk,settings.account_scope,CLIENT_A.actor_ref,authority.scope.execution_domain_ref,
                                'risk','rev1',frozenset({key}),frozenset({'direct.reserve','direct.dispatch'}))
    profile=build_ctp_gateway_trading_profile(config,authority,tmp_path/'registry.sqlite',profile_ref='delegate-profile',
        settings=settings,grants=grants,request_feed_factory=lambda:None,market_stream_factory=lambda *_:None,
        assert_admitted=lambda _:True,control_grants={'operator':GatewayControlGrant('operator-actor',True,frozenset(),control_ops)},
        upgrade_controls_from_v9=True,upgrade_strategy_ledger_from_v10=True,upgrade_account_extensions_from_v11=True,
        delegation_settings=GatewayDelegationSettings((grant,),lambda instrument:instrument==key))
    deployment=profile.deployment_factory(); deployment.start()
    try:
        registry=deployment._host._registry
        scope=replace(strategy_template,run_id='run')
        strategy=_register(registry,principal=CLIENT_A.principal_ref,identity=CLIENT_A,scope=scope,key='strategy',
            instance_id='gateway-a',port='service.direct_write',scope_ref=config.scope_ref,operations=('direct.reserve',))
        assert strategy['ok'], strategy
        actor=_register(registry,principal='risk-principal',identity=risk,scope=scope,key='risk',
            instance_id='gateway-a',port='service.direct_write',scope_ref=config.scope_ref,operations=tuple(delegate_ops))
        assert actor['ok'], actor
        payload=OrderRequest(symbol='rb',side=Side.BUY,order_type=OrderType.LIMIT,quantity=Decimal('1'),price=Decimal('1'),
            account_id=settings.account_scope,client_order_id='client',idempotency_key='order',offset='open',
            quantity_unit='lots',exchange_id='SHFE').to_dict()
        command=GatewayDelegatedCommand(settings.account_scope,CLIENT_A.actor_ref,authority.scope.execution_domain_ref,
            risk.principal_ref,risk.actor_ref,'risk','run','order',gateway_payload_digest(payload),key,'rev1')
        rec=actor['result']
        result=registry.handle(risk.principal_ref,_next_request('direct.delegated_reserve',{
            'registration_id':rec['registration_id'],'service_epoch':rec['service_epoch'],'instance_id':'gateway-a',
            'arguments':{'command':command.to_mapping(),'payload':payload,'ttl_seconds':10}},identity=risk,scope=scope))
        assert result['ok'] and result['result']['actor_principal']=='risk-principal', result
    finally:
        deployment.close()
