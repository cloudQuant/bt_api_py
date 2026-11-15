"""Synthetic composition and parent observation; no real transport or account."""
from dataclasses import replace
import hashlib

import pytest

from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.contracts import ExecutionScope
from bt_api_execution.gateway_session import ExecutionGatewaySession
from bt_api_execution.store import SqliteExecutionStore
from bt_api_py.gateway_delegate_service_client import GatewayDelegateRoute
from bt_api_py.gateway_parent_client import GatewayParentServiceClient, QUERY, _trusted_parent_query_target
from bt_api_py.gateway_parent_service import OPERATIONS
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_runtime import ClientCurveKeys
from bt_api_py.service_runtime_thread import BindingSnapshot, RuntimeSnapshot, ThreadedServiceClientRuntime
from tests.unit.test_iter63_gateway_parent_client import _spec, _Host


def config():
    return parse_service_config({
        'service_config_schema_version': 1, 'process_role': 'service',
        'service_id': 'synthetic-execution', 'scope_ref': 'execution-scope',
        'transport_ref': 'listener', 'registration_policy_ref': 'policy',
        'service_requirements_ref': 'requirements',
        'components': {'execution': {'config_schema_version': 2, 'settings': {
            'instance_id': 'execution', 'bind_ports': ['service.order_group_entry'],
            'options': {'role': 'group_coordinator', 'portfolio_ref': 'portfolio'}}}},
        'clients': {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [{
            'mount': 'upstream.direct_write', 'account_ref': 'account', 'service_id': 'gateway',
            'instance_id': 'instance', 'scope_ref': 'scope', 'transport_ref': 'gateway-rpc', 'required': True}]}}},
        'communication': {'config_schema_version': 1, 'profiles': {
            'listener': {'backend': 'zmq', 'config_schema_version': 1,
                         'endpoints': {'rpc_bind': 'tcp://127.0.0.1:7854'}, 'security_profile_ref': 'security'},
            'gateway-rpc': {'backend': 'zmq', 'config_schema_version': 1,
                            'endpoints': {'rpc_connect': 'tcp://127.0.0.1:7855'},
                            'security_profile_ref': 'security'}}}})


