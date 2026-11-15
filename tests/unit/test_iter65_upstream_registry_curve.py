"""Opt-in localhost CURVE with real synthetic Gateway Registry; no order RPC."""
import os
import queue
import threading
import time

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_transport_zmq.server import CurveServerCredentials
from bt_api_transport_zmq.service_v2 import ZmqServiceServer
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ServiceRuntimeError, ZmqTransportFactory
from tests.unit.test_iter65_fresh_intake_host import build_host, SCOPE

pytestmark = pytest.mark.skipif(os.environ.get('BT_API_SYNTHETIC_CURVE_LOOPBACK') != '1' or not zmq.has('curve'),
                                reason='requires opt-in localhost CURVE')
OPS = frozenset({'direct.parent.register', 'direct.parent.query'})


@pytest.fixture
def registry_curve(tmp_path, monkeypatch):
    # Existing fixture creates a temporary real Registry and a fake intake;
    # synthetic_auth=False avoids replacing real client authentication.
    host, intake, _, registry = build_host(tmp_path, monkeypatch, synthetic_auth=False)
    caller = host._authority.identity
    peer = WireIdentity(WireRole.SERVICE, 'gateway-peer', 'gateway-service')
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    stop, ready, failures = threading.Event(), queue.Queue(), queue.Queue()
    calls = []
    drop_first = threading.Event()
    release_reply = threading.Event()
    reply_finished = threading.Event()
    reject_first = threading.Event()
    def handler(principal, envelope):
        if envelope.header.operation == 'service.register' and reject_first.is_set():
            reject_first.clear()
            result = {'ok': False, 'code': 'DENIED', 'result': {}}
            calls.append((envelope.payload['registration_key'], result))
            return result
        result = registry.handle(principal, envelope)
        if envelope.header.operation == 'service.register':
            calls.append((envelope.payload['registration_key'], result))
            if drop_first.is_set():
                drop_first.clear()
                assert release_reply.wait(5)
                reply_finished.set()
        return result
    def serve():
        server = None
        try:
            server = ZmqServiceServer('tcp://127.0.0.1:*', peer, {client_public: caller.principal_ref}, handler,
                credentials=CurveServerCredentials(server_public, server_secret))
            ready.put(server.endpoint)
            while not stop.is_set():
                server.serve_once(timeout_ms=20)
        except BaseException as error:
            failures.put(error); ready.put(None)
        finally:
            if server is not None:
                server.close()
    thread = threading.Thread(target=serve, daemon=True); thread.start()
    runtimes = []
    try:
        endpoint = ready.get(timeout=5)
        assert endpoint
        raw = host._config.to_public_dict()
        raw['communication']['profiles']['upstream-connect']['endpoints']['rpc_connect'] = endpoint
        # Exact profile name is a public synthetic config value, not a secret.
        profile = raw['communication']['profiles']['upstream-connect']['security_profile_ref']
        factory = ZmqTransportFactory({profile: ClientCurveKeys(server_public, client_public, client_secret)},
                                      strict_single_connection=True)
        def new_runtime():
            value = ServiceClientRuntime(parse_service_config(raw), identity=caller, scope_resolver=lambda _: SCOPE,
                peer_resolver=lambda _: peer, transport_factory=factory, run_id=SCOPE.run_id,
                client_session_id='same-explicit-session', requested_operations={'upstream.direct_write': OPS},
                execution_upstream_lease=True)
            runtimes.append(value)
            return value
        yield registry, calls, new_runtime, drop_first, release_reply, reply_finished, reject_first
    finally:
        release_reply.set()
        for value in reversed(runtimes):
            value.close()
        stop.set(); thread.join(5)
        host.close(); intake.close(); registry.close()
        assert not thread.is_alive() and failures.empty()


def test_two_runtimes_same_session_issue_distinct_real_registry_keys(registry_curve):
    registry, calls, make, _, _, _, _ = registry_curve
    a, b = make(), make()
    a.start(); b.start()
    binding_a, = a._bindings
    binding_b, = b._bindings
    assert a._execution_upstream_lease_for(binding_a) is not None
    assert b._execution_upstream_lease_for(binding_b) is not None
    assert binding_a.registration_id != binding_b.registration_id
    assert len(calls) == 2 and all(item[1]['ok'] for item in calls)
    assert len(calls[0][0]) == len(calls[1][0]) == 64 and calls[0][0] != calls[1][0]
    with registry._lock:
        rows = registry._db.execute('SELECT registration_key FROM sessions WHERE principal=?',
                                    (a.identity.principal_ref,)).fetchall()
    assert {row['registration_key'] for row in rows} == {item[0] for item in calls}
    # Coexisting local test registrations prove neither old writer retirement
    # nor global service-owner exclusivity.


