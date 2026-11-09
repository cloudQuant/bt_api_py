"""Synthetic account READ grant, independent journal cursor, and Registry fences."""
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from types import MappingProxyType
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.gateway_account_observer import GatewayAccountObserver, ACCOUNT_OBSERVER_OPERATIONS
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_service_registry import _next_request, _register
from test_iter63_direct_execution_journal import command, fact


def test_account_observer_rpc_requires_read_grant_and_keeps_separate_cursor(tmp_path):
    guard_dir = tmp_path/'guards'
    guard_dir.mkdir()
    holder = {}
    def factory():
        guard = LocalAccountSessionGuard(guard_dir, 'acct', 'owner')
        generation = guard.acquire()
        journal = GatewayDirectCommandJournal.create(tmp_path/'db', 'acct', generation=generation,
                                                       max_records=20, execution_max_facts=20)
        ledger = GatewayDirectExecutionLedger(journal, guard)
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        command(journal, strategy='s1', key='one', anchor='a')
        command(journal, strategy='s2', key='two', anchor='b')
        assert ledger.ingest(fact(keys=('a',), binding=('a',), venue='a', trade_key=('SHFE', 'trade1'))).disposition == 'ATTRIBUTED'
        assert ledger.ingest(fact(keys=('b',), binding=('b',), venue='b', trade_key=('SHFE', 'trade2'))).disposition == 'ATTRIBUTED'
        holder.update(journal=journal, ledger=ledger)
        dispatcher = GatewayDirectDispatcher(journal,'CTP',guard,lambda *_:None,lambda _:('key',),lambda _:True)
        return GatewayDirectOwnerResources(dispatcher,journal,guard.release)
    owner = GatewayDirectOwner(factory,queue_size=4,max_queue_wait_seconds=1,busy_timeout_seconds=1)
    assert owner.start(2)
    registry = None
    try:
        service = GatewayAccountObserver(owner,holder['journal'],holder['ledger'],account_scope='acct',
            execution_domain_ref='domain',principals=frozenset({'operator','service'}))
        operator=WireIdentity(WireRole.APPLICATION_CLIENT,'operator','actor')
        service_identity=WireIdentity(WireRole.SERVICE,'service','service-actor')
        scope=WireScope('domain',account_ref='acct')
        capability=ServiceCapability('gateway','service.account_read',ACCOUNT_OBSERVER_OPERATIONS,frozenset())
        grant=ResourceGrant('gateway','service.account_read','scope',scope,ACCOUNT_OBSERVER_OPERATIONS)
        registry=ServiceRegistry(':memory:',service_id='service',capabilities=(capability,),
            grants={'operator':(PrincipalGrant(operator,(grant,)),), 'service':(PrincipalGrant(service_identity,(grant,)),)},
            policy_version='p',requirements_version='r')
        for operation in ACCOUNT_OBSERVER_OPERATIONS:
            registry.register_handler('gateway',operation,lambda ctx,args,op=operation:service.handle(op,ctx,args))
        def register(principal, identity):
            result=_register(registry,principal=principal,identity=identity,scope=scope,instance_id='gateway',
                port='service.account_read',scope_ref='scope',operations=tuple(ACCOUNT_OBSERVER_OPERATIONS))
            assert result['ok'], result
            return result['result']
        def call(principal,identity,rec,op,args):
            return registry.handle(principal,_next_request(op,{'registration_id':rec['registration_id'],
                'service_epoch':rec['service_epoch'],'instance_id':'gateway','arguments':args},identity=identity,scope=scope))
        reg=register('operator',operator)
        service_reg=register('service',service_identity)
        page=call('operator',operator,reg,'account.events.read_after',
                  {'after_sequence':0,'expected_epoch':None,'limit':10})
        assert page['ok'] and page['result']['status']=='OK', page
        page=page['result']['page']
        assert [event['strategy_scope'] for event in page['events']]==['s1','s2']
        assert {event['command_principal'] for event in page['events']}=={'principal'}
        ack=call('operator',operator,reg,'account.events.ack',
                 {'sequence':page['offered_sequence'],'expected_epoch':page['epoch']})
        assert ack['ok'] and ack['result'] == {
            'status': 'REQUEST_REJECTED', 'code': 'ACCOUNT_OBSERVER_HANDOFF_UNAVAILABLE'}, ack
        own_progress=call('operator',operator,reg,'account.events.progress',{})
        assert own_progress['result']['progress']['acknowledged_sequence'] == 0
        progress=call('service',service_identity,service_reg,'account.events.progress',{})
        assert progress['ok'] and progress['result']['progress']['offered_sequence']==0, progress
        assert progress['result']['progress']['consumer_ref'] != page['consumer_ref']
        forged=call('operator',operator,reg,'account.events.read_after',
                    {'after_sequence':0,'expected_epoch':None,'limit':10,'consumer_ref':'forged'})
        assert forged['ok'] and forged['result']['code']=='INVALID_REQUEST', forged
        with registry._lock:
            registry._grants = MappingProxyType({**registry._grants, 'operator': ()})
        denied=call('operator',operator,reg,'account.events.progress',{})
        assert not denied['ok']
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)


