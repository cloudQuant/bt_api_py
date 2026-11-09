"""Synthetic explicit account comparison never accepts a client-supplied cut."""

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.gateway_account_comparison import GatewayAccountComparison
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.unit.test_service_registry import _next_request, _register
from test_iter63_strategy_ledger_owner import KEY


def test_account_compare_requires_explicit_account_grant_and_returns_not_comparable(tmp_path):
    directory = tmp_path/'guards'
    directory.mkdir()
    holder = {}
    def factory():
        guard = LocalAccountSessionGuard(directory, 'acct', 'owner')
        generation = guard.acquire()
        journal = GatewayDirectCommandJournal.create(tmp_path/'db', 'acct', generation=generation)
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, lambda *_:None, lambda _:('key',), lambda _:True)
        holder['journal'] = journal
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release)
    owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=1, busy_timeout_seconds=1)
    assert owner.start(2)
    registry = None
    try:
        service = GatewayAccountComparison(owner, holder['journal'], account_scope='acct',
            principals=frozenset({'operator'}), instruments=frozenset({KEY}))
        identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'actor')
        scope = WireScope('domain', account_ref='acct')
        ops = frozenset({'account.strategy_net_comparison'})
        cap = ServiceCapability('gateway','service.account_read',ops,frozenset())
        grant = ResourceGrant('gateway','service.account_read','scope',scope,ops)
        registry = ServiceRegistry(':memory:', service_id='service', capabilities=(cap,),
            grants={'operator':(PrincipalGrant(identity,(grant,)),)},policy_version='p',requirements_version='r')
        registry.register_handler('gateway','account.strategy_net_comparison',lambda ctx,args:service.handle(ctx,args))
        registered = _register(registry,principal='operator',identity=identity,scope=scope,
            instance_id='gateway',port='service.account_read',scope_ref='scope',operations=tuple(ops))
        assert registered['ok']
        rec = registered['result']
        def call(arguments):
            return registry.handle('operator',_next_request('account.strategy_net_comparison',{
                'registration_id':rec['registration_id'],'service_epoch':rec['service_epoch'],
                'instance_id':'gateway','arguments':arguments},identity=identity,scope=scope))
        result = call({'instrument':KEY.to_dict()})
        assert result['ok'] and result['result']['status'] == 'NOT_COMPARABLE'
        forged = call({'instrument':KEY.to_dict(),'account_net':'0','cut':'fabricated'})
        assert forged['ok'] and forged['result']['code'] == 'INVALID_REQUEST'
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)


def test_ctp_v12_comparison_is_unavailable_without_provider_cut(tmp_path, monkeypatch):
    from dataclasses import replace
    import zmq
    from bt_api_py.ctp_gateway_trading import build_ctp_gateway_trading_profile
    from bt_api_py.gateway_direct_service import GatewayControlGrant
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_registry import ServiceCallContext
    from tests.test_iter63_ctp_gateway_trading import CLIENT_A, _read_profile

    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    opts = raw['components']['gateway']['settings']['options']
    opts.update(control_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True)
    config = parse_service_config(raw)
    operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'actor')
    scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    controls = frozenset({'direct.control.status'})
    direct = ResourceGrant('gateway-a','service.direct_write',config.scope_ref,scope,controls)
    read_grant = ResourceGrant('gateway-a','service.account_read',config.scope_ref,scope,
                               frozenset({'account.strategy_net_comparison'}))
    public, _ = zmq.curve_keypair()
    authority = replace(authority,principals={**authority.principals,public:'operator'},
        grants={**authority.grants,'operator':(PrincipalGrant(operator,(direct,read_grant)),)})
    profile = build_ctp_gateway_trading_profile(config,authority,tmp_path/'registry-v12.sqlite',
        profile_ref='account-v12',settings=settings,grants=grants,
        request_feed_factory=lambda:None,market_stream_factory=lambda *_:None,assert_admitted=lambda _:True,
        account_read_settings=read,account_read_principals=frozenset({CLIENT_A.principal_ref,'operator'}),
        control_grants={'operator':GatewayControlGrant('actor',True,frozenset(),controls)},
        upgrade_controls_from_v9=True,upgrade_strategy_ledger_from_v10=True,
        upgrade_account_extensions_from_v11=True)
    resources = profile.deployment_factory()._provide_resources('gateway','gateway-a',opts,authority.scope)
    try:
        assert resources.values['binding']._journal._schema_version == 12
        result = resources.values['account_comparison'].handle(
            ServiceCallContext(operator,scope,'registration','operator'),
            {'instrument':read.observation_instruments[0].to_dict()})
        assert result['status'] == 'NOT_COMPARABLE'
    finally:
        resources.close()
