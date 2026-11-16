"""Local composition boundaries and ambiguous-call behavior; no account access."""
import time
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

import pytest
import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_run_contract import AccessMode
from bt_api_runtime.gateway_control import GatewayDelegationProof
from bt_api_py.runtime_gateway_control import (RuntimeGatewayControlBridge, RuntimeGatewayControlRoute,
                                               TrustedRuntimeControlBinding)
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import ClientCurveKeys


def _closed(**overrides):
    values = dict(state='closed', thread_alive=False, cleanup_failures=0,
                  unregister_uncertainties=0, unknown_registration_count=0,
                  owner_cleanup_uncertainties=0)
    values.update(overrides)
    return SimpleNamespace(**values)


def _client(endpoint, identity, route):
    return parse_client_config({'client_config_schema_version': 1, 'process_role': 'application_client',
        'registration': {'application_ref': identity.actor_ref},
        'clients': {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [{
            'mount': 'application.direct_write', 'service_id': route.service_id, 'instance_id': route.instance_id,
            'scope_ref': route.scope_ref, 'account_ref': route.scope.account_ref, 'transport_ref': 'gateway'}]}}},
        'communication': {'config_schema_version': 1, 'profiles': {'gateway': {
            'backend': 'zmq', 'config_schema_version': 1, 'endpoints': {'rpc_connect': endpoint},
            'security_profile_ref': 'client-key'}}}})


@pytest.fixture
def setup():
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'runtime', 'app')
    peer = WireIdentity(WireRole.SERVICE, 'gateway', 'service')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy')
    route = RuntimeGatewayControlRoute('service', 'instance', 'scope', scope)
    config = _client('tcp://127.0.0.1:45678', identity, route)
    proof = GatewayDelegationProof('delegation', 'alice', time.time() + 60)
    binding = TrustedRuntimeControlBinding(route, identity, peer, config.fingerprint(), proof)
    args = dict(client_configs={'strategy': config}, client_identity=identity, expected_gateway=peer,
                security_profiles={'client-key': ClientCurveKeys(server_keys[0], *client_keys)},
                proofs={'strategy': binding})
    return route, args


def test_refuses_missing_or_misbound_proof_and_account_scope(setup):
    route, args = setup
    with pytest.raises(ValueError):
        RuntimeGatewayControlBridge({'strategy': route}, **{**args, 'proofs': {}})
    original = args['proofs']['strategy']
    for changed in (replace(original, client_config_fingerprint='wrong'),
                    replace(original, client_identity=WireIdentity(WireRole.APPLICATION_CLIENT, 'other', 'app')),
                    replace(original, route=replace(route, scope_ref='other')),
                    replace(original, proof=None)):
        with pytest.raises(ValueError):
            RuntimeGatewayControlBridge({'strategy': route}, **{**args, 'proofs': {'strategy': changed}})
    with pytest.raises(ValueError):
        replace(route, scope=WireScope('domain', account_ref='account'))
    with pytest.raises(ValueError):
        replace(route, scope=replace(route.scope, run_id='run'))


def test_lost_reply_is_unknown_and_not_retried(setup, monkeypatch):
    route, args = setup
    calls = []
    class Runtime:
        def __init__(self, factory, *, require_unregister_ack):
            assert require_unregister_ack is True
            self.factory = factory
        def start(self, **kwargs):
            return SimpleNamespace(healthy=True)
        def call_bound(self, mount, operation, arguments, **kwargs):
            calls.append((mount, operation, arguments))
            raise TimeoutError('lost after send')
        def close(self, **kwargs):
            return _closed()
    monkeypatch.setattr('bt_api_py.runtime_gateway_control.ThreadedServiceClientRuntime', Runtime)
    bridge = RuntimeGatewayControlBridge({'strategy': route}, **args).start()
    config = SimpleNamespace(operators=[SimpleNamespace(name='alice')])
    client = bridge.client_for_strategy(config, 'strategy')
    unit = SimpleNamespace(access=AccessMode.GATEWAY, gateway=object())
    result = client.pause(unit, strategy_uuid='strategy', operator='alice', command_id='cmd', reason='', freeze=True)
    assert result.status == 'UNKNOWN' and result.code == 'kill_switch_unknown'
    assert len(calls) == 1
    denied = client.pause(unit, strategy_uuid='other', operator='alice', command_id='cmd2', reason='', freeze=True)
    assert denied.status == 'UNKNOWN' and len(calls) == 1
    bridge.close()