def test_real_registry_idempotent_replay_does_not_reissue_token(registry_curve):
    _, calls, make, _, _, _, _ = registry_curve
    runtime = make(); runtime.start()
    original, = runtime._bindings
    raw_binding = runtime._config['clients']['gateway']['settings']['bindings'][0]
    caps = [{'instance_id': 'gateway-a', 'port': 'service.direct_write', 'operations': list(OPS)}]
    runtime._register_port(raw_binding, SCOPE, 'service.direct_write', caps, timeout=2,
                           required=True, heartbeat_interval_seconds=5)
    assert len(calls) == 2 and calls[0][0] == calls[1][0]
    assert calls[0][1]['result']['registration_id'] == calls[1][1]['result']['registration_id']
    assert runtime._execution_upstream_lease_for(original) is None
    assert not runtime._execution_upstream_clock_leases


def test_lost_first_reply_same_key_replays_without_new_token(registry_curve):
    registry, calls, make, drop, release, finished, _ = registry_curve
    runtime = make()
    old_session = runtime.client_session_id
    drop.set()
    try:
        with pytest.raises(ServiceRuntimeError):
            runtime.start()
    finally:
        release.set()
    assert finished.wait(3)
    assert len(calls) == 1 and calls[0][1]['ok']
    first_key = calls[0][0]
    first_registration = calls[0][1]['result']['registration_id']
    assert runtime._unknown_registration_count == 1
    assert not runtime._execution_upstream_clock_leases
    # Recover uses a new session/key, but cannot establish that the earlier
    # registration or any message was absent or fenced.
    runtime.recover()
    assert len(calls) == 2 and calls[1][0] != first_key
    assert runtime._unknown_registration_count == 1
    assert runtime._last_unknown_registration_key == first_key
    with registry._lock:
        active = registry._db.execute('SELECT registration_id FROM sessions WHERE principal=?',
                                      (runtime.identity.principal_ref,)).fetchall()
    assert first_registration in {row['registration_id'] for row in active}
    # Test-only restoration forces the previously attempted key to be sent
    # again to the real Registry, which idempotently returns the old session.
    runtime.client_session_id = old_session
    raw_binding = runtime._config['clients']['gateway']['settings']['bindings'][0]
    caps = [{'instance_id': 'gateway-a', 'port': 'service.direct_write', 'operations': list(OPS)}]
    runtime._register_port(raw_binding, SCOPE, 'service.direct_write', caps, timeout=2,
                           required=True, heartbeat_interval_seconds=5)
    assert calls[-1][0] == first_key and calls[-1][1]['ok']
    assert calls[-1][1]['result']['registration_id'] == first_registration
    assert not runtime._execution_upstream_clock_leases
    assert runtime._unknown_registration_count == 1


def test_definitive_rejection_then_same_key_success_does_not_issue_token(registry_curve):
    _, calls, make, _, _, _, reject = registry_curve
    runtime = make()
    old_session = runtime.client_session_id
    reject.set()
    with pytest.raises(ServiceRuntimeError, match='DENIED'):
        runtime.start()
    assert len(calls) == 1 and not calls[0][1]['ok']
    old_key = calls[0][0]
    assert runtime._unknown_registration_count == 0
    runtime.recover()
    assert calls[1][0] != old_key and calls[1][1]['ok']
    runtime.client_session_id = old_session
    raw_binding = runtime._config['clients']['gateway']['settings']['bindings'][0]
    caps = [{'instance_id': 'gateway-a', 'port': 'service.direct_write', 'operations': list(OPS)}]
    runtime._register_port(raw_binding, SCOPE, 'service.direct_write', caps, timeout=2,
                           required=True, heartbeat_interval_seconds=5)
    assert calls[-1][0] == old_key and calls[-1][1]['ok']
    assert not runtime._execution_upstream_clock_leases
