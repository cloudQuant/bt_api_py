"""Synthetic Registry-issued SERVICE delegation; no real provider writes."""

from decimal import Decimal
from dataclasses import replace
from contextlib import contextmanager
from types import SimpleNamespace
import threading
import time

from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_delegation import GatewayDelegateGrant, authorize_delegated_command
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome, _identity
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_base.direct_write_protocol import NativeOrderIdentity, PreDispatchRecord, WriteOperation
from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_delegation_service import GatewayDelegationService, GatewayDelegationSettings
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_service_registry import _next_request, _register
from test_iter63_direct_delegation import KEY


def test_delegate_reserve_requires_trusted_service_grant_and_live_strategy_run(tmp_path):
    root=tmp_path/'guards'; root.mkdir()
    holder={'entered':threading.Event(),'allow':threading.Event(),'block':False,'sent':0}
    def factory():
        guard=LocalAccountSessionGuard(root,'acct','owner'); generation=guard.acquire()
        journal=GatewayDirectCommandJournal.create(tmp_path/'db','acct',generation=generation)
        journal.enable_controls(guard); journal.enable_strategy_ledger(guard); journal.enable_account_extensions(guard)
        def place(payload, port, account):
            if holder['block']:
                holder['entered'].set()
                assert holder['allow'].wait(3)
            identity = NativeOrderIdentity('CTP','ref',{'trading_day':'20300101'},1)
            marker = port.commit(PreDispatchRecord(operation=WriteOperation.PLACE,host_scope=account,
                exchange_name='CTP',symbol=KEY.native_symbol,identity=identity,target_identity=None,
                request_summary=payload,trading_day='20300101',session_generation=1,created_at_utc='synthetic'))
            holder['sent'] += 1
            return NativeDispatchOutcome(True,identity,marker)
        dispatcher=GatewayDirectDispatcher(journal,'CTP',guard,place,lambda _:('key',),lambda _:True)
        def cancel(target,symbol,port,account):
            prep=PreparedCancelRequest('provider-reservation',target,symbol,'20300102',2,{'request_id':12},('cancel-key',))
            marker=port.commit(PreDispatchRecord(WriteOperation.CANCEL,account,'CTP',symbol,None,target,{},
                prep.trading_day,prep.session_generation,'synthetic',prep))
            return NativeDispatchOutcome(True,target,marker)
        cancel_dispatcher=GatewayDirectCancelDispatcher(GatewayDirectCancelJournal(journal),dispatcher,guard,
                                                       'CTP',cancel,lambda _:True)
        holder['journal']=journal
        return GatewayDirectOwnerResources(dispatcher,journal,guard.release,cancel_dispatcher=cancel_dispatcher)
    owner=GatewayDirectOwner(factory,queue_size=4,max_queue_wait_seconds=1,busy_timeout_seconds=1)
    assert owner.start(2)
    registry=None
    try:
        strategy=WireIdentity(WireRole.STRATEGY_CLIENT,'strategy-principal','strategy')
        actor=WireIdentity(WireRole.SERVICE,'risk-principal','risk-actor')
        scope=WireScope('domain',account_ref='acct',strategy_ref='strategy',run_id='run')
        template=WireScope('domain',account_ref='acct',strategy_ref='strategy')
        strategy_ops=frozenset({'direct.reserve','direct.delegated_cancel','direct.delegated_cancel_status'})
        delegate_ops=frozenset({'direct.delegated_reserve','direct.delegated_dispatch'})
        writes=(strategy_ops|delegate_ops)-{'direct.delegated_cancel_status'}
        cap=ServiceCapability('gateway','service.direct_write',strategy_ops|delegate_ops,frozenset(writes))
        sr=ResourceGrant('gateway','service.direct_write','scope',template,strategy_ops)
        dr=ResourceGrant('gateway','service.direct_write','scope',template,delegate_ops)
        clock=[100.0]
        registry=ServiceRegistry(':memory:',service_id='gateway-service',capabilities=(cap,),
            grants={'strategy-principal':(PrincipalGrant(strategy,(sr,)),),'risk-principal':(PrincipalGrant(actor,(dr,)),)},
            policy_version='p',requirements_version='r',lease_seconds=10,clock=lambda:clock[0])
        grant=GatewayDelegateGrant(actor,'acct','strategy','domain','risk','rev1',frozenset({KEY}),
                                   frozenset({'direct.reserve','direct.dispatch'}))
        service=GatewayDelegationService(GatewayDelegationSettings((grant,),lambda instrument:instrument==KEY),
            owner=owner,journal=holder['journal'],instance_id='gateway',scope_ref='scope')
        service.configure(registry)
        registry.register_handler('gateway','direct.reserve',lambda *_:{'unused':True})
        strategy_service=GatewayDirectServiceBinding(holder['journal'],
            {'strategy-principal':GatewayDirectGrant('strategy',frozenset({KEY.native_symbol}))},owner=owner,cancel_enabled=True)
        for op in ('direct.delegated_cancel','direct.delegated_cancel_status'):
            registry.register_handler('gateway',op,lambda ctx,args,op=op:strategy_service.handle(op,context=ctx,arguments=args))
        for op in delegate_ops:
            registry.register_handler('gateway',op,lambda ctx,args,op=op:service.handle(op,ctx,args))
        def register(principal,identity,ops,key):
            return _register(registry,principal=principal,identity=identity,scope=scope,key=key,
                instance_id='gateway',port='service.direct_write',scope_ref='scope',operations=tuple(ops))
        assert not register('risk-principal',actor,delegate_ops,'early')['ok']
        strategy_registration=register('strategy-principal',strategy,strategy_ops,'strategy')
        assert strategy_registration['ok']
        clock[0]=105.0
        registration=register('risk-principal',actor,delegate_ops,'risk')
        assert registration['ok'], registration
        payload=OrderRequest(symbol=KEY.native_symbol,side=Side.BUY,order_type=OrderType.LIMIT,
            quantity=Decimal('1'),price=Decimal('1'),account_id='acct',client_order_id='client',
            idempotency_key='order-1',offset='open',quantity_unit='lots',exchange_id='SHFE').to_dict()
        command=GatewayDelegatedCommand('acct','strategy','domain','risk-principal','risk-actor','risk','run',
            'order-1',gateway_payload_digest(payload),KEY,'rev1')
        rec=registration['result']
        def call(command_value, request_payload=payload):
            return registry.handle('risk-principal',_next_request('direct.delegated_reserve',{
                'registration_id':rec['registration_id'],'service_epoch':rec['service_epoch'],'instance_id':'gateway',
                'arguments':{'command':command_value,'payload':request_payload,'ttl_seconds':10}},identity=actor,scope=scope))
        first=call(command.to_mapping())
        assert first['ok'] and first['result'].get('actor_principal')=='risk-principal', first
        assert call(command.to_mapping())['result']['reservation_id']==first['result']['reservation_id']
        dispatch = registry.handle('risk-principal',_next_request('direct.delegated_dispatch',{
            'registration_id':rec['registration_id'],'service_epoch':rec['service_epoch'],'instance_id':'gateway',
            'arguments':{'command':command.to_mapping(),'payload':payload,
                'reservation_id':first['result']['reservation_id'],'expected_version':1}},identity=actor,scope=scope))
        assert dispatch['ok'] and dispatch['result'] == {'status':'REQUEST_REJECTED','code':'DISPATCH_CLAIM_UNAVAILABLE'}, dispatch
        assert holder['journal'].get('strategy','order-1','risk-principal').status == 'RESERVED'
        assert holder['sent'] == 0
        # Seed a synthetic previously-sent delegated order directly in the
        # real journal, as historical state predating the claim gate. This is
        # not a native call or a production authorization substitute.
        @contextmanager
        def fixture_fence():
            yield
        fixture_context = SimpleNamespace(identity=actor, scope=scope,
            principal_ref='risk-principal', registration_id=rec['registration_id'])
        fixture_permit = authorize_delegated_command(fixture_context, grant, command, 'direct.dispatch', payload,
            registry_recheck=lambda _: True, current_grant=lambda: grant,
            instrument_recheck=lambda instrument: instrument == KEY)
        row = holder['journal'].get('strategy', 'order-1', 'risk-principal')
        identity = NativeOrderIdentity('CTP','ref',{'trading_day':'20300101'},1)
        native_mapping = _identity(identity, 'CTP')
        started = holder['journal'].begin_dispatch('strategy','order-1',row.reservation_id,row.digest,
            'risk-principal',row.version,native_mapping,('key',),delegation_permit=fixture_permit,
            delegation_fence=fixture_fence).record
        holder['journal'].finish('strategy','order-1',row.reservation_id,row.digest,'risk-principal',
            started.version,native_mapping,('key',),'SENT')
        strategy_rec=strategy_registration['result']
        cancel_result=registry.handle('strategy-principal',_next_request('direct.delegated_cancel',{
            'registration_id':strategy_rec['registration_id'],'service_epoch':strategy_rec['service_epoch'],
            'instance_id':'gateway','arguments':{'request_key':'order-1','reservation_id':first['result']['reservation_id'],
                'digest':command.payload_digest,'operation_id':'cancel-1'}},identity=strategy,scope=scope))
        assert cancel_result['ok'] and cancel_result['result'].get('cancel_queue_state') == 'QUEUED', cancel_result
        deadline=time.monotonic()+2
        cancel_journal=GatewayDirectCancelJournal(holder['journal'])
        while cancel_journal.get('strategy','cancel-1','strategy-principal').status == 'REQUESTED' and time.monotonic()<deadline:
            time.sleep(0.005)
        status=registry.handle('strategy-principal',_next_request('direct.delegated_cancel_status',{
            'registration_id':strategy_rec['registration_id'],'service_epoch':strategy_rec['service_epoch'],
            'instance_id':'gateway','arguments':{'operation_id':'cancel-1'}},identity=strategy,scope=scope))
        assert status['ok'] and status['result']['record']['target_actor_principal']=='risk-principal', status
        assert status['result']['record']['state']=='SENT', (status,owner.status())
        forged={**command.to_mapping(),'strategy_scope':'other'}
        assert call(forged)['result']['status']=='UNKNOWN'
        second_payload={**payload,'idempotency_key':'order-2','client_order_id':'client-2'}
        second_command=replace(command,request_key='order-2',payload_digest=gateway_payload_digest(second_payload))
        second=call(second_command.to_mapping(),second_payload)
        assert second['ok'] and second['result']['state']=='RESERVED'
        queued=registry.handle('risk-principal',_next_request('direct.delegated_dispatch',{
            'registration_id':rec['registration_id'],'service_epoch':rec['service_epoch'],'instance_id':'gateway',
            'arguments':{'command':second_command.to_mapping(),'payload':second_payload,
                'reservation_id':second['result']['reservation_id'],'expected_version':1}},identity=actor,scope=scope))
        assert queued['ok'] and queued['result'] == {'status':'REQUEST_REJECTED','code':'DISPATCH_CLAIM_UNAVAILABLE'}
        clock[0]=111.0
        with registry._read_snapshot():
            assert not registry._gateway_delegate_strategy_run_current(scope)
        clock[0]=105.0
        with registry._lock:
            registry._gateway_delegate_grants.clear()  # trusted policy revocation, not wire data
        assert holder['journal'].get('strategy','order-2','risk-principal').status == 'RESERVED'
        assert holder['sent']==0
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)
