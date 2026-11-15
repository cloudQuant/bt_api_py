"""Opt-in synthetic localhost CURVE; no provider, real account or trade."""
from dataclasses import replace
import os
import queue
import threading
import time

import pytest
import zmq
from bt_api_base.service_protocol import WIRE_PROTOCOL_VERSION, WireIdentity, WireRole, wire_contract_digest
from bt_api_transport_zmq.server import CurveServerCredentials
from bt_api_transport_zmq.service_v2 import ZmqServiceServer
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ServiceRuntimeError, ZmqTransportFactory
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import ServiceDispatchAdmissionError
from tests.unit.test_iter65_bound_runtime_curve import _config
from tests.unit.test_iter65_execution_parent_registry_grant import fixture, call

pytestmark = pytest.mark.skipif(os.environ.get('BT_API_SYNTHETIC_CURVE_LOOPBACK') != '1' or not zmq.has('curve'),
                                reason='requires opt-in localhost CURVE')
OPS = ['direct.parent.query', 'direct.parent.register']


@pytest.mark.parametrize('case', ['renew', 'sleep', 'retire', 'socket_replace', 'lost_reply',
                                 'missing_receipt', 'no_socket_io', 'forged_observation', 'fork',
                                 'delayed_registration', 'nonstrict', 'clock_unavailable',
                                 'nonce_unavailable', 'not_enabled', 'two_runtimes', 'duplicate_register'])
