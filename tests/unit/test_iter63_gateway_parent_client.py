"""Synthetic tests for durable claims and authenticated parent receipts."""
import copy

from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.gateway_delegate_service_client import GatewayDelegateRoute
from bt_api_py.gateway_parent_client import GatewayParentServiceClient
from bt_api_py.gateway_parent_service import GatewayParentGrant, GatewayParentSettings, OPERATIONS
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime, BindingSnapshot, RuntimeSnapshot


def _spec():
    return {'account_scope': 'account', 'strategy_scope': 'strategy', 'parent_ref': 'parent',
            'registration_operation_id': 'register-1', 'execution_domain_ref': 'domain',
            'instrument': MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb').to_dict(),
            'quantity': '1', 'quantity_unit': 'lots', 'side': 'buy', 'offset': 'open',
            'limit_price': '1', 'creation_trading_day': '20300101', 'allowed_trading_days': ['20300101'],
            'expires_at_ns': 2000000000000000000, 'grant_revision': 'rev', 'target_position_ref': None,
            'plan_digest': '0' * 64, 'plan_version': '1', 'time_in_force': 'DAY',
            'risk_selected': False, 'risk_operation_id': 'risk-1'}


class _Host:
    def __init__(self):
        self.used = set()
    def prepare_parent_dispatch(self, spec, operation_id, kind, expected_version):
        key = (spec['parent_ref'], operation_id)
        if key in self.used:
            return False
        self.used.add(key)
        return True


def _client(monkeypatch):
    identity = WireIdentity(WireRole.SERVICE, 'execution-principal', 'execution')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
    route = GatewayDelegateRoute('upstream.direct_write', 'gateway', 'instance', 'scope', scope, identity)
    runtime = ThreadedServiceClientRuntime(lambda: None)
    binding = BindingSnapshot('registration', 'epoch', True, route.mount, route.service_id,
                              route.instance_id, route.scope_ref, scope, OPERATIONS, True)
    snap = RuntimeSnapshot('running', True, True, None, (binding,), 0, 0, identity, scope.run_id)
    monkeypatch.setattr(runtime, 'status', lambda: snap)
    host = _Host()
    return GatewayParentServiceClient(runtime, route=route, host=host), runtime, host


def test_registered_receipt_requires_live_client_and_unmodified_content(monkeypatch):
    client, runtime, host = _client(monkeypatch)
    spec = _spec()
    def call(_mount, op, args, **_kwargs):
        assert ('parent', 'register-1') in host.used
        value = args['spec']
        result = {'spec': value, 'spec_digest': gateway_payload_digest(value),
                  'execution_principal': 'execution-principal', 'execution_actor_ref': 'execution',
                  'state': 'ACTIVE', 'version': 1}
        return {'status': 'OBSERVED', 'schema_version': 1, 'operation': op, 'result': result}
    monkeypatch.setattr(runtime, 'call', call)
    receipt = client.register_parent(spec, 'register-1')
    assert client.verify(receipt, spec, 'register-1') == 'ACCEPTED'
    assert client.verify(dict(receipt), spec, 'register-1') is None
    receipt['result']['spec']['quantity'] = '2'
    assert client.verify(receipt, spec, 'register-1') is None
    assert client.register_parent(spec, 'register-1') is None


def test_unknown_never_resends_and_missing_query_does_not_resolve(monkeypatch):
    client, runtime, host = _client(monkeypatch)
    calls = []
    def call(_mount, operation, args, **_kwargs):
        calls.append((operation, copy.deepcopy(args)))
        if operation.endswith('register'):
            raise TimeoutError
        return {'status': 'UNKNOWN'}
    monkeypatch.setattr(runtime, 'call', call)
    spec = _spec()
    assert client.register_parent(spec, 'register-1') is None
    assert client.register_parent(spec, 'register-1') is None
    assert client.query_operation(spec, 'register-1') is None
    assert [call[0] for call in calls] == ['direct.parent.register', 'direct.parent.operation']
    assert calls[-1][1]['operation_id'] == 'register-1'