def test_synthetic_ctp_profile_installs_explicit_account_observer(tmp_path, monkeypatch):
    from dataclasses import replace
    import zmq
    from bt_api_py.ctp_gateway_trading import build_ctp_gateway_trading_profile, CtpGatewayReturnSettings
    from bt_api_py.gateway_direct_service import GatewayControlGrant
    from bt_api_py.service_config import parse_service_config
    from tests.test_iter63_ctp_gateway_trading import CLIENT_A, FakeReturnMaintenance, _read_profile
    import bt_api_py.ctp_gateway_return_maintenance as return_module
    import bt_api_py.gateway_instrument_service as instrument_module

    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch)
    class Returns(FakeReturnMaintenance):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._ledger = self.ledger
    monkeypatch.setattr(return_module, 'CtpGatewayReturnMaintenance', Returns)
    class InstrumentStub:
        def __init__(self, owner, session, journal, read_service, *args):
            self.owner, self.journal, self.read_service = owner, journal, read_service
        def matches(self, binding, read_service):
            return (binding._owner is self.owner and binding._journal is self.journal
                    and read_service is self.read_service)
        def begin_close(self):
            pass
        def maintain_registrations(self, contexts):
            pass
        def cleanup_on_owner(self):
            return True
    monkeypatch.setattr(instrument_module, 'GatewayInstrumentService', InstrumentStub)
    raw = config.to_public_dict()
    opts = raw['components']['gateway']['settings']['options']
    opts.update(control_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True)
    config = parse_service_config(raw)
    operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'observer', 'actor')
    scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    controls = frozenset({'direct.control.status'})
    direct = ResourceGrant('gateway-a','service.direct_write',config.scope_ref,scope,controls)
    account_ops = ACCOUNT_OBSERVER_OPERATIONS | {'account.strategy_net_comparison'}
    read_grant = ResourceGrant('gateway-a','service.account_read',config.scope_ref,scope,account_ops)
    public, _ = zmq.curve_keypair()
    authority = replace(authority,principals={**authority.principals,public:'observer'},
        grants={**authority.grants,'observer':(PrincipalGrant(operator,(direct,read_grant)),)})
    profile = build_ctp_gateway_trading_profile(config,authority,tmp_path/'observer-registry.sqlite',
        profile_ref='observer-v12',settings=settings,grants=grants,
        request_feed_factory=lambda:None,market_stream_factory=lambda *_:None,assert_admitted=lambda _:True,
        return_settings=CtpGatewayReturnSettings(read.observation_instruments),
        account_read_settings=read,account_read_principals=frozenset({CLIENT_A.principal_ref,'observer'}),
        control_grants={'observer':GatewayControlGrant('actor',True,frozenset(),controls)},
        upgrade_controls_from_v9=True,upgrade_strategy_ledger_from_v10=True,
        upgrade_account_extensions_from_v11=True)
    deployment = profile.deployment_factory()
    resources = deployment._provide_resources('gateway', 'gateway-a', opts, authority.scope)
    try:
        observer = resources.values['account_observer']
        assert observer.principals == frozenset({'observer'})
        assert observer.owner.is_bound_to(observer.journal)
    finally:
        resources.close()
