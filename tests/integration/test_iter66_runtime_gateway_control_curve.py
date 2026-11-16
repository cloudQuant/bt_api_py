"""Authenticated loopback of Runtime control with synthetic Gateway resources."""
import queue
import socket
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_run_contract import AccessMode
from bt_api_runtime.gateway_control import GatewayDelegationProof
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import (GatewayControlGrant, GatewayDirectGrant,
    GatewayDirectServiceBinding, GatewayRuntimeControlProof)
from bt_api_py.runtime_gateway_control import (RuntimeGatewayControlBridge, RuntimeGatewayControlRoute,
                                               TrustedRuntimeControlBinding)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys

OPS = frozenset({'direct.control.apply', 'direct.control.status', 'direct.control.resume'})


def _client(endpoint, client, route):
    return parse_client_config({'client_config_schema_version': 1, 'process_role': 'application_client',
        'registration': {'application_ref': client.actor_ref},
        'clients': {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [{
            'mount': 'application.direct_write', 'service_id': route.service_id, 'instance_id': route.instance_id,
            'scope_ref': route.scope_ref, 'account_ref': route.scope.account_ref, 'transport_ref': 'gateway'}],
            'options': {'request_timeout_ms': 1000}}}},
        'communication': {'config_schema_version': 1, 'profiles': {'gateway': {
            'backend': 'zmq', 'config_schema_version': 1, 'endpoints': {'rpc_connect': endpoint},
            'security_profile_ref': 'client-key'}}}})