def test_cancel_receipt_preserves_targets_and_rejects_changed_identities(monkeypatch):
    client, runtime, _ = _client(monkeypatch)
    spec = _spec()
    wire = {k: v for k, v in spec.items() if k not in {'risk_selected', 'risk_operation_id'}}
    target = {'account_scope': 'account', 'strategy_scope': 'strategy', 'parent_ref': 'parent',
              'child_id': 'child', 'request_key': 'request', 'reservation_id': 'reservation',
              'payload_digest': 'a' * 64, 'target_principal': 'execution-principal', 'target_version': 3,
              'native_identity': {'ref': 'synthetic'}, 'strong_identity_keys': ['key'],
              'phase': 'CANCEL_PENDING', 'cancel_operation_id': 'parent-cancel-' + gateway_payload_digest(
                  ['account', 'strategy', 'parent', 'child', 'cancel-1'])}
    result = {'spec': wire, 'spec_digest': gateway_payload_digest(wire),
              'execution_principal': 'execution-principal', 'execution_actor_ref': 'execution',
              'state': 'CANCEL_REQUESTED', 'version': 2, 'targets': [target]}
    def call(_mount, operation, _args, **_kwargs):
        value = copy.deepcopy(result)
        if operation.endswith('operation'):
            value = {'operation_id': 'cancel-1', 'kind': 'CANCEL', 'result': value}
        return {'status': 'OBSERVED', 'schema_version': 1, 'operation': operation, 'result': value}
    monkeypatch.setattr(runtime, 'call', call)
    receipt = client.cancel_parent(spec, 'cancel-1', 1)
    assert client.verify(receipt, spec, 'cancel-1') == 'ACCEPTED'
    assert receipt['result']['targets'] == [target]
    queried = client.query_operation(spec, 'cancel-1')
    assert client.verify(queried, spec, 'cancel-1') == 'ACCEPTED'
    queried['result']['targets'][0]['request_key'] = 'changed'
    assert client.verify(queried, spec, 'cancel-1') is None
    result['targets'][0]['cancel_operation_id'] = 'forged'
    assert client.query_operation(spec, 'cancel-1') is None


def test_parent_grants_are_explicit_and_strategy_cannot_register():
    key = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
    identity = WireIdentity(WireRole.SERVICE, 'execution-principal', 'execution')
    grant = GatewayParentGrant(identity, 'account', 'strategy', 'domain', 'execution', 'rev',
                               frozenset({key}), OPERATIONS)
    assert GatewayParentSettings((grant,)).grants == (grant,)
    import pytest
    with pytest.raises(ValueError):
        GatewayParentGrant(WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy'),
                           'account', 'strategy', 'domain', 'strategy', 'rev', frozenset({key}), OPERATIONS)