def _two_strategies(setup):
    route, args = setup
    other_route = RuntimeGatewayControlRoute('service', 'other-instance', 'other-scope',
        WireScope('domain', account_ref='other-account', strategy_ref='other-strategy'))
    other_config = _client('tcp://127.0.0.1:45679', args['client_identity'], other_route)
    proof = args['proofs']['strategy'].proof
    other_binding = TrustedRuntimeControlBinding(other_route, args['client_identity'], args['expected_gateway'],
                                                 other_config.fingerprint(), proof)
    return ({'strategy': route, 'other-strategy': other_route},
            {**args, 'client_configs': {**args['client_configs'], 'other-strategy': other_config},
             'proofs': {**args['proofs'], 'other-strategy': other_binding}})


def test_multiple_strategies_keep_config_scope_peer_and_client_separate(setup, monkeypatch):
    routes, args = _two_strategies(setup)
    created, calls = [], []
    def service_runtime(config, **kwargs):
        created.append((config, kwargs))
        return SimpleNamespace(config=config, **kwargs)
    class Runtime:
        def __init__(self, factory, *, require_unregister_ack):
            assert require_unregister_ack is True
            self.factory = factory
        def start(self, **kwargs):
            self.runtime = self.factory()
            return SimpleNamespace(healthy=True)
        def call_bound(self, mount, operation, arguments, **kwargs):
            calls.append((self.runtime, mount, operation, arguments, kwargs['route']))
            return SimpleNamespace(result={'status': 'NOT_FOUND', 'cause_id': arguments['cause_id']})
        def close(self, **kwargs):
            return _closed()
    monkeypatch.setattr('bt_api_py.runtime_gateway_control.ServiceClientRuntime', service_runtime)
    monkeypatch.setattr('bt_api_py.runtime_gateway_control.ThreadedServiceClientRuntime', Runtime)
    bridge = RuntimeGatewayControlBridge(routes, **args).start()
    for name in routes:
        assert bridge(name, 'direct.control.status', {'cause_id': 'read'})['status'] == 'NOT_FOUND'
    for (config, kwargs), (runtime, mount, operation, _, bound), (name, route) in zip(created, calls, routes.items()):
        assert config is args['client_configs'][name]
        assert kwargs['identity'] == args['client_identity']
        assert kwargs['scope_resolver'](route.scope_ref) == route.scope
        assert kwargs['peer_resolver'](route.service_id) == args['expected_gateway']
        assert kwargs['requested_operations'] == {'application.direct_write': frozenset({
            'direct.control.apply', 'direct.control.status', 'direct.control.resume'})}
        assert kwargs['require_unregister_ack'] is True
        assert runtime.config is config and mount == 'application.direct_write' and operation == 'direct.control.status'
        assert (bound.scope, bound.scope_ref, bound.instance_id, bound.peer) == (
            route.scope, route.scope_ref, route.instance_id, args['expected_gateway'])
    runtime_client = bridge.client_for_strategy(SimpleNamespace(operators=[SimpleNamespace(name='alice')]), 'strategy')
    unit = SimpleNamespace(access=AccessMode.GATEWAY, gateway=object())
    denied = runtime_client.pause(unit, strategy_uuid='other-strategy', operator='alice', command_id='cross',
                                  reason='', freeze=True)
    assert denied.status == 'UNKNOWN' and len(calls) == 2
    bridge.close()
    assert bridge.state == 'closed'