@pytest.fixture
def composition(tmp_path):
    store = SqliteExecutionStore(tmp_path / 'parent.sqlite')
    scope = ExecutionScope('FAKE', 'synthetic', 'account', 'strategy', '20300101')
    lease = store.acquire_or_renew_lease(scope, 'writer', ttl_ns=300_000_000_000)
    identity = WireIdentity(WireRole.SERVICE, 'execution-principal', 'execution')
    route = GatewayDelegateRoute('upstream.direct_write', 'gateway', 'instance', 'scope',
        WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run'), identity)
    peer = WireIdentity(WireRole.SERVICE, 'gateway-principal', 'gateway-actor')
    keys = ClientCurveKeys(b'synthetic-server-key', b'synthetic-client-key', b'synthetic-secret')
    profiles = {'security': keys}
    session = ExecutionGatewaySession(config=config(), route=route, expected_gateway=peer,
        security_profiles=profiles, store=store, scope=scope, lease=lease, admission=lambda *_: True)
    yield session, route, peer, keys, profiles, store
    store.close()


def _snapshot(route, *, extra=(), epoch='epoch'):
    binding = BindingSnapshot('registration', epoch, True, route.mount, route.service_id,
                              route.instance_id, route.scope_ref, route.scope, OPERATIONS, True)
    return RuntimeSnapshot('running', True, True, None, (binding, *extra), 0, 0,
                           route.identity, route.scope.run_id)


def _response(client, spec):
    wire_spec = client._spec(spec)
    return {'status': 'OBSERVED', 'schema_version': 1, 'operation': QUERY, 'result': {
        'spec': wire_spec, 'spec_digest': gateway_payload_digest(wire_spec),
        'execution_principal': 'execution-principal', 'execution_actor_ref': 'execution',
        'state': 'ACTIVE', 'version': 1}}


def test_session_observation_uses_frozen_trusted_target_and_cannot_verify(composition, monkeypatch):
    session, route, peer, keys, profiles, _ = composition
    runtime, client = session.runtime, session.parent_client
    monkeypatch.setattr(runtime, 'status', lambda: _snapshot(route))
    calls = []
    def bound(target, operation, args, *, timeout_seconds):
        calls.append((target, operation, args, timeout_seconds))
        return _response(client, _spec())
    monkeypatch.setattr(runtime, 'call_bound', bound)
    monkeypatch.setattr(runtime, 'call', lambda *_a, **_k: pytest.fail('ordinary call used'))
    # Mutating inputs after assembly cannot change its static expectations.
    object.__setattr__(peer, 'actor_ref', 'mutated-peer')
    object.__setattr__(keys, 'server_key', b'changed')
    profiles.clear()
    result = client.query_parent(_spec())
    assert result['state'] == 'ACTIVE' and client.verify(result, _spec(), 'register-1') is None
    target, operation, args, timeout = calls[0]
    assert operation == QUERY and args['parent_ref'] == 'parent' and timeout == 5.0
    assert target.expected_peer.actor_ref == 'gateway-actor'
    assert target.server_key_fingerprint == hashlib.sha256(b'synthetic-server-key').hexdigest()
    assert (target.mount, target.port, target.transport_ref, target.security_profile_ref, target.endpoint) == (
        'upstream.direct_write', 'service.direct_write', 'gateway-rpc', 'security', 'tcp://127.0.0.1:7855')
    assert (target.registration_id, target.service_epoch) == ('registration', 'epoch')


@pytest.mark.parametrize('failure', ['wrong_peer', 'wrong_key', 'wrong_profile', 'socket', 'epoch', 'unknown'])
def test_owner_rejection_and_unknown_fail_closed(composition, monkeypatch, failure):
    session, route, peer, keys, _, _ = composition
    runtime, client = session.runtime, session.parent_client
    monkeypatch.setattr(runtime, 'status', lambda: _snapshot(route))
    seen = []
    def owner(target, operation, args, *, timeout_seconds):
        seen.append(target)
        actual_peer = replace(peer, actor_ref='other') if failure == 'wrong_peer' else peer
        actual_key = b'other-key' if failure == 'wrong_key' else keys.server_key
        actual_profile = 'other-profile' if failure == 'wrong_profile' else 'security'
        active_epoch = 'new-epoch' if failure == 'epoch' else 'epoch'
        socket_current = failure != 'socket'
        if (target.expected_peer != actual_peer or target.server_key_fingerprint != hashlib.sha256(actual_key).hexdigest()
                or target.security_profile_ref != actual_profile or target.service_epoch != active_epoch or not socket_current):
            raise RuntimeError('synthetic owner rejected')
        return {'status': 'UNKNOWN'}
    monkeypatch.setattr(runtime, 'call_bound', owner)
    monkeypatch.setattr(runtime, 'call', lambda *_a, **_k: pytest.fail('ordinary call used'))
    assert client.query_parent(_spec()) is None and len(seen) == 1


def test_status_ambiguity_and_epoch_change_are_closed(composition, monkeypatch):
    session, route, *_ = composition
    runtime, client = session.runtime, session.parent_client
    binding = _snapshot(route).bindings[0]
    monkeypatch.setattr(runtime, 'status', lambda: _snapshot(route, extra=(replace(binding, registration_id='other'),)))
    monkeypatch.setattr(runtime, 'call_bound', lambda *_a, **_k: pytest.fail('ambiguous registration used'))
    assert client.query_parent(_spec()) is None
    statuses = iter((_snapshot(route), _snapshot(route, epoch='new-epoch')))
    monkeypatch.setattr(runtime, 'status', lambda: next(statuses))
    monkeypatch.setattr(runtime, 'call_bound', lambda *_a, **_k: _response(client, _spec()))
    assert client.query_parent(_spec()) is None


def test_legacy_constructor_has_no_implicit_bound_target(monkeypatch):
    identity = WireIdentity(WireRole.SERVICE, 'execution-principal', 'execution')
    route = GatewayDelegateRoute('upstream.direct_write', 'gateway', 'instance', 'scope',
        WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run'), identity)
    runtime = ThreadedServiceClientRuntime(lambda: None)
    monkeypatch.setattr(runtime, 'call', lambda *_a, **_k: pytest.fail('ordinary call used'))
    monkeypatch.setattr(runtime, 'call_bound', lambda *_a, **_k: pytest.fail('missing target used'))
    client = GatewayParentServiceClient(runtime, route=route, host=_Host())
    assert client.query_parent(_spec()) is None


def test_missing_static_key_and_altered_route_fail_closed(composition, monkeypatch):
    session, route, peer, _keys, _profiles, _ = composition
    assert _trusted_parent_query_target(config(), route, peer, {}) is None
    monkeypatch.setattr(session.runtime, 'call_bound', lambda *_a, **_k: pytest.fail('mutated route used'))
    object.__setattr__(route, 'instance_id', 'mutated-instance')
    assert session.parent_client.query_parent(_spec()) is None


def test_other_service_same_scope_does_not_override_selected_binding(composition, monkeypatch):
    session, route, *_ = composition
    runtime, client = session.runtime, session.parent_client
    selected = _snapshot(route).bindings[0]
    other = replace(selected, service_id='other-gateway', instance_id='other-instance',
                    registration_id='other-registration')
    monkeypatch.setattr(runtime, 'status', lambda: _snapshot(route, extra=(other,)))
    calls = []
    def call_bound(target, operation, args, *, timeout_seconds):
        calls.append(target)
        return _response(client, _spec())
    monkeypatch.setattr(runtime, 'call_bound', call_bound)
    assert client.query_parent(_spec())['state'] == 'ACTIVE'
    assert calls[0].service_id == 'gateway' and calls[0].registration_id == 'registration'


def test_scope_mutation_after_composition_fails_closed(composition, monkeypatch):
    session, route, *_ = composition
    monkeypatch.setattr(session.runtime, 'call_bound', lambda *_a, **_k: pytest.fail('mutated scope used'))
    object.__setattr__(route.scope, 'account_ref', 'other-account')
    assert session.parent_client.query_parent(_spec()) is None
