"""Offline tests: no account or provider facts are established."""
import hashlib
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.execution_parent_grant import OPERATIONS
from bt_api_py.execution_parent_strategy_session import (ExecutionParentStrategySession,
    _RETAINED_EXECUTION_SESSIONS)
from bt_api_py.service_config import ServiceConfigError, parse_client_config
from bt_api_py.service_runtime import (BoundExecutionParentTarget, ClientCurveKeys,
    ServiceClientRuntime, ServiceRuntimeError)
from bt_api_py.service_runtime_thread import ThreadedRuntimeError
from tests.unit.test_iter65_sdk_execution_routing_config import strategy_config


KEYS = ClientCurveKeys(b'a' * 40, b'b' * 40, b'c' * 40)
IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
PEER = WireIdentity(WireRole.SERVICE, 'execution-principal', 'exec-service')
SCOPE = WireScope('paper', account_ref='acct', strategy_ref='strategy', run_id='run')
TARGET = BoundExecutionParentTarget('broker.execution_parent', 'service.execution_parent',
    'exec-service', 'exec-instance', 'scope-a', SCOPE, IDENTITY, 'run', 'rpc',
    'test-security', 'tcp://127.0.0.1:7101', PEER, hashlib.sha256(KEYS.server_key).hexdigest(),
    'placeholder-registration', 'placeholder-epoch')


def config():
    return parse_client_config(strategy_config())


def make_session(target=TARGET, profiles=None):
    return ExecutionParentStrategySession(config(), target=target,
        security_profiles={'test-security': KEYS} if profiles is None else profiles)


def make_runtime(**options):
    return ServiceClientRuntime(config(), identity=IDENTITY, scope_resolver=lambda _: SCOPE,
        peer_resolver=lambda _: PEER, transport_factory=lambda *_: None, run_id='run', **options)


def test_joint_config_cross_validation_and_selection():
    with pytest.raises(ServiceConfigError, match='EXECUTION_REQUIRES_GATEWAY'):
        parse_client_config(strategy_config(gateway=False))
    runtime = make_runtime(registration_mounts=frozenset({'broker.execution_parent'}),
                           requested_operations={'broker.execution_parent': OPERATIONS})
    seen = []
    runtime._register = lambda binding, **_: seen.append(binding['mount'])
    runtime.start()
    assert seen == ['broker.execution_parent']
    assert runtime.healthy
    runtime.close()


@pytest.mark.parametrize('selection', [frozenset(), {'broker.execution_parent'},
    frozenset({'unknown'}), frozenset({'broker.order_query'})])
def test_invalid_selection_rejected(selection):
    with pytest.raises(ServiceRuntimeError, match='INVALID_REGISTRATION_MOUNTS'):
        make_runtime(registration_mounts=selection)


def test_operations_must_belong_to_selected_mount():
    with pytest.raises(ServiceRuntimeError, match='INVALID_ACTION_REQUEST'):
        make_runtime(registration_mounts=frozenset({'broker.execution_parent'}),
                     requested_operations={'broker.direct_write': frozenset({'direct.reserve'})})


@pytest.mark.parametrize('changed', [
    {'service_id': 'other'}, {'instance_id': 'other'}, {'scope_ref': 'other'},
    {'transport_ref': 'other'}, {'endpoint': 'tcp://127.0.0.1:7102'},
    {'server_key_fingerprint': '0' * 64},
    {'expected_peer': WireIdentity(WireRole.SERVICE, 'peer', 'different')},
    {'scope': replace(SCOPE, account_ref='other')},
    {'scope': replace(SCOPE, strategy_ref='other')},
    {'identity': WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'other')},
])
def test_rejects_mismatched_target(changed):
    with pytest.raises(ValueError, match='INVALID_EXECUTION_PARENT_CONFIGURATION'):
        make_session(replace(TARGET, **changed))


def test_rejects_wrong_key_profile():
    with pytest.raises(ValueError, match='INVALID_EXECUTION_PARENT_CONFIGURATION'):
        make_session(profiles={'test-security': replace(KEYS, server_key=b'd' * 40)})
    with pytest.raises(ValueError, match='INVALID_EXECUTION_PARENT_CONFIGURATION'):
        make_session(profiles={'other': KEYS})


def test_close_before_start_confirms_original_runtime():
    session = make_session()
    assert not session.cleanup_confirmed
    assert session.close().state == 'closed'
    assert session.cleanup_confirmed
    with pytest.raises(ThreadedRuntimeError):
        session.start()


