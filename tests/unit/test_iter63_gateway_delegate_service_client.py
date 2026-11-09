"""Risk/Execution client persists intents and never resends an ambiguous dispatch."""
from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.gateway_delegate_service_client import GatewayDelegateRoute, GatewayDelegateServiceClient
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime, BindingSnapshot, RuntimeSnapshot
from tests.unit.test_iter63_gateway_api import order


class Host:
    def __init__(self):
        self.intent, self.observation, self.marker = {}, {}, {}
    def save_delegate_intent(self, value):
        self.intent[value.command.request_key] = value
        return value
    def load_delegate_intent(self, key):
        return self.intent.get(key)
    def save_delegate_observation(self, key, value):
        self.observation[key] = value
        return value
    def load_delegate_observation(self, key):
        return self.observation.get(key)
    def save_delegate_dispatch_marker(self, key, value):
        self.marker[key] = value
        return value
    def load_delegate_dispatch_marker(self, key):
        return self.marker.get(key)


def test_service_client_durable_intent_marker_and_no_ambiguous_resend(monkeypatch):
    request = order()
    identity = WireIdentity(WireRole.SERVICE, 'risk-principal', 'risk')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
    route = GatewayDelegateRoute('upstream.direct_write', 'gateway', 'instance', 'scope', scope, identity)
    cmd = GatewayDelegatedCommand('account', 'strategy', 'domain', 'risk-principal', 'risk', 'risk',
        'run', request.idempotency_key, gateway_payload_digest(request.to_dict()),
        MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb'), 'grant')
    runtime = ThreadedServiceClientRuntime(lambda: None)
    operations = frozenset({'direct.delegated_reserve', 'direct.delegated_dispatch'})
    binding = BindingSnapshot('registration', 'epoch', True, route.mount, route.service_id,
                              route.instance_id, route.scope_ref, scope, operations, True)
    snapshot = RuntimeSnapshot('running', True, True, None, (binding,), 0, 0, identity, scope.run_id)
    monkeypatch.setattr(runtime, 'status', lambda: snapshot)
    host = Host()
    client = GatewayDelegateServiceClient(runtime, route=route, host=host)
    calls = []
    def rpc(_mount, op, args, **_kwargs):
        calls.append(op)
        assert host.intent[cmd.request_key].command == cmd
        if op == 'direct.delegated_reserve':
            return {'status': 'OBSERVED', 'schema_version': 1, 'account_scope': 'account',
                'strategy_scope': 'strategy', 'actor_principal': 'risk-principal', 'request_key': cmd.request_key,
                'reservation_id': 'reservation', 'digest': cmd.payload_digest, 'state': 'RESERVED', 'version': 1,
                'owner_generation': 'owner', 'reservation_generation': 'owner'}
        assert host.marker[cmd.request_key] == ('reservation', cmd.payload_digest, 1, cmd.digest)
        raise TimeoutError('lost')
    monkeypatch.setattr(runtime, 'call', rpc)
    assert client.reserve(cmd, request).outcome == 'OBSERVED'
    assert client.dispatch(cmd.request_key).outcome == 'UNKNOWN'
    assert client.dispatch(cmd.request_key).code == 'DISPATCH_ALREADY_ATTEMPTED'
    assert calls == ['direct.delegated_reserve', 'direct.delegated_dispatch']


def test_delegate_never_sends_before_intent_is_durable(monkeypatch):
    request = order()
    identity = WireIdentity(WireRole.SERVICE, 'risk-principal', 'risk')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
    route = GatewayDelegateRoute('upstream.direct_write', 'gateway', 'instance', 'scope', scope, identity)
    cmd = GatewayDelegatedCommand('account', 'strategy', 'domain', 'risk-principal', 'risk', 'risk', 'run',
        request.idempotency_key, gateway_payload_digest(request.to_dict()),
        MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb'), 'grant')
    runtime = ThreadedServiceClientRuntime(lambda: None)
    host = Host()
    monkeypatch.setattr(host, 'save_delegate_intent', lambda _intent: None)
    monkeypatch.setattr(runtime, 'call', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('sent')))
    client = GatewayDelegateServiceClient(runtime, route=route, host=host)
    assert client.reserve(cmd, request).code == 'INTENT_NOT_DURABLE'


def test_client_registry_owner_dispatch_synthetic(monkeypatch, tmp_path):
    import time
    from decimal import Decimal
    from bt_api_base.direct_write_protocol import NativeOrderIdentity, PreDispatchRecord, WriteOperation
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_gateway.direct_delegation import GatewayDelegateGrant
    from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome
    from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
    from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
    from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
    from bt_api_gateway.session_guard import LocalAccountSessionGuard
    from bt_api_py._contracts.models import OrderRequest, OrderType, Side
    from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
    from bt_api_py.gateway_delegation_service import GatewayDelegationService, GatewayDelegationSettings
    from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
    from bt_api_py.gateway_direct_client import GatewayClientRoute
    from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
    from bt_api_py.gateway_api import GatewayBtApi
    from tests.unit.test_iter63_gateway_strategy_client import parts, config_data
    from bt_api_py.service_config import parse_client_config
    from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
    from tests.unit.test_iter63_gateway_strategy_delegated import Host as CancelHost
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
    from tests.unit.test_service_registry import _next_request, _register

    directory = tmp_path / 'guards'
    directory.mkdir()
    stored = {}
    def factory():
        guard = LocalAccountSessionGuard(directory, 'account', 'owner')
        journal = GatewayDirectCommandJournal.create(tmp_path / 'db', 'account', generation=guard.acquire())
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        def place(payload, port, account):
            native = NativeOrderIdentity('CTP', 'ref', {'trading_day': '20300101'}, 1)
            marker = port.commit(PreDispatchRecord(operation=WriteOperation.PLACE, host_scope=account,
                exchange_name='CTP', symbol='rb', identity=native, target_identity=None, request_summary=payload,
                trading_day='20300101', session_generation=1, created_at_utc='synthetic'))
            return NativeDispatchOutcome(True, native, marker)
        from iter63_claim_support import SyntheticClaimPort, claiming
        dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, claiming(place), lambda _: ('key',), lambda _: True,
                                             claim_port=SyntheticClaimPort())
        def cancel(target, symbol, port, account):
            prep = PreparedCancelRequest('provider-reservation', target, symbol, '20300102', 2,
                                         {'request_id': 12}, ('cancel-key',))
            marker = port.commit(PreDispatchRecord(WriteOperation.CANCEL, account, 'CTP', symbol, None,
                target, {}, prep.trading_day, prep.session_generation, 'synthetic', prep))
            return NativeDispatchOutcome(True, target, marker)
        cancel_dispatcher = GatewayDirectCancelDispatcher(GatewayDirectCancelJournal(journal), dispatcher,
                                                            guard, 'CTP', cancel, lambda _: True)
        stored['journal'] = journal
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release, cancel_dispatcher=cancel_dispatcher)
    owner = GatewayDirectOwner(factory, queue_size=2, max_queue_wait_seconds=1, busy_timeout_seconds=1)
    assert owner.start(2)
    registry = None
    try:
        actor = WireIdentity(WireRole.SERVICE, 'risk-principal', 'risk')
        strategy = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
        scope = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
        template = WireScope('domain', account_ref='account', strategy_ref='strategy')
        actor_ops = frozenset({'direct.delegated_reserve', 'direct.delegated_dispatch'})
        strategy_ops = frozenset({'direct.reserve', 'direct.delegated_command_status',
                                  'direct.delegated_cancel', 'direct.delegated_cancel_status'})
        read_ops = {'direct.delegated_command_status', 'direct.delegated_cancel_status'}
        cap = ServiceCapability('gateway', 'service.direct_write', actor_ops | strategy_ops, (actor_ops | strategy_ops) - read_ops)
        grants = {'risk-principal': (PrincipalGrant(actor, (ResourceGrant('gateway', 'service.direct_write', 'scope', template, actor_ops),)),),
                  'principal': (PrincipalGrant(strategy, (ResourceGrant('gateway', 'service.direct_write', 'scope', template, strategy_ops),)),)}
        registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(cap,), grants=grants,
                                   policy_version='p', requirements_version='r')
        instrument = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
        grant = GatewayDelegateGrant(actor, 'account', 'strategy', 'domain', 'risk', 'grant',
                                     frozenset({instrument}), frozenset({'direct.reserve', 'direct.dispatch'}))
        service = GatewayDelegationService(GatewayDelegationSettings((grant,), lambda value: value == instrument),
            owner=owner, journal=stored['journal'], instance_id='gateway', scope_ref='scope')
        service.configure(registry)
        registry.register_handler('gateway', 'direct.reserve', lambda *_: {})
        strategy_service = GatewayDirectServiceBinding(stored['journal'],
            {'principal': GatewayDirectGrant('strategy', frozenset({'rb'}))}, owner=owner, cancel_enabled=True)
        for operation in strategy_ops - {'direct.reserve'}:
            registry.register_handler('gateway', operation, lambda ctx, args, op=operation:
                strategy_service.handle(op, context=ctx, arguments=args))
        for operation in actor_ops:
            registry.register_handler('gateway', operation, lambda ctx, args, op=operation: service.handle(op, ctx, args))
        def register(principal, identity, ops, key):
            return _register(registry, principal=principal, identity=identity, scope=scope, key=key,
                instance_id='gateway', port='service.direct_write', scope_ref='scope', operations=tuple(ops))
        strategy_registration = register('principal', strategy, strategy_ops, 'strategy')
        assert strategy_registration['ok']
        registration = register('risk-principal', actor, actor_ops, 'risk')
        assert registration['ok']
        issued = registration['result']
        runtime = ThreadedServiceClientRuntime(lambda: None)
        binding = BindingSnapshot(issued['registration_id'], issued['service_epoch'], True, 'upstream.direct_write',
                                  'gateway', 'gateway', 'scope', scope, actor_ops, True)
        snapshot = RuntimeSnapshot('running', True, True, None, (binding,), 0, 0, actor, scope.run_id)
        monkeypatch.setattr(runtime, 'status', lambda: snapshot)
        def rpc(_mount, operation, args, **_kwargs):
            result = registry.handle('risk-principal', _next_request(operation, {
                'registration_id': issued['registration_id'], 'service_epoch': issued['service_epoch'],
                'instance_id': 'gateway', 'arguments': args}, identity=actor, scope=scope))
            assert result['ok'], result
            return result['result']
        monkeypatch.setattr(runtime, 'call', rpc)
        route = GatewayDelegateRoute('upstream.direct_write', 'gateway', 'gateway', 'scope', scope, actor)
        request = OrderRequest(symbol='rb', side=Side.BUY, order_type=OrderType.LIMIT, quantity=Decimal('1'),
            price=Decimal('1'), account_id='account', client_order_id='client', idempotency_key='order-1',
            offset='open', quantity_unit='lots', exchange_id='SHFE')
        cmd = GatewayDelegatedCommand('account', 'strategy', 'domain', 'risk-principal', 'risk', 'risk', 'run',
            'order-1', gateway_payload_digest(request.to_dict()), instrument, 'grant')
        client = GatewayDelegateServiceClient(runtime, route=route, host=Host())
        assert client.reserve(cmd, request).outcome == 'OBSERVED'
        assert client.dispatch('order-1').outcome == 'QUEUED'
        deadline = time.monotonic() + 2
        while stored['journal'].get('strategy', 'order-1', 'risk-principal').status in {'RESERVED', 'DISPATCHING'} and time.monotonic() < deadline:
            time.sleep(0.005)
        assert stored['journal'].get('strategy', 'order-1', 'risk-principal').status == 'SENT'
        kwargs = parts()
        config = config_data()
        config['clients']['gateway']['settings']['bindings'][0]['instance_id'] = 'gateway'
        kwargs['config'] = parse_client_config(config)
        kwargs['binding'] = GatewayCommandBinding(stored['journal'].journal_id, 'account', 'strategy', 'principal')
        kwargs['route'] = GatewayClientRoute('broker.direct_write', 'gateway', 'gateway', 'scope', scope, strategy)
        kwargs['requested_operations'] = strategy_ops - {'direct.reserve'}
        session = GatewayStrategyClientSession(**kwargs, delegated_cancel_host=CancelHost())
        facade = GatewayBtApi(session, exchange_name='CTP')
        sr = strategy_registration['result']
        session_route = session.route
        sb = BindingSnapshot(sr['registration_id'], sr['service_epoch'], True, session_route.mount,
            session_route.service_id, session_route.instance_id, session_route.scope_ref, scope, strategy_ops, True)
        monkeypatch.setattr(session.runtime, 'status', lambda: RuntimeSnapshot(
            'running', True, True, None, (sb,), 0, 0, strategy, scope.run_id))
        def strategy_rpc(_mount, operation, args, **_kwargs):
            result = registry.handle('principal', _next_request(operation, {
                'registration_id': sr['registration_id'], 'service_epoch': sr['service_epoch'],
                'instance_id': 'gateway', 'arguments': args}, identity=strategy, scope=scope))
            assert result['ok'], result
            return result['result']
        monkeypatch.setattr(session.runtime, 'call', strategy_rpc)
        observed = facade.delegated_command_status('CTP', 'order-1')
        assert observed.outcome == 'OBSERVED' and observed.observation.actor_principal == 'risk-principal'
        cancelled = facade.cancel_delegated_order('CTP', observed.observation, operation_id='cancel-1')
        assert cancelled.outcome in {'QUEUED', 'OBSERVED'}
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            last = facade.delegated_cancel_status('CTP', 'cancel-1')
            if last.observation and last.observation.state == 'SENT':
                break
            time.sleep(0.005)
        assert last.observation.state == 'SENT' and last.observation.target_actor_principal == 'risk-principal'
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)