@pytest.mark.parametrize('uncertain', [False, True])
def test_second_start_failure_closes_first_and_preserves_uncertainty(setup, monkeypatch, uncertain):
    routes, args = _two_strategies(setup)
    workers = []
    class Runtime:
        def __init__(self, factory, *, require_unregister_ack):
            assert require_unregister_ack is True
            self.index = len(workers)
            self.closed = 0
            workers.append(self)
        def start(self, **kwargs):
            if self.index == 1:
                raise RuntimeError('SECOND_START_FAILED')
            return SimpleNamespace(healthy=True)
        def close(self, **kwargs):
            self.closed += 1
            return _closed(thread_alive=self.index == 0 and uncertain and self.closed == 1)
    monkeypatch.setattr('bt_api_py.runtime_gateway_control.ThreadedServiceClientRuntime', Runtime)
    bridge = RuntimeGatewayControlBridge(routes, **args)
    expected = 'RUNTIME_GATEWAY_CLEANUP_UNCERTAIN' if uncertain else 'SECOND_START_FAILED'
    with pytest.raises(RuntimeError, match=expected):
        bridge.start()
    assert [worker.closed for worker in workers] == [1, 1]
    assert bridge.state == ('close_uncertain' if uncertain else 'closed')
    with pytest.raises(ValueError):
        bridge('strategy', 'direct.control.status', {'cause_id': 'c'})
    with pytest.raises(RuntimeError, match='LIFECYCLE_INVALID'):
        bridge.start()
    if uncertain:
        bridge.close()
        assert bridge.state == 'closed'
        assert [worker.closed for worker in workers] == [2, 2]


@pytest.mark.parametrize('field', ['state', 'thread_alive', 'cleanup_failures',
                                   'unregister_uncertainties', 'unknown_registration_count',
                                   'owner_cleanup_uncertainties'])
@pytest.mark.parametrize('bad', ['missing', 'raises', None, True, False, -1, 1, '0', 'failed'])
def test_close_rejects_unproven_snapshot_fields(setup, monkeypatch, field, bad):
    route, args = setup
    class Snapshot:
        def __getattribute__(self, name):
            if name == field:
                if bad == 'missing':
                    raise AttributeError(name)
                if bad == 'raises':
                    raise RuntimeError('unreadable')
                return bad
            return getattr(_closed(), name)
    class Runtime:
        def __init__(self, factory, *, require_unregister_ack):
            assert require_unregister_ack is True
        def close(self, **kwargs):
            return Snapshot()
    # False is the one valid value for thread_alive.
    if field == 'thread_alive' and bad is False:
        return
    monkeypatch.setattr('bt_api_py.runtime_gateway_control.ThreadedServiceClientRuntime', Runtime)
    bridge = RuntimeGatewayControlBridge({'strategy': route}, **args)
    with pytest.raises(RuntimeError, match='RUNTIME_GATEWAY_CLEANUP_UNCERTAIN'):
        bridge.close()
    assert bridge.state == 'close_uncertain'


def test_partial_close_attempts_all_and_retries_original_owners(setup, monkeypatch):
    routes, args = _two_strategies(setup)
    workers = []
    class Runtime:
        def __init__(self, factory, *, require_unregister_ack):
            self.calls = 0
            workers.append(self)
        def start(self, **kwargs):
            return SimpleNamespace(healthy=True)
        def close(self, **kwargs):
            self.calls += 1
            if self is workers[0] and self.calls == 1:
                raise RuntimeError('cleanup failed')
            return _closed()
    monkeypatch.setattr('bt_api_py.runtime_gateway_control.ThreadedServiceClientRuntime', Runtime)
    bridge = RuntimeGatewayControlBridge(routes, **args).start()
    original_entries = bridge._entries
    original_workers = tuple(entry[2] for entry in original_entries.values())
    with pytest.raises(RuntimeError, match='RUNTIME_GATEWAY_CLEANUP_UNCERTAIN'):
        bridge.close()
    assert bridge.state == 'close_uncertain' and [w.calls for w in workers] == [1, 1]
    bridge.close()
    assert bridge.state == 'closed' and [w.calls for w in workers] == [2, 2]
    assert bridge._entries is original_entries
    assert tuple(entry[2] for entry in bridge._entries.values()) == original_workers == tuple(workers)