def test_unknown_cleanup_is_retained_and_cannot_be_reissued(monkeypatch):
    session = make_session()
    monkeypatch.setattr('bt_api_py.execution_parent_strategy_session._strict_close_snapshot',
                        lambda *args, **kwargs: None)
    with pytest.raises(ThreadedRuntimeError, match='CLEANUP_FAILED'):
        session.close()
    assert not session.cleanup_confirmed
    assert _RETAINED_EXECUTION_SESSIONS[id(session)] is session
    with pytest.raises(ThreadedRuntimeError, match='CLEANUP_FAILED'):
        session.close()


def test_local_curve_session_registration_query_and_strict_cleanup():
    """Actual CURVE transport, with synthetic service and account data."""
    import queue
    import threading
    import time
    import zmq
    from bt_api_base.service_protocol import WIRE_PROTOCOL_VERSION, wire_contract_digest
    from bt_api_transport_zmq.server import CurveServerCredentials
    from bt_api_transport_zmq.service_v2 import ZmqServiceServer
    from bt_api_py.execution_parent_grant import QUERY, SUBMIT

    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    ready, failures, calls = queue.Queue(), queue.Queue(), []
    stop = threading.Event()

    def handler(principal, request):
        assert principal == IDENTITY.principal_ref
        operation = request.header.operation
        calls.append(operation)
        if operation == 'service.discover':
            result = {'protocol_version': WIRE_PROTOCOL_VERSION, 'wire_contract': wire_contract_digest(),
                'service_id': 'exec-service', 'ready': True, 'capabilities': [{
                    'instance_id': 'exec-instance', 'port': 'service.execution_parent',
                    'operations': [SUBMIT, QUERY]}]}
        elif operation == 'service.register':
            assert set(request.payload['operations']) == OPERATIONS
            result = {'registration_id': 'registration', 'service_epoch': 'epoch',
                'operations': [SUBMIT, QUERY], 'expires_at': time.time() + 120,
                'lease_remaining_seconds': 120, 'policy_version': 'policy', 'requirements_version': 'req'}
        elif operation == 'service.unregister':
            result = {'unregistered': True, 'registration_id': 'registration'}
        else:
            assert operation == QUERY
            result = {'local_only': True, 'found': False}
        return {'ok': True, 'code': 'OK', 'result': result}

    def serve():
        server = None
        try:
            server = ZmqServiceServer('tcp://127.0.0.1:*', PEER,
                {client_public: IDENTITY.principal_ref}, handler,
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

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    session = None
    try:
        endpoint = ready.get(timeout=5)
        assert endpoint
        raw = strategy_config()
        raw['communication']['profiles']['rpc']['endpoints']['rpc_connect'] = endpoint
        target = replace(TARGET, endpoint=endpoint,
                         server_key_fingerprint=hashlib.sha256(server_public).hexdigest())
        session = ExecutionParentStrategySession(parse_client_config(raw), target=target,
            security_profiles={'test-security': ClientCurveKeys(server_public, client_public, client_secret)})
        snapshot = session.start(timeout_seconds=8)
        assert [binding.mount for binding in snapshot.bindings] == ['broker.execution_parent']
        request = {'request_id': 'request', 'order_type': 'limit', 'spec': {
            'parent_ref': 'parent', 'account_scope': 'acct', 'strategy_scope': 'strategy',
            'execution_domain_ref': 'paper'}}
        assert session.client.query(request).outcome == 'LOCAL_NOT_FOUND'
        assert session.close(timeout_seconds=5).state == 'closed'
        assert session.cleanup_confirmed
        assert calls == ['service.discover', 'service.register', QUERY, 'service.unregister']
    finally:
        stop.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert failures.empty()


def test_failed_start_retains_original_until_proven_closed(monkeypatch):
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
    session = make_session()
    original = session.runtime
    def fail_start(self, **kwargs):
        raise ThreadedRuntimeError('START_TIMEOUT')
    monkeypatch.setattr(ThreadedServiceClientRuntime, 'start', fail_start)
    with pytest.raises(ThreadedRuntimeError, match='START_TIMEOUT'):
        session.start()
    assert _RETAINED_EXECUTION_SESSIONS[id(session)] is session
    assert session.runtime is original and not session.cleanup_confirmed
    with pytest.raises(ThreadedRuntimeError, match='CLEANUP_POLICY_UNPROVEN'):
        session.start()
    assert session.close().state == 'closed'
    assert session.cleanup_confirmed


def test_session_import_does_not_import_optional_execution_package():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c',
        'import sys; import bt_api_py.execution_parent_strategy_session; '
        'assert not any(name == "bt_api_execution" or name.startswith("bt_api_execution.") '
        'for name in sys.modules)'], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