def test_fresh_intake_strict_registration_and_renewal_evidence(case, monkeypatch):
    registry, _, identity, scope, _, received, result = fixture(system_clock=True)
    assert result['ok']
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    caller = WireIdentity(WireRole.SERVICE, 'synthetic-execution', 'synthetic-actor')
    peer = WireIdentity(WireRole.SERVICE, 'synthetic-gateway-principal', 'synthetic-gateway')
    ready, errors, stop = queue.Queue(), queue.Queue(), threading.Event()
    registrations = {}
    def handler(principal, request):
        assert principal == caller.principal_ref
        op = request.header.operation
        if op == 'service.discover':
            result = {'protocol_version': WIRE_PROTOCOL_VERSION, 'wire_contract': wire_contract_digest(),
                'service_id': 'synthetic-gateway', 'ready': True, 'capabilities': [
                    {'instance_id': 'synthetic-instance', 'port': 'service.direct_write', 'operations': OPS}]}
        elif op in ('service.register', 'service.renew'):
            if case == 'delayed_registration' and op == 'service.register':
                time.sleep(0.3)  # synthetic response delay consumes the lease
            if op == 'service.register':
                key = request.payload['registration_key']
                registration = registrations.setdefault(key, 'registration-' + str(len(registrations)))
            else:
                registration = request.payload['registration_id']
            result = {'registration_id': registration, 'service_epoch': 'epoch', 'operations': OPS,
                'expires_at': time.time() + 3, 'lease_remaining_seconds': 3,
                'policy_version': 'policy', 'requirements_version': 'req'}
        elif op == 'service.unregister':
            result = {'unregistered': True, 'registration_id': request.payload['registration_id']}
        else:
            pytest.fail('unexpected operation: ' + op)
        return {'ok': True, 'code': 'OK', 'result': result}
    def serve():
        server = None
        try:
            server = ZmqServiceServer('tcp://127.0.0.1:*', peer, {client_public: caller.principal_ref}, handler,
                credentials=CurveServerCredentials(server_public, server_secret))
            ready.put(server.endpoint)
            while not stop.is_set():
                server.serve_once(timeout_ms=20)
        except BaseException as error:
            errors.put(error); ready.put(None)
        finally:
            if server is not None:
                server.close()
    thread = threading.Thread(target=serve, daemon=True); thread.start()
    runtime = other_runtime = None
    try:
        endpoint = ready.get(timeout=5)
        assert endpoint
        config = _config(endpoint).to_public_dict()
        config['clients']['gateway']['settings']['bindings'][0]['account_ref'] = scope.account_ref
        if case == 'clock_unavailable':
            from bt_api_py import service_runtime
            monkeypatch.setattr(service_runtime, '_EXECUTION_SLEEP_CLOCK_ID', None)
        if case == 'nonce_unavailable':
            from bt_api_py import service_runtime
            monkeypatch.setattr(service_runtime, '_EXECUTION_NONCE_BYTES', None)
        runtime = ServiceClientRuntime(parse_service_config(config), identity=caller, scope_resolver=lambda _: scope,
            peer_resolver=lambda _: peer,
            transport_factory=ZmqTransportFactory({'synthetic-key': ClientCurveKeys(
                server_public, client_public, client_secret)}, strict_single_connection=case != 'nonstrict'),
            run_id=scope.run_id, client_session_id='synthetic-shared-session',
            requested_operations={'upstream.direct_write': frozenset(OPS)},
            execution_upstream_lease=case != 'not_enabled')
        runtime.start()
        binding, = runtime._bindings
        token = runtime._execution_upstream_lease_for(binding)
        if case in ('nonstrict', 'clock_unavailable', 'nonce_unavailable', 'not_enabled'):
            assert token is None and runtime.healthy
            assert call(registry, identity, scope, result['result'])[0]['ok']
            context, arguments = received[-1]
            with pytest.raises(ServiceDispatchAdmissionError, match='EXECUTION_UPSTREAM_UNAVAILABLE'):
                context.dispatch_admission.bind_execution_upstream(context, arguments, runtime, binding)
            context.dispatch_admission.close()
            return
        assert token is not None
        if case == 'duplicate_register':
            raw_binding = runtime._config['clients']['gateway']['settings']['bindings'][0]
            caps = [{'instance_id': 'synthetic-instance', 'port': 'service.direct_write', 'operations': OPS}]
            runtime._register_port(raw_binding, scope, 'service.direct_write', caps, timeout=2,
                                   required=True, heartbeat_interval_seconds=5)
            assert len(registrations) == 1
            assert runtime._execution_upstream_lease_for(binding) is None
            assert not runtime._execution_upstream_clock_leases
            return
        if case == 'two_runtimes':
            other_runtime = ServiceClientRuntime(parse_service_config(config), identity=caller,
                scope_resolver=lambda _: scope, peer_resolver=lambda _: peer,
                transport_factory=ZmqTransportFactory({'synthetic-key': ClientCurveKeys(
                    server_public, client_public, client_secret)}, strict_single_connection=True),
                run_id=scope.run_id, client_session_id='synthetic-shared-session',
                requested_operations={'upstream.direct_write': frozenset(OPS)}, execution_upstream_lease=True)
            other_runtime.start()
            other_binding, = other_runtime._bindings
            assert other_runtime._execution_upstream_lease_for(other_binding) is not None
            assert len(registrations) == 2 and all(len(key) == 64 for key in registrations)
            assert binding.registration_id != other_binding.registration_id
            assert runtime._execution_upstream_lease_for(binding) is token
        if case == 'delayed_registration':
            from bt_api_py.service_runtime import _ExecutionSuspendClock
            assert 0 < token.deadline - _ExecutionSuspendClock.now(token.clock) < 2_800_000_000
        assert call(registry, identity, scope, result['result'])[0]['ok']
        context, arguments = received[-1]
        permit = context.dispatch_admission
        permit.bind_execution_upstream(context, arguments, runtime, binding)
        with permit.intake_scope() as check:
            check()
            if case == 'sleep':
                # Test-only synthetic suspend and wall rollback; neither
                # changes the operating system's clocks.
                from bt_api_py.service_runtime import _ExecutionSuspendClock
                wall_now = time.time
                monkeypatch.setattr(time, 'time', lambda: wall_now() - 3600)
                monkeypatch.setattr(_ExecutionSuspendClock, 'now', lambda _: token.deadline)
                with pytest.raises(ServiceDispatchAdmissionError, match='EXECUTION_UPSTREAM_UNAVAILABLE'):
                    check()
            elif case == 'retire':
                token.generation._retire()
                with pytest.raises(ServiceDispatchAdmissionError, match='EXECUTION_UPSTREAM_UNAVAILABLE'):
                    check()
            elif case == 'socket_replace':
                client = token.proof[1]
                old_binding = client._socket_binding
                client._socket_binding = object()
                try:
                    with pytest.raises(ServiceDispatchAdmissionError, match='EXECUTION_UPSTREAM_UNAVAILABLE'):
                        check()
                finally:
                    client._socket_binding = old_binding
            elif case == 'no_socket_io':
                client = token.proof[1]
                with monkeypatch.context() as local:
                    local.setattr(client, '_socket_binding_current', lambda _: pytest.fail('socket used under journal lock'))
                    check()
        permit.close()
        if case == 'lost_reply':
            adapter = token.proof[0]
            monkeypatch.setattr(adapter, 'request', lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError()))
            with pytest.raises(ServiceRuntimeError, match='REQUIRED_RENEWAL_FAILED'):
                runtime.renew()
            assert not runtime._execution_upstream_clock_leases
            return
        if case == 'missing_receipt':
            from bt_api_transport_zmq import service_v2
            monkeypatch.setattr(service_v2, '_consume_strict_reply_receipt', lambda *_args: False)
        if case == 'forged_observation':
            runtime._publish_execution_upstream_lease(binding, {
                'execution_sleep_started': token.deadline + 100, 'execution_strict_proof': token.proof}, 100)
            assert runtime._execution_upstream_lease_for(binding) is None
            return
        if case == 'fork':
            import signal
            if not hasattr(os, 'fork'):
                pytest.skip('fork unavailable')
            pid = os.fork()
            if pid == 0:
                signal.alarm(2)
                try:
                    runtime._execution_upstream_lease_for(binding)
                except Exception:
                    os._exit(0)
                os._exit(1)
            _, status = os.waitpid(pid, 0)
            assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        runtime.renew()
        renewed, = runtime._bindings
        fresh = runtime._execution_upstream_lease_for(renewed)
        if case in ('sleep', 'retire', 'missing_receipt'):
            assert fresh is None
        else:
            assert fresh is not None and fresh is not token and fresh.proof is token.proof
        assert runtime._execution_upstream_lease_for(binding) is None
    finally:
        if other_runtime is not None:
            other_runtime.close()
        if runtime is not None:
            runtime.close()
        stop.set(); thread.join(5)
        registry.close()
        assert not thread.is_alive() and errors.empty()
