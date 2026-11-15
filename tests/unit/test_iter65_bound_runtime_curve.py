"""A local CURVE peer and synthetic operation exercise registration binding."""

from __future__ import annotations

import hashlib
import queue
import threading
import time
from dataclasses import replace

import pytest
import zmq
from bt_api_base.service_protocol import (
    WIRE_PROTOCOL_VERSION,
    WireIdentity,
    WireRole,
    WireScope,
    wire_contract_digest,
)
from bt_api_transport_zmq.server import CurveServerCredentials
from bt_api_transport_zmq.service_v2 import ZmqServiceServer

from bt_api_py.service_config import parse_service_config
from bt_api_py.service_runtime import (
    BoundServiceTarget,
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeRequestNotSentError,
    ZmqTransportFactory,
)
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime


def _config(endpoint):
    return parse_service_config({
        'service_config_schema_version': 1, 'process_role': 'service',
        'service_id': 'synthetic-execution', 'scope_ref': 'execution-root',
        'transport_ref': 'unused-listener', 'registration_policy_ref': 'policy',
        'service_requirements_ref': 'requirements',
        'components': {'monitor': {'config_schema_version': 2, 'settings': {
            'instance_id': 'unused-monitor', 'bind_ports': ['service.telemetry_ingress'],
            'options': {'role': 'gateway_heartbeat'}}}},
        'clients': {'gateway': {'config_schema_version': 1, 'settings': {
            'bindings': [{'mount': 'upstream.direct_write', 'service_id': 'synthetic-gateway',
                'instance_id': 'synthetic-instance', 'scope_ref': 'synthetic-scope',
                'account_ref': 'synthetic-account', 'transport_ref': 'gateway-transport'}],
            'options': {'request_timeout_ms': 2000, 'heartbeat_interval_seconds': 5}}}},
        'communication': {'config_schema_version': 1, 'profiles': {
            'unused-listener': {'backend': 'zmq', 'config_schema_version': 1,
                'endpoints': {'rpc_bind': 'tcp://127.0.0.1:1'}, 'security_profile_ref': 'unused'},
            'gateway-transport': {'backend': 'zmq', 'config_schema_version': 1,
                'endpoints': {'rpc_connect': endpoint}, 'security_profile_ref': 'synthetic-key'}}},
    })


def test_bound_call_uses_actual_local_curve_registration_and_trusted_key():
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    identity = WireIdentity(WireRole.SERVICE, 'synthetic-execution', 'synthetic-actor')
    peer = WireIdentity(WireRole.SERVICE, 'synthetic-gateway-principal', 'synthetic-gateway')
    scope = WireScope('synthetic-domain', account_ref='synthetic-account',
                      strategy_ref='synthetic-strategy', run_id='synthetic-run')
    ready, stop, failures, calls = queue.Queue(), threading.Event(), queue.Queue(), queue.Queue()

    def handler(principal, request):
        assert principal == identity.principal_ref
        operation = request.header.operation
        if operation == 'service.discover':
            result = {'protocol_version': WIRE_PROTOCOL_VERSION, 'wire_contract': wire_contract_digest(),
                      'service_id': 'synthetic-gateway', 'ready': True, 'capabilities': [{
                          'instance_id': 'synthetic-instance', 'port': 'service.direct_write',
                          'operations': ['direct.delegated_reserve']}]}
        elif operation == 'service.register':
            result = {'registration_id': 'synthetic-registration', 'service_epoch': 'synthetic-epoch',
                      'operations': ['direct.delegated_reserve'], 'expires_at': time.time() + 120,
                      'lease_remaining_seconds': 120, 'policy_version': 'policy', 'requirements_version': 'req'}
        elif operation == 'service.unregister':
            result = {'unregistered': True, 'registration_id': 'synthetic-registration'}
        else:
            assert operation == 'direct.delegated_reserve'
            calls.put(request)
            result = {'value': 'synthetic'}
        return {'ok': True, 'code': 'OK', 'result': result}

    def server_thread():
        server = None
        try:
            server = ZmqServiceServer('tcp://127.0.0.1:*', peer, {client_public: identity.principal_ref}, handler,
                credentials=CurveServerCredentials(server_public, server_secret))
            ready.put(server.endpoint)
            while not stop.is_set():
                server.serve_once(timeout_ms=20)
        except BaseException as error:
            failures.put(error)
            ready.put(None)
        finally:
            if server is not None:
                server.close()

    server = threading.Thread(target=server_thread, daemon=True)
    server.start()
    wrapper = None
    try:
        endpoint = ready.get(timeout=5)
        assert endpoint is not None
        keys = ClientCurveKeys(server_public, client_public, client_secret)

        def factory():
            return ServiceClientRuntime(_config(endpoint), identity=identity, scope_resolver=lambda _: scope,
                peer_resolver=lambda _: peer, transport_factory=ZmqTransportFactory({'synthetic-key': keys}),
                run_id='synthetic-run', requested_operations={'upstream.direct_write':
                                                             frozenset({'direct.delegated_reserve'})})

        wrapper = ThreadedServiceClientRuntime(factory)
        snapshot = wrapper.start(timeout_seconds=8)
        binding, = snapshot.bindings
        target = BoundServiceTarget('upstream.direct_write', 'service.direct_write', 'synthetic-gateway',
            'synthetic-instance', 'synthetic-scope', scope, identity, 'synthetic-run', 'gateway-transport',
            'synthetic-key', endpoint, peer, hashlib.sha256(server_public).hexdigest(),
            binding.registration_id, binding.service_epoch)
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            wrapper.call_bound(replace(target, server_key_fingerprint='0' * 64), 'direct.delegated_reserve', {})
        assert calls.empty()
        assert wrapper.call_bound(target, 'direct.delegated_reserve', {'sample': 'value'}) == {'value': 'synthetic'}
        request = calls.get(timeout=1)
        assert request.header.scope == scope and request.header.identity == identity
        assert request.payload['registration_id'] == target.registration_id
    finally:
        if wrapper is not None:
            assert wrapper.close(timeout_seconds=5).state == 'closed'
        stop.set()
        server.join(timeout=5)
        assert not server.is_alive()
    assert failures.empty()