@pytest.mark.parametrize('server_revoked', [False, True])
def test_curve_control_scope_revocation_and_lifecycle(tmp_path, server_revoked):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        endpoint = f'tcp://127.0.0.1:{probe.getsockname()[1]}'
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    service = WireIdentity(WireRole.SERVICE, 'gateway-service', 'service')
    client = WireIdentity(WireRole.APPLICATION_CLIENT, 'runtime-principal', 'runtime-app')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy')
    route = RuntimeGatewayControlRoute('service', 'gateway', 'scope', scope)
    config = _client(endpoint, client, route)
    expiry = time.time() + 120
    proof = GatewayDelegationProof('delegation', 'alice', expiry)
    server_proof = GatewayRuntimeControlProof('delegation', 'alice', expiry, revoked=server_revoked)
    binding = TrustedRuntimeControlBinding(route, client, service, config.fingerprint(), proof)
    keys = {'client-key': ClientCurveKeys(server_keys[0], client_keys[0], client_keys[1])}
    service_config = parse_service_config({'service_config_schema_version': 1, 'process_role': 'service',
        'service_id': 'service', 'scope_ref': 'scope', 'transport_ref': 'server',
        'registration_policy_ref': 'policy', 'service_requirements_ref': 'requirements',
        'components': {'gateway': {'config_schema_version': 2, 'settings': {'instance_id': 'gateway',
            'bind_ports': ['service.direct_write'], 'options': {'role': 'trading_owner',
                'dispatch_enabled': True, 'control_enabled': True}}}},
        'communication': {'config_schema_version': 1, 'profiles': {'server': {'backend': 'zmq',
            'config_schema_version': 1, 'endpoints': {'rpc_bind': endpoint}, 'security_profile_ref': 'key'}}}})
    grant = ResourceGrant('gateway', 'service.direct_write', 'scope', scope, OPS)
    authority = TrustedServiceAuthority(identity=service, scope=WireScope('domain'), scope_ref='scope',
        scope_version='v1', security_ref='key', security_version='v1', registration_policy_ref='policy',
        registration_policy_version='v1', service_requirements_ref='requirements', service_requirements_version='v1',
        curve_public_key=server_keys[0], curve_secret_key=server_keys[1],
        principals={client_keys[0]: client.principal_ref}, grants={client.principal_ref: (PrincipalGrant(client, (grant,)),)})
    ready, stop = queue.Queue(), threading.Event()
    (tmp_path / 'guard').mkdir()

    def serve():
        owner = host = None
        try:
            state = []
            def factory():
                guard = LocalAccountSessionGuard(tmp_path / 'guard', 'account', 'owner')
                journal = GatewayDirectCommandJournal.create(tmp_path / 'journal', 'account', generation=guard.acquire())
                dispatcher = GatewayDirectDispatcher(journal, 'TEST', guard, lambda *_: None, lambda _: (), lambda _: True)
                state.append((journal, guard))
                return GatewayDirectOwnerResources(dispatcher, journal, guard.release)
            owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=10, busy_timeout_seconds=10)
            assert owner.start(10)
            journal, guard = state[0]
            control = owner.submit_service_call(lambda: journal.enable_controls(guard)).result(timeout_seconds=5)
            grants = {client.principal_ref: GatewayControlGrant(client.actor_ref, False, frozenset({'strategy'}),
                OPS, kind='runtime', runtime_proof=server_proof)}
            service_binding = GatewayDirectServiceBinding(journal, {'strategy': GatewayDirectGrant('s', frozenset({'X'}))},
                owner=owner, control=control, control_grants=grants)
            host = ServiceHost(service_config, authority, tmp_path / 'registry',
                lambda *_: ComponentResources.borrowed({'binding': service_binding}, ready=lambda: True))
            host.start()
            ready.put(('ready', grants))
            while not stop.is_set():
                host.serve_once(10)
        except BaseException as exc:
            ready.put(('error', repr(exc)))
        finally:
            if host is not None and host.state == 'started':
                host.close()
            if owner is not None:
                owner.close(10)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    bridge = None
    try:
        status, server_grants = ready.get(timeout=20)
        assert status == 'ready'
        bridge = RuntimeGatewayControlBridge({'strategy': route}, client_configs={'strategy': config},
            client_identity=client, expected_gateway=service, security_profiles=keys, proofs={'strategy': binding})
        bridge.start()
        first = bridge('strategy', 'direct.control.status', {'cause_id': 'missing'})
        if server_revoked:
            assert first == {'status': 'REQUEST_REJECTED', 'code': 'RUNTIME_PROOF_UNAVAILABLE'}
            return
        assert first['status'] == 'NOT_FOUND'
        runtime_client = bridge.client_for_strategy(
            SimpleNamespace(operators=[SimpleNamespace(name='alice')]), 'strategy')
        pause = runtime_client.pause(SimpleNamespace(access=AccessMode.GATEWAY, gateway=object()),
            strategy_uuid='strategy', operator='alice', command_id='runtime-command', reason='', freeze=True)
        assert pause.status == 'APPLIED'
        applied = bridge('strategy', 'direct.control.apply', {'cause_id': 'cause', 'mode': 'FREEZE'})
        assert applied['status'] == 'OBSERVED' and applied['active'] is True
        rejected = bridge('strategy', 'direct.control.apply', {'cause_id': 'bad', 'mode': 'INVALID'})
        assert rejected['status'] == 'REQUEST_REJECTED'
        for strategy, op, args in [('other', 'direct.control.status', {'cause_id': 'cause'}),
                                   ('strategy', 'direct.control.list', {}),
                                   ('strategy', 'direct.control.apply', {'cause_id': 'x', 'mode': 'DRAIN', 'strategy': 'other'})]:
            with pytest.raises(ValueError):
                bridge(strategy, op, args)
        assert bridge('strategy', 'direct.control.resume',
            {'cause_id': 'cause', 'expected_version': applied['version'], 'operation_id': 'release'})['active'] is False
    finally:
        if bridge is not None:
            bridge.close()
        stop.set()
        thread.join(15)
        assert not thread.is_alive()
    with pytest.raises(ValueError):
        bridge('strategy', 'direct.control.status', {'cause_id': 'cause'})

    bad = replace(binding, proof=replace(proof, revoked=True))
    denied = RuntimeGatewayControlBridge({'strategy': route}, client_configs={'strategy': config},
        client_identity=client, expected_gateway=service, security_profiles=keys, proofs={'strategy': bad})
    with pytest.raises(ValueError, match='PROOF_UNAVAILABLE'):
        denied.start()
