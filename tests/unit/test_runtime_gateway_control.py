"""Local composition boundaries and ambiguous-call behavior; no account access."""
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_run_contract import AccessMode
from bt_api_runtime.gateway_control import GatewayDelegationProof
from bt_api_py.runtime_gateway_control import (RuntimeGatewayControlBridge, RuntimeGatewayControlRoute,
                                               TrustedRuntimeControlBinding)
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import ClientCurveKeys


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
        def __init__(self, factory):
            self.factory = factory
        def start(self, **kwargs):
            return SimpleNamespace(healthy=True)
        def call_bound(self, mount, operation, arguments, **kwargs):
            calls.append((mount, operation, arguments))
            raise TimeoutError('lost after send')
        def close(self, **kwargs):
            return SimpleNamespace(thread_alive=False, cleanup_failures=0, unregister_uncertainties=0)
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
        def __init__(self, factory):
            self.factory = factory
        def start(self, **kwargs):
            self.runtime = self.factory()
            return SimpleNamespace(healthy=True)
        def call_bound(self, mount, operation, arguments, **kwargs):
            calls.append((self.runtime, mount, operation, arguments, kwargs['route']))
            return SimpleNamespace(result={'status': 'NOT_FOUND', 'cause_id': arguments['cause_id']})
        def close(self, **kwargs):
            return SimpleNamespace(thread_alive=False, cleanup_failures=0, unregister_uncertainties=0)
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
        def __init__(self, factory):
            self.index = len(workers)
            self.closed = 0
            workers.append(self)
        def start(self, **kwargs):
            if self.index == 1:
                raise RuntimeError('SECOND_START_FAILED')
            return SimpleNamespace(healthy=True)
        def close(self, **kwargs):
            self.closed += 1
            return SimpleNamespace(thread_alive=self.index == 0 and uncertain and self.closed == 1,
                                   cleanup_failures=0, unregister_uncertainties=0)
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