def test_real_threaded_runtime_lost_registration_remains_uncertain(setup, tmp_path):
    # Reuse the exact SDK runtime and real local Registry with a synthetic
    # transport that commits REGISTER and drops its reply; no account access.
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
    from tests.unit.test_service_runtime import LocalTransport, _config, _registry, _runtime

    route, args = setup
    bridge = RuntimeGatewayControlBridge({'strategy': route}, **args)
    made, committed = [], []
    def factory():
        registry = _registry(tmp_path / 'lost-register.sqlite', [])
        class LoseReply(LocalTransport):
            def request(self, request, *, timeout):
                reply = super().request(request, timeout=timeout)
                if request.header.operation == 'service.register':
                    committed.append(reply.payload['ok'])
                    raise TimeoutError('synthetic lost register reply')
                return reply
            def close(self):
                super().close()
                registry.close()
        raw = _runtime(_config(), lambda *_: LoseReply(registry), require_unregister_ack=True)
        made.append(raw)
        return raw
    owner = ThreadedServiceClientRuntime(factory, require_unregister_ack=True)
    old = bridge._entries['strategy']
    bridge._entries = MappingProxyType({'strategy': (old[0], old[1], owner, old[3])})
    with pytest.raises(RuntimeError, match='RUNTIME_GATEWAY_CLEANUP_UNCERTAIN'):
        bridge.start()
    snapshot = owner.status()
    assert committed == [True] and len(made) == 1
    assert snapshot.state == 'failed' and snapshot.thread_alive is False
    assert snapshot.cleanup_failures == snapshot.unregister_uncertainties == 0
    assert snapshot.unknown_registration_count == 1 and bridge.state == 'close_uncertain'
    with pytest.raises(RuntimeError, match='RUNTIME_GATEWAY_CLEANUP_UNCERTAIN'):
        bridge.close()
    assert bridge._entries['strategy'][2] is owner and len(made) == 1


@pytest.mark.parametrize('inactive', [False, True])
def test_real_threaded_runtime_requires_ack_even_after_epoch_inactive(setup, tmp_path, inactive):
    from bt_api_base.service_protocol import WireEnvelope, WireMessageKind
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
    from tests.unit.test_service_runtime import LocalTransport, ManualClock, PEER, _config, _registry, _runtime

    route, args = setup
    bridge = RuntimeGatewayControlBridge({'strategy': route}, **args)
    clock, operations, made = ManualClock(1000), [], []
    def factory():
        registry = _registry(tmp_path / 'strict-ack.sqlite', [])
        class Transport(LocalTransport):
            def request(self, request, *, timeout):
                operations.append(request.header.operation)
                if inactive and request.header.operation in {'service.renew', 'service.unregister'}:
                    header = replace(request.header, kind=WireMessageKind.REPLY,
                                     message_id='rejected', correlation_id=request.header.message_id,
                                     identity=PEER)
                    return WireEnvelope(header, {'ok': False, 'code': 'STALE_EPOCH'})
                return super().request(request, timeout=timeout)
            def close(self):
                super().close()
                registry.close()
        raw = _runtime(_config(), lambda *_: Transport(registry), monotonic_clock=clock,
                       require_unregister_ack=True)
        made.append(raw)
        return raw
    owner = ThreadedServiceClientRuntime(factory, monotonic_clock=clock, require_unregister_ack=True)
    old = bridge._entries['strategy']
    bridge._entries = MappingProxyType({'strategy': (old[0], old[1], owner, old[3])})
    bridge.start()
    if inactive:
        clock.advance(10.1)
        deadline = time.monotonic() + 3
        while owner.status().state != 'failed' and time.monotonic() < deadline:
            time.sleep(0.01)
        assert owner.status().state == 'failed'
        with pytest.raises(RuntimeError, match='RUNTIME_GATEWAY_CLEANUP_UNCERTAIN'):
            bridge.close()
        assert 'service.renew' in operations
        assert owner.status().unregister_uncertainties == 1
        assert bridge.state == 'close_uncertain'
    else:
        bridge.close()
        assert bridge.state == 'closed'
    assert operations.count('service.unregister') == 1
    assert bridge._entries['strategy'][2] is owner and len(made) == 1