def test_parent_registry_owner_and_client_synthetic(tmp_path, monkeypatch):
    from dataclasses import replace
    import sqlite3
    import threading
    import time
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
    from bt_api_gateway.direct_parent_authority import GatewayParentAuthority
    from bt_api_gateway.session_guard import LocalAccountSessionGuard
    from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
    from bt_api_py.gateway_parent_service import GatewayParentService
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
    from tests.unit.test_service_registry import _next_request, _register

    directory = tmp_path / 'guard'
    directory.mkdir()
    parts = {}
    def factory():
        guard = LocalAccountSessionGuard(directory, 'account', 'owner')
        journal = GatewayDirectCommandJournal.create(tmp_path / 'journal', 'account', generation=guard.acquire())
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        journal.enable_parent_authority(guard)
        authority = GatewayParentAuthority(journal, guard, now_ns=lambda: 100, trading_day=lambda: '20300101')
        dispatcher = GatewayDirectDispatcher(journal, 'CTP', guard, lambda *_: None, lambda _: ('key',), lambda _: True)
        parts.update(journal=journal, authority=authority)
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release)
    owner = GatewayDirectOwner(factory, queue_size=2, max_queue_wait_seconds=2, busy_timeout_seconds=2)
    assert owner.start(2)
    registry = None
    try:
        client, runtime, _ = _client(monkeypatch)
        route = client._route
        identity, scope = route.identity, route.scope
        strategy = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-principal', 'strategy')
        template = WireScope('domain', account_ref='account', strategy_ref='strategy')
        strategy_ops = frozenset({'direct.reserve'})
        writes = frozenset({'direct.reserve', 'direct.parent.register', 'direct.parent.cancel'})
        cap = ServiceCapability('instance', 'service.direct_write', OPERATIONS | strategy_ops, writes)
        registry = ServiceRegistry(':memory:', service_id='gateway-service', capabilities=(cap,),
            grants={identity.principal_ref: (PrincipalGrant(identity, (ResourceGrant('instance', 'service.direct_write',
                      'scope', template, OPERATIONS),)),),
                    strategy.principal_ref: (PrincipalGrant(strategy, (ResourceGrant('instance', 'service.direct_write',
                      'scope', template, strategy_ops),)),)}, policy_version='p', requirements_version='r')
        key = MarketInstrumentKey.from_dict(_spec()['instrument'])
        grant = GatewayParentGrant(identity, 'account', 'strategy', 'domain', 'execution', 'rev',
                                   frozenset({key}), OPERATIONS)
        service = GatewayParentService(GatewayParentSettings((grant,)), owner=owner, journal=parts['journal'],
            authority=parts['authority'], instance_id='instance', scope_ref='scope')
        service.configure(registry)
        registry.register_handler('instance', 'direct.reserve', lambda *_: {})
        for op in OPERATIONS:
            registry.register_handler('instance', op, lambda ctx, args, op=op: service.handle(op, ctx, args))
        def register(actor, ops, key):
            return _register(registry, principal=actor.principal_ref, identity=actor, scope=scope, key=key,
                instance_id='instance', port='service.direct_write', scope_ref='scope', operations=tuple(ops))
        assert register(strategy, strategy_ops, 'strategy')['ok']
        registration = register(identity, OPERATIONS, 'execution')
        assert registration['ok'], registration
        issued = registration['result']
        binding = BindingSnapshot(issued['registration_id'], issued['service_epoch'], True, route.mount,
                                  route.service_id, route.instance_id, route.scope_ref, scope, OPERATIONS, True)
        monkeypatch.setattr(runtime, 'status', lambda: RuntimeSnapshot('running', True, True, None,
            (binding,), 0, 0, identity, scope.run_id))
        responses = []
        def rpc(_mount, op, args, **_kwargs):
            response = registry.handle(identity.principal_ref, _next_request(op, {
                'registration_id': issued['registration_id'], 'service_epoch': issued['service_epoch'],
                'instance_id': 'instance', 'arguments': args}, identity=identity, scope=scope))
            assert response['ok'], response
            responses.append(response)
            if op != 'direct.parent.operation' or args.get('operation_id') != 'absent':
                assert response['result'].get('status') == 'OBSERVED', response
            return response['result']
        monkeypatch.setattr(runtime, 'call', rpc)
        spec = _spec()
        receipt = client.register_parent(spec, 'register-1')
        assert client.verify(receipt, spec, 'register-1') == 'ACCEPTED', responses
        result = client.query_operation(spec, 'register-1')
        assert client.verify(result, spec, 'register-1') == 'ACCEPTED'
        assert client.query_operation(spec, 'absent') is None
        cancel = client.cancel_parent(spec, 'cancel-1', 1)
        assert client.verify(cancel, spec, 'cancel-1') == 'ACCEPTED'
        assert client.query_parent(spec)['state'] == 'CANCEL_REQUESTED'
        assert client.verify(client.query_operation(spec, 'cancel-1'), spec, 'cancel-1') == 'ACCEPTED'

        # Hold the same account owner after Registry admission, then narrow the
        # trusted grant. The queued mutation must not enter its transaction.
        def fenced_registration(parent, change_policy):
            entered, release = threading.Event(), threading.Event()
            def block():
                entered.set()
                assert release.wait(2)
            blocker = owner.submit_service_call(block)
            assert entered.wait(2)
            request = copy.deepcopy(spec)
            request.pop('risk_selected')
            request.pop('risk_operation_id')
            request.update(parent_ref=parent, registration_operation_id=parent + '-op')
            output = []
            def send():
                output.append(registry.handle(identity.principal_ref, _next_request('direct.parent.register', {
                    'registration_id': issued['registration_id'], 'service_epoch': issued['service_epoch'],
                    'instance_id': 'instance', 'arguments': {'spec': request}}, identity=identity, scope=scope)))
            worker = threading.Thread(target=send)
            worker.start()
            deadline = time.monotonic() + 0.8
            while owner.status().service_queue_depth != 1 and time.monotonic() < deadline:
                time.sleep(0.002)
            assert owner.status().service_queue_depth == 1
            change_policy()
            release.set()
            blocker.result(timeout_seconds=2)
            worker.join(2)
            assert not worker.is_alive()
            assert ((output[0]['ok'] and output[0]['result'] == {'status': 'UNKNOWN'})
                    or (not output[0]['ok'] and output[0]['code'] in {'SCOPE_DENIED', 'DUPLICATE_ACTIVE_RUN'})), output
            with sqlite3.connect(tmp_path / 'journal') as db:
                assert db.execute('SELECT count(*) FROM direct_execution_parents WHERE parent_ref=?', (parent,)).fetchone()[0] == 0

        grant_key = ('instance', 'scope', identity.principal_ref, 'account', 'strategy')
        def narrow_grant():
            with registry._lock:
                registry._gateway_parent_grants[grant_key] = replace(grant, revision='rev2')
        fenced_registration('revoked-grant', narrow_grant)
        with registry._lock:
            registry._gateway_parent_grants[grant_key] = grant
        strategy_registration_id = registry._db.execute(
            "SELECT registration_id FROM sessions WHERE principal=?", (strategy.principal_ref,)).fetchone()[0]
        def revoke_run():
            with registry._lock:
                registry._db.execute('UPDATE sessions SET expires_at=0 WHERE registration_id=?',
                                     (strategy_registration_id,))
        fenced_registration('revoked-run', revoke_run)
        forged = register(WireIdentity(WireRole.SERVICE, identity.principal_ref, 'impostor'), OPERATIONS, 'forged')
        assert not forged['ok']
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)
