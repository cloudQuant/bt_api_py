"""Account observer uses its own cursor and explicitly acknowledges."""
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.gateway_account_observer_client import GatewayAccountObserverClient, GatewayAccountObserverRoute
from bt_api_py.gateway_account_observer import account_observer_consumer_ref
from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime_thread import BindingSnapshot, RuntimeSnapshot, ThreadedServiceClientRuntime
from tests.unit.test_iter63_gateway_strategy_client import parts, config_data


def test_account_observer_scopes_cursor_and_never_auto_acks(monkeypatch):
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
    route = GatewayAccountObserverRoute('store.account', 'gateway', 'instance', 'scope', scope, identity)
    runtime = ThreadedServiceClientRuntime(lambda: None)
    ops = frozenset({'account.events.read_after', 'account.events.progress', 'account.events.ack'})
    binding = BindingSnapshot('registration', 'epoch', True, route.mount, route.service_id,
                              route.instance_id, route.scope_ref, scope, ops, True)
    snap = RuntimeSnapshot('running', True, True, None, (binding,), 0, 0, identity, scope.run_id)
    monkeypatch.setattr(runtime, 'status', lambda: snap)
    consumer = account_observer_consumer_ref(principal_ref='principal', identity=identity, scope=scope)
    calls = []
    def call(_mount, operation, args, **_kwargs):
        calls.append((operation, args))
        progress = {'schema_version': 1, 'account_scope': 'account', 'consumer_ref': consumer,
                    'epoch': 'a' * 64, 'offered_sequence': 0, 'acknowledged_sequence': 0}
        if operation == 'account.events.read_after':
            return {'status': 'OK', 'page': {**progress, 'events': [], 'has_more': False}}
        return {'status': 'OK', 'progress': progress}
    monkeypatch.setattr(runtime, 'call', call)
    client = GatewayAccountObserverClient(runtime, route=route)
    page = client.read_after()
    assert page.outcome == 'OBSERVED' and page.page.events == ()
    assert [call[0] for call in calls] == ['account.events.read_after']
    assert client.acknowledge(0, expected_epoch='a' * 64).outcome == 'OBSERVED'
    assert calls[-1] == ('account.events.ack', {'sequence': 0, 'expected_epoch': 'a' * 64})
    monkeypatch.setattr(runtime, 'status', lambda: replace(snap, bindings=(replace(binding, operations=frozenset()),)))
    assert client.progress().outcome == 'TRANSPORT_NOT_STARTED'
    with pytest.raises(ValueError):
        client.read_after(after_sequence=1)


def test_strategy_facade_exposes_only_explicit_account_observer():
    kwargs = parts()
    ordinary = GatewayBtApi(GatewayStrategyClientSession(**kwargs), exchange_name='CTP')
    with pytest.raises(RuntimeError, match='ACCOUNT_OBSERVER_UNAVAILABLE'):
        ordinary.gateway_account_observer('CTP')
    config = config_data()
    mounts = config['clients']['gateway']['settings']['bindings']
    mounts.append(dict(mounts[0], mount='store.account'))
    kwargs['config'] = parse_client_config(config)
    session = GatewayStrategyClientSession(**kwargs, account_observer_operations=frozenset({'account.events.progress'}))
    api = GatewayBtApi(session, exchange_name='CTP')
    assert api.gateway_account_observer('CTP') is session.account_observer
    assert session.account_observer._route.scope.account_ref == 'account'


def test_account_client_uses_real_registry_and_owner(monkeypatch, tmp_path):
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
    from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
    from bt_api_gateway.session_guard import LocalAccountSessionGuard
    from bt_api_py.gateway_account_observer import GatewayAccountObserver, ACCOUNT_OBSERVER_OPERATIONS
    from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
    from tests.unit.test_service_registry import _next_request, _register
    from test_iter63_direct_execution_journal import command, fact

    directory = tmp_path / 'guard'
    directory.mkdir()
    stored = {}
    def factory():
        guard = LocalAccountSessionGuard(directory, 'account', 'owner')
        journal = GatewayDirectCommandJournal.create(tmp_path / 'db', 'account', generation=guard.acquire())
        ledger = GatewayDirectExecutionLedger(journal, guard)
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        # Source test helpers use acct, so create the synthetic event with the
        # same account by rebuilding the original fact.
        command(journal, strategy='s1', key='one', anchor='a')
        raw = fact(keys=('a',), binding=('a',), venue='a')
        assert ledger.ingest(replace(raw, account_scope='account')).disposition == 'ATTRIBUTED'
        stored.update(journal=journal, ledger=ledger)
        dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, lambda *_: None, lambda _: (), lambda _: True)
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release)
    owner = GatewayDirectOwner(factory, queue_size=2, max_queue_wait_seconds=1, busy_timeout_seconds=1)
    assert owner.start(2)
    registry = None
    try:
        identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'actor')
        scope = WireScope('domain', account_ref='account')
        cap = ServiceCapability('gateway', 'service.account_read', ACCOUNT_OBSERVER_OPERATIONS, frozenset())
        grant = ResourceGrant('gateway', 'service.account_read', 'scope', scope, ACCOUNT_OBSERVER_OPERATIONS)
        registry = ServiceRegistry(':memory:', service_id='service', capabilities=(cap,),
            grants={'operator': (PrincipalGrant(identity, (grant,)),)}, policy_version='p', requirements_version='r')
        service = GatewayAccountObserver(owner, stored['journal'], stored['ledger'], account_scope='account',
                                         execution_domain_ref='domain', principals=frozenset({'operator'}))
        for operation in ACCOUNT_OBSERVER_OPERATIONS:
            registry.register_handler('gateway', operation, lambda ctx, args, op=operation: service.handle(op, ctx, args))
        registered = _register(registry, principal='operator', identity=identity, scope=scope, instance_id='gateway',
            port='service.account_read', scope_ref='scope', operations=tuple(ACCOUNT_OBSERVER_OPERATIONS))
        assert registered['ok']
        reg = registered['result']
        runtime = ThreadedServiceClientRuntime(lambda: None)
        route = GatewayAccountObserverRoute('application.account', 'gateway', 'gateway', 'scope', scope, identity)
        binding = BindingSnapshot(reg['registration_id'], reg['service_epoch'], True, route.mount, route.service_id,
                                  route.instance_id, route.scope_ref, scope, ACCOUNT_OBSERVER_OPERATIONS, True)
        monkeypatch.setattr(runtime, 'status', lambda: RuntimeSnapshot(
            'running', True, True, None, (binding,), 0, 0, identity, 'client-runtime-run'))
        def rpc(_mount, operation, args, **_kwargs):
            reply = registry.handle('operator', _next_request(operation, {'registration_id': reg['registration_id'],
                'service_epoch': reg['service_epoch'], 'instance_id': 'gateway', 'arguments': args},
                identity=identity, scope=scope))
            assert reply['ok'], reply
            return reply['result']
        monkeypatch.setattr(runtime, 'call', rpc)
        client = GatewayAccountObserverClient(runtime, route=route)
        page = client.read_after()
        assert page.outcome == 'OBSERVED' and [e.strategy_scope for e in page.page.events] == ['s1']
        progress = client.acknowledge(page.page.progress.offered_sequence, expected_epoch=page.page.progress.epoch)
        # v12 has no persisted registration owner, so its ACK is intentionally
        # unavailable until the account is explicitly upgraded to v19.
        assert (progress.outcome, progress.code) == ('REQUEST_DENIED', 'ACCOUNT_OBSERVER_HANDOFF_UNAVAILABLE')
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)
