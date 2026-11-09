"""Strategy reads original delegated actor through a Registry-issued fence."""

from dataclasses import replace

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_service_registry import _next_request, _register
from test_iter63_direct_delegation import PAYLOAD, fence, permit


def test_strategy_reads_own_delegated_record_without_impersonating_actor(tmp_path):
    root = tmp_path/'guards'
    root.mkdir()
    holder = {}
    def factory():
        guard = LocalAccountSessionGuard(root, 'acct', 'owner')
        generation = guard.acquire()
        journal = GatewayDirectCommandJournal.create(tmp_path/'db', 'acct', generation=generation)
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        journal.reserve('strategy','order-1','risk-principal',PAYLOAD,
                        reservation_fence=fence,delegation_permit=permit())
        dispatcher = GatewayDirectDispatcher(journal,'CTP',guard,lambda *_:None,lambda _:('key',),lambda _:True)
        holder['journal'] = journal
        return GatewayDirectOwnerResources(dispatcher,journal,guard.release)
    owner = GatewayDirectOwner(factory,queue_size=4,max_queue_wait_seconds=1,busy_timeout_seconds=1)
    assert owner.start(2)
    registry = None
    try:
        identity = WireIdentity(WireRole.STRATEGY_CLIENT,'strategy-principal','strategy')
        scope = WireScope('domain',account_ref='acct',strategy_ref='strategy',run_id='run')
        ops = frozenset({'direct.delegated_command_status'})
        capability = ServiceCapability('gateway','service.direct_write',ops,frozenset())
        resource = ResourceGrant('gateway','service.direct_write','scope',replace(scope,run_id=None),ops)
        registry = ServiceRegistry(':memory:',service_id='gateway-service',capabilities=(capability,),
            grants={'strategy-principal':(PrincipalGrant(identity,(resource,)),)},policy_version='p',requirements_version='r')
        binding = GatewayDirectServiceBinding(holder['journal'],
            {'strategy-principal':GatewayDirectGrant('strategy',frozenset({'rb2601'}))},owner=owner)
        registry.register_handler('gateway','direct.delegated_command_status',
            lambda ctx,args:binding.handle('direct.delegated_command_status',context=ctx,arguments=args))
        registered = _register(registry,principal='strategy-principal',identity=identity,scope=scope,
            instance_id='gateway',port='service.direct_write',scope_ref='scope',operations=tuple(ops))
        assert registered['ok'], registered
        rec=registered['result']
        result=registry.handle('strategy-principal',_next_request('direct.delegated_command_status',{
            'registration_id':rec['registration_id'],'service_epoch':rec['service_epoch'],'instance_id':'gateway',
            'arguments':{'request_key':'order-1'}},identity=identity,scope=scope))
        assert result['ok'] and result['result']['actor_principal'] == 'risk-principal'
        assert result['result']['strategy_scope'] == 'strategy'
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)
