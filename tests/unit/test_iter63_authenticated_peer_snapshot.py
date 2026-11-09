"""CURVE registration proof, scoped to a live runtime binding."""
import hashlib
import multiprocessing

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from test_gateway_heartbeat_host import (_ACCOUNT_SCOPE, _INGRESS_ID, _INGRESS_PRINCIPAL,
                                         _endpoint, _monitor_process, _producer_config, _server_config)


def test_live_curve_registration_exposes_only_authenticated_peer(tmp_path):
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    keys={'server_public':server_public,'server_secret':server_secret,
          'client_public':client_public,'client_secret':client_secret}
    endpoint=_endpoint()
    ctx=multiprocessing.get_context('spawn')
    parent, child=ctx.Pipe()
    process=ctx.Process(target=_monitor_process,args=(child,_server_config(endpoint=endpoint),str(tmp_path/'db'),keys))
    process.start(); child.close()
    assert parent.poll(10) and parent.recv()==('ready',endpoint)
    local=WireIdentity(WireRole.SERVICE,'synthetic-producer-a','producer-a-service')
    peer=WireIdentity(WireRole.SERVICE,_INGRESS_PRINCIPAL,_INGRESS_ID)
    def factory():
        return ServiceClientRuntime(_producer_config(endpoint),identity=local,
            scope_resolver=lambda _:_ACCOUNT_SCOPE,peer_resolver=lambda _:peer,
            transport_factory=ZmqTransportFactory({'producer-client-v1':ClientCurveKeys(
                server_public,client_public,client_secret)}),
            requested_operations={'upstream.telemetry':frozenset({'monitor.heartbeat'})})
    runtime=ThreadedServiceClientRuntime(factory)
    try:
        status=runtime.start(timeout_seconds=8)
        assert status.healthy
        binding,=status.bindings
        assert binding.authenticated_peer == peer
        assert binding.server_key_fingerprint == hashlib.sha256(server_public).hexdigest()
        assert binding.registration_id and binding.service_epoch
        first_registration = binding.registration_id
        runtime.close(timeout_seconds=5)
        runtime = ThreadedServiceClientRuntime(factory)
        rebound = runtime.start(timeout_seconds=8).bindings[0]
        assert rebound.registration_id != first_registration
        assert rebound.authenticated_peer == peer
        assert rebound.server_key_fingerprint == hashlib.sha256(server_public).hexdigest()
    finally:
        runtime.close(timeout_seconds=5)
        parent.send('close')
        assert parent.poll(6) and parent.recv()=='closed'
        process.join(6)
        parent.close()
        assert process.exitcode==0
