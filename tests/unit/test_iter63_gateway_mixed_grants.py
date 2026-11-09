"""Execution may combine parent and child operations only with both explicit grants."""
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_delegation import GatewayDelegateGrant
from bt_api_py.gateway_parent_service import GatewayParentGrant
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_service_registry import _next_request, _register


def test_parent_and_delegate_union_requires_each_current_grant():
    strategy=WireIdentity(WireRole.STRATEGY_CLIENT,'strategy-principal','strategy')
    execution=WireIdentity(WireRole.SERVICE,'execution-principal','execution')
    template=WireScope('domain',account_ref='account',strategy_ref='strategy')
    scope=WireScope('domain',account_ref='account',strategy_ref='strategy',run_id='run')
    parent_op='direct.parent.register'; delegate_op='direct.delegated_reserve'; cancel_op='direct.delegate_actor_cancel'
    service_ops=frozenset({parent_op,delegate_op,cancel_op}); strategy_ops=frozenset({'direct.reserve'})
    capability=ServiceCapability('gateway','service.direct_write',service_ops|strategy_ops,service_ops|strategy_ops)
    grants={execution.principal_ref:(PrincipalGrant(execution,(ResourceGrant('gateway','service.direct_write','scope',template,service_ops),)),),
            strategy.principal_ref:(PrincipalGrant(strategy,(ResourceGrant('gateway','service.direct_write','scope',template,strategy_ops),)),)}
    registry=ServiceRegistry(':memory:',service_id='service',capabilities=(capability,),grants=grants,
        policy_version='p',requirements_version='r')
    try:
        key=MarketInstrumentKey('ctp','SHFE','simnow','future','rb','rb')
        parent=GatewayParentGrant(execution,'account','strategy','domain','execution','rev',frozenset({key}),frozenset({parent_op}))
        delegate=GatewayDelegateGrant(execution,'account','strategy','domain','execution','rev',frozenset({key}),frozenset({'direct.reserve','direct.cancel'}))
        registry.configure_gateway_parents(instance_id='gateway',scope_ref='scope',grants=(parent,))
        registry.configure_gateway_delegates(instance_id='gateway',scope_ref='scope',grants=(delegate,))
        for op in service_ops|strategy_ops:
            registry.register_handler('gateway',op,lambda *_:{'observed':True})
        def register(identity, ops, tag):
            return _register(registry,principal=identity.principal_ref,identity=identity,scope=scope,key=tag,
                instance_id='gateway',port='service.direct_write',scope_ref='scope',operations=tuple(ops))
        assert register(strategy,strategy_ops,'strategy')['ok']
        assert register(execution,frozenset({parent_op}),'parent')['ok']
        assert register(execution,frozenset({delegate_op}),'delegate')['ok']
        union=register(execution,service_ops,'both')
        assert union['ok'],union
        rec=union['result']
        def call(op):
            return registry.handle(execution.principal_ref,_next_request(op,{'registration_id':rec['registration_id'],
                'service_epoch':rec['service_epoch'],'instance_id':'gateway','arguments':{}},identity=execution,scope=scope))
        assert call(parent_op)['ok'] and call(delegate_op)['ok'] and call(cancel_op)['ok']
        with registry._lock:
            registry._gateway_parent_grants.clear()
        denied=register(execution,service_ops,'missing-parent')
        assert not denied['ok']
        assert not call(parent_op)['ok']
        with registry._lock:
            registry._gateway_parent_grants[('gateway','scope',execution.principal_ref,'account','strategy')]=parent
            registry._gateway_delegate_grants.clear()
        denied=register(execution,service_ops,'missing-delegate')
        assert not denied['ok']
        assert not call(delegate_op)['ok']
    finally:
        registry.close()
