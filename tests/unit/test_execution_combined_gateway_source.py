"""Synthetic combined Execution and local Gateway SOURCE composition only."""
import hashlib
import json
import os
import queue
import select
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, encode_wire_value
from bt_api_execution.frozen_algorithm_plan import algorithm_plan_digest
from bt_api_transport_zmq.server import CurveClientCredentials
from bt_api_transport_zmq.service_v2 import ZmqServiceClient

from bt_api_py import execution_combined_service_host as combined
from bt_api_py.execution_gateway_source_config import StdinSourceControl
from bt_api_py.execution_parent_grant import OPERATIONS, PORT, SUBMIT
from tests.unit.test_execution_combined_service_host import setup_combined, seed
from tests.unit.test_iter65_prepared_parent_curve_loopback import local_curve, source_calls, parent_counts  # noqa: F401
from tests.unit.test_service_host import _request
from test_iter65_atomic_submission_intake import atomic  # noqa: F401


def source_config(raw, directory):
    key_files = [directory / name for name in ('source-server.pub', 'source-client.pub', 'source-client.key')]
    return {'endpoint': 'tcp://127.0.0.1:19999', 'service_id': 'gateway-service',
        'instance_id': 'gateway-instance', 'scope_ref': 'gateway-scope',
        'peer_identity': {'role': 'service', 'principal_ref': 'gateway-principal', 'actor_ref': 'gateway-service'},
        'strategy_principal_ref': raw['parent']['strategy_grants'][0]['identity']['principal_ref'],
        'server_public_key_file': str(key_files[0]), 'client_public_key_file': str(key_files[1]),
        'client_secret_key_file': str(key_files[2]), 'timeout_ms': 200}


def test_default_combined_does_not_construct_source(tmp_path, monkeypatch):
    raw, _, _, _ = setup_combined(tmp_path)
    monkeypatch.setattr(combined, 'GatewaySourceClient', lambda *_: pytest.fail('unexpected source client'))
    host = combined.CombinedServiceHost(combined.parse_config(raw))
    host.start()
    try:
        assert host.poll() == {'parent': 'UNREADY', 'management': 'READY'}
        assert host.observe_source('x', request_id='y', request_sha256='a' * 64) == 'UNKNOWN'
    finally:
        host.close()


@pytest.mark.parametrize('mutation', [
    lambda raw: raw['gateway_source'].update(unexpected=True),
    lambda raw: raw['gateway_source'].pop('client_secret_key_file'),
    lambda raw: raw['gateway_source'].update(strategy_principal_ref='other'),
    lambda raw: raw['gateway_source'].update(endpoint='tcp://0.0.0.0:1234'),
    lambda raw: raw['gateway_source'].update(timeout_ms=True),
    lambda raw: raw['gateway_source'].update(timeout_ms=251),
    lambda raw: raw['gateway_source'].update(timeout_ms=0),
    lambda raw: raw['gateway_source'].update(service_id=raw['parent']['service']['service_id']),
    lambda raw: raw['gateway_source'].update(client_secret_key_file=raw['gateway_source']['client_public_key_file']),
    lambda raw: raw['gateway_source'].update(client_secret_key_file=raw['parent']['storage']['journal_root'] + '/secret'),
    lambda raw: raw['gateway_source'].update(client_secret_key_file=raw['management']['storage']['audit_root'] + '/secret'),
])
def test_invalid_source_config_rejected_before_resources(tmp_path, monkeypatch, mutation):
    raw, _, _, _ = setup_combined(tmp_path)
    raw['gateway_source'] = source_config(raw, tmp_path)
    mutation(raw)
    monkeypatch.setattr(combined, 'ParentServiceHost', lambda *_: pytest.fail('unexpected parent'))
    with pytest.raises(combined.CombinedServiceError, match='INVALID_CONFIGURATION'):
        combined.parse_config(raw)


def test_source_lifecycle_cleanup_uncertainty_retains_original_owner(tmp_path, monkeypatch):
    raw, _, _, _ = setup_combined(tmp_path)
    raw['gateway_source'] = source_config(raw, tmp_path)
    config = combined.parse_config(raw)
    events = []
    class Parent:
        def __init__(self, _): pass
        def start(self): events.append('parent-start')
        def close(self): events.append('parent-close')
    class Read:
        def __init__(self, _, *, borrowed_from): self.owner = borrowed_from
        def start(self): events.append('read-start')
        def close(self): events.append('read-close')
    class Source:
        def __init__(self, _, owner): self.owner, self.attempts = owner, 0
        def start(self): events.append('source-start'); raise RuntimeError
        def close(self):
            events.append('source-close'); self.attempts += 1
            if self.attempts == 1: raise RuntimeError
    monkeypatch.setattr(combined, 'ParentServiceHost', Parent)
    monkeypatch.setattr(combined, 'ReadServiceHost', Read)
    monkeypatch.setattr(combined, 'GatewaySourceClient', Source)
    host = combined.CombinedServiceHost(config)
    with pytest.raises(combined.CombinedServiceError, match='CLEANUP_UNKNOWN'):
        host.start()
    owner, read, source = host.parent, host.management, host.gateway_source
    assert read.owner is owner and source.owner is owner and host.state == 'close_uncertain'
    assert events == ['parent-start', 'read-start', 'source-start', 'source-close']
    host.close()
    assert events == ['parent-start', 'read-start', 'source-start', 'source-close',
                      'source-close', 'read-close', 'parent-close']
    assert host.state == 'closed'


def test_runtime_start_failure_retains_exact_runtime_for_cleanup(tmp_path, monkeypatch):
    from bt_api_py import execution_gateway_source_config as module
    raw, _, _, _ = setup_combined(tmp_path)
    raw['gateway_source'] = source_config(raw, tmp_path)
    config = combined.parse_config(raw)
    owner = SimpleNamespace(config=config.parent)
    class Runtime:
        def __init__(self, *_args, **_kwargs): self.calls = 0
        def start(self, **_kwargs): raise RuntimeError('synthetic startup failure')
        def close(self, **_kwargs):
            self.calls += 1
            if self.calls == 1: raise RuntimeError('synthetic uncertain close')
            return SimpleNamespace(state='closed', thread_alive=False)
    monkeypatch.setattr(module, '_keys', lambda _: SimpleNamespace(server_key=b'synthetic'))
    monkeypatch.setattr(module, 'ZmqTransportFactory', lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, 'ThreadedServiceClientRuntime', Runtime)
    source = module.GatewaySourceClient(config.gateway_source, owner)
    with pytest.raises(RuntimeError, match='startup failure'):
        source.start()
    runtime = source.runtime
    with pytest.raises(RuntimeError, match='uncertain close'):
        source.close()
    assert source.runtime is runtime and source.state == 'close_uncertain'
    source.close()
    assert source.runtime is None and source.state == 'closed' and runtime.calls == 2


def test_source_status_refuses_replaced_gateway_registration(tmp_path):
    from bt_api_py.execution_gateway_source_config import GatewaySourceClient
    from bt_api_py.service_runtime import BoundServiceTarget
    raw, _, _, _ = setup_combined(tmp_path)
    raw['gateway_source'] = source_config(raw, tmp_path)
    config = combined.parse_config(raw)
    parent, source_configured = config.parent, config.gateway_source
    target = BoundServiceTarget('upstream.direct_write', 'service.direct_write', source_configured.service_id,
        source_configured.instance_id, source_configured.scope_ref, parent.wire_scope, parent.identity,
        parent.wire_scope.run_id, 'execution-source-gateway', 'execution-source-key', source_configured.endpoint,
        source_configured.peer, 'a' * 64, 'registration', 'epoch')
    binding = SimpleNamespace(active=True, mount=target.mount, service_id=target.service_id,
        instance_id=target.instance_id, scope_ref=target.scope_ref, scope=target.scope,
        registration_id=target.registration_id, service_epoch=target.service_epoch,
        operations=frozenset({'direct.parent.source', 'direct.parent.register_v2'}),
        authenticated_peer=target.expected_peer, server_key_fingerprint=target.server_key_fingerprint)
    snapshot = SimpleNamespace(state='running', thread_alive=True, healthy=True, identity=target.identity,
                               run_id=target.run_id, bindings=(binding,))
    runtime = SimpleNamespace(bounded_status=lambda **_: snapshot)
    source = GatewaySourceClient(source_configured, SimpleNamespace(config=parent))
    source.state, source.runtime, source.target = 'running', runtime, target
    assert source.status() == 'READY'
    binding.registration_id = 'replacement'
    assert source.status() == 'UNREADY'


def test_stdin_control_is_bounded_nonblocking_and_eof_does_not_stop_owner():
    read_fd, write_fd = os.pipe()
    control = StdinSourceControl(read_fd)
    try:
        assert control.poll() == (False, False)
        os.write(write_fd, b'{"parent_ref":"p","request_id":"r","request_sha256":"' + b'a' * 64 + b'"}\n')
        item, full = control.poll()
        assert not full and control.request(item)['parent_ref'] == 'p'
        os.write(write_fd, b'{}\n' * 12)
        item, full = control.poll()
        assert not full and control.request(item) is None
        for _ in range(7): assert control.poll() == (b'{}', False)
        for _ in range(4): assert control.poll() == (False, True)
        os.write(write_fd, b'x' * 1025 + b'\n')
        item, full = control.poll()
        assert item is None and not full and control.request(item) is None
        os.close(write_fd); write_fd = None
        assert control.poll() == (False, False)
        assert control.poll() == (False, False)
    finally:
        control.close(); os.close(read_fd)
        if write_fd is not None: os.close(write_fd)


def test_control_output_backpressure_pauses_input_then_recovers():
    read_fd, write_fd = os.pipe()
    output_read, output_write = os.pipe()
    control = StdinSourceControl(read_fd, output_write)
    os.set_blocking(output_read, False)
    try:
        while control.accepting:
            assert control.report('EXECUTION_SOURCE_INVALID_REQUEST')
            control.flush()
        assert len(control._output) <= 4096 and not control.accepting
        os.write(write_fd, b'{}\n')
        try:
            while os.read(output_read, 65536): pass
        except BlockingIOError:
            pass
        control.flush()
        assert control.accepting
        assert control.poll() == (b'{}', False)
    finally:
        control.close()
        for fd in (read_fd, write_fd, output_read, output_write): os.close(fd)


@pytest.mark.skipif(os.environ.get('BT_API_SYNTHETIC_CURVE_LOOPBACK') != '1' or not zmq.has('curve'),
                    reason='requires explicit synthetic localhost CURVE sandbox opt-in')
@pytest.mark.parametrize('slow_source', [False, True])
def test_combined_real_gateway_source_from_original_receipt(local_curve, tmp_path, slow_source):  # noqa: F811
    env, rig = local_curve, local_curve['rig']
    raw, endpoint, parent_keys = configured_real_source(env, tmp_path)
    seed(raw)
    ready, errors, done, requests, results = (queue.Queue(), queue.Queue(), threading.Event(), queue.Queue(), queue.Queue())
    def serve():
        host = combined.CombinedServiceHost(combined.parse_config(raw))
        try:
            host.start(); ready.put((host.poll(), host))
            while not done.is_set():
                host.poll()
                try: payload = requests.get_nowait()
                except queue.Empty: continue
                results.put(host.observe_source(payload['spec']['parent_ref'], request_id=payload['request_id'],
                    request_sha256=hashlib.sha256(encode_wire_value(payload)).hexdigest()))
        except BaseException as error:
            errors.put(error); ready.put(None)
        finally:
            try: host.close()
            except BaseException as error: errors.put(error)
    thread = threading.Thread(target=serve); thread.start()
    client = None
    release = threading.Event()
    try:
        entry = ready.get(timeout=15)
        assert entry is not None, list(errors.queue)
        state, owner = entry
        assert state['gateway_source'] == 'READY', (state, list(errors.queue))
        assert owner.observe_source('outside-owner', request_id='r', request_sha256='a' * 64) == 'UNKNOWN'
        peer = WireIdentity(WireRole.SERVICE, env['service_identity'].principal_ref, env['service_identity'].actor_ref)
        client = ZmqServiceClient(endpoint, peer, credentials=CurveClientCredentials(*parent_keys))
        payload = submit_parent(client, rig)
        if slow_source:
            env['hook'][0] = lambda _: release.wait(2)
        before = time.monotonic()
        requests.put(payload)
        result = results.get(timeout=5)
        elapsed = time.monotonic() - before
        if slow_source:
            assert result == 'UNKNOWN' and elapsed < 1
            release.set()
            time.sleep(0.3)
            requests.put(payload)
            assert results.get(timeout=5) == 'UNKNOWN'
        else:
            assert result == 'OBSERVED'
            requests.put(payload)
            assert results.get(timeout=5) == 'HISTORICAL'
        assert len(source_calls(env)) == 1 and parent_counts(env) == (0, 0)
    finally:
        release.set()
        if client is not None: client.close()
        done.set(); thread.join(15)
    assert not thread.is_alive() and errors.empty()


def configured_real_source(env, tmp_path):
    rig = env['rig']
    root = tmp_path / 'combined'; root.mkdir(mode=0o700)
    raw, endpoint, parent_keys, _ = setup_combined(root)
    raw['parent']['service']['service_id'] = env['service_identity'].actor_ref
    raw['parent']['service_identity'].update(principal_ref=env['service_identity'].principal_ref,
                                            actor_ref=env['service_identity'].actor_ref)
    raw['parent']['wire_scope']['execution_domain_ref'] = rig.scope.execution_domain_ref
    raw['parent']['strategy_grants'][0]['identity'].update(principal_ref=rig.identity.principal_ref,
                                                           actor_ref=rig.identity.actor_ref)
    raw['management']['wire_scope']['domain'] = rig.scope.execution_domain_ref
    config = source_config(raw, root)
    target = env['target']
    config.update(endpoint=target.endpoint, service_id=target.service_id, instance_id=target.instance_id,
                  scope_ref=target.scope_ref, peer_identity={'role': 'service',
                      'principal_ref': target.expected_peer.principal_ref, 'actor_ref': target.expected_peer.actor_ref})
    keys = env['runtime']._factory._profiles['synthetic-key']
    for field, value in [('server_public_key_file', keys.server_key), ('client_public_key_file', keys.public_key),
                         ('client_secret_key_file', keys.secret_key)]:
        path = config[field]; open(path, 'wb').write(value); os.chmod(path, 0o600)
    raw['gateway_source'] = config
    return raw, endpoint, parent_keys


def submit_parent(client, rig):
    def request(operation, arguments, schema):
        return client.request(_request(operation, arguments, identity=rig.identity, scope=rig.scope,
                                       schema_id=schema), timeout=3).payload
    registered = request('service.register', {'registration_key': 'combined-source',
        'client_session_id': 'combined-source', 'instance_id': 'exec-a', 'port': PORT,
        'scope_ref': 'scope-a', 'operations': sorted(OPERATIONS)}, 'sdk.service.control')
    assert registered['ok'], registered
    binding = registered['result']
    payload = rig.parent(parent_ref='combined-source-parent')
    payload['algorithm_plan']['build_id'] = 'build-a'
    payload['spec']['plan_digest'] = algorithm_plan_digest(payload['algorithm_plan'])
    result = request(SUBMIT, {'registration_id': binding['registration_id'],
        'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': payload},
        'sdk.service.call')
    assert result['ok'] and result['result']['status'] == 'RECEIVED'
    return payload


def test_cli_control_processes_one_command_and_survives_eof():
    code = '''
import time
from types import SimpleNamespace
import bt_api_py.execution_combined_service_host as module
module.load_config = lambda path: SimpleNamespace(gateway_source=object())
class Host:
    def __init__(self, config): self.calls = 0
    def start(self): pass
    def poll(self):
        time.sleep(0.01)
        return {'parent': 'READY', 'management': 'READY', 'gateway_source': 'READY'}
    def observe_source(self, parent_ref, *, request_id, request_sha256):
        self.calls += 1
        return 'UNKNOWN'
    def close(self): print('CALLS_' + str(self.calls), flush=True)
module.CombinedServiceHost = Host
raise SystemExit(module.main(['run', '--config', 'unused', '--source-control-stdin']))
'''
    process = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    try:
        lines = []
        buffer = b''
        deadline = time.monotonic() + 8
        while len(lines) < 3 and time.monotonic() < deadline:
            if select.select([process.stdout], [], [], 0.2)[0]:
                buffer += os.read(process.stdout.fileno(), 4096)
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1); lines.append(line.decode())
        assert lines == ['EXECUTION_COMBINED_PARENT_READY', 'EXECUTION_COMBINED_MANAGEMENT_READY',
                         'EXECUTION_COMBINED_GATEWAY_SOURCE_READY']
        payload = {'parent_ref': 'synthetic', 'request_id': 'synthetic', 'request_sha256': 'a' * 64}
        process.stdin.write(json.dumps(payload).encode() + b'\n'); process.stdin.flush()
        assert select.select([process.stdout], [], [], 5)[0]
        assert os.read(process.stdout.fileno(), 4096).strip() == b'EXECUTION_SOURCE_UNKNOWN'
        process.stdin.close(); process.stdin = None
        time.sleep(0.15)
        assert process.poll() is None
        process.send_signal(signal.SIGTERM)
        output, error = process.communicate(timeout=5)
        assert process.returncode == 0 and not error
        assert output.splitlines() == [b'CALLS_1', b'EXECUTION_COMBINED_STOPPED']
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM); process.wait(timeout=5)


@pytest.mark.parametrize('condition', ['signal', 'unavailable'])
def test_cli_never_starts_source_after_signal_or_listener_failure(condition):
    code = '''
import os, signal
from types import SimpleNamespace
import bt_api_py.execution_combined_service_host as module
module.load_config = lambda path: SimpleNamespace(gateway_source=object())
class Host:
    def __init__(self, config): self.polls = 0; self.calls = 0
    def start(self): pass
    def poll(self):
        self.polls += 1
        if self.polls == 2:
            if CONDITION == 'signal': os.kill(os.getpid(), signal.SIGTERM)
            else: return {'parent': 'UNAVAILABLE', 'management': 'READY', 'gateway_source': 'READY'}
        return {'parent': 'READY', 'management': 'READY', 'gateway_source': 'READY'}
    def observe_source(self, *args, **kwargs): self.calls += 1; return 'OBSERVED'
    def close(self): print('CALLS_' + str(self.calls), flush=True)
module.CombinedServiceHost = Host
raise SystemExit(module.main(['run', '--config', 'unused', '--source-control-stdin']))
'''.replace('CONDITION', repr(condition))
    process = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    command = {'parent_ref': 'p', 'request_id': 'r', 'request_sha256': 'a' * 64}
    try:
        output, error = process.communicate(json.dumps(command).encode() + b'\n', timeout=5)
        assert not error and b'CALLS_0' in output and b'EXECUTION_SOURCE_OBSERVED' not in output
        assert process.returncode == (0 if condition == 'signal' else 1)
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM); process.wait(timeout=5)


def test_cli_exits_when_stdout_consumer_stops_reading():
    code = '''
import time
from types import SimpleNamespace
import bt_api_py.execution_combined_service_host as module
module.load_config = lambda path: SimpleNamespace(gateway_source=object())
class Host:
    def __init__(self, config): pass
    def start(self): pass
    def poll(self):
        time.sleep(0.0001)
        return {'parent': 'READY', 'management': 'READY', 'gateway_source': 'READY'}
    def observe_source(self, *args, **kwargs): return 'UNKNOWN'
    def close(self): pass
module.CombinedServiceHost = Host
raise SystemExit(module.main(['run', '--config', 'unused', '--source-control-stdin']))
'''
    process = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        os.read(process.stdout.fileno(), 4096)
        os.set_blocking(process.stdin.fileno(), False)
        deadline, written = time.monotonic() + 5, 0
        while time.monotonic() < deadline:
            try:
                written += os.write(process.stdin.fileno(), b'{}\n' * 1000)
            except BlockingIOError:
                time.sleep(0.001)
        assert written > 8192 and process.poll() is None
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=3)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM); process.wait(timeout=5)
        process.stdin.close(); process.stdout.close(); process.stderr.close()


@pytest.mark.skipif(os.environ.get('BT_API_SYNTHETIC_CURVE_LOOPBACK') != '1' or not zmq.has('curve'),
                    reason='requires explicit synthetic localhost CURVE sandbox opt-in')
def test_cli_stdin_real_gateway_and_original_parent(local_curve, tmp_path):  # noqa: F811
    env, rig = local_curve, local_curve['rig']
    raw, endpoint, parent_keys = configured_real_source(env, tmp_path)
    seed(raw)
    path = tmp_path / 'combined.json'
    path.write_text(json.dumps(raw), encoding='utf-8'); path.chmod(0o600)
    process = subprocess.Popen([sys.executable, '-m', 'bt_api_py.execution_combined_service_host',
        'run', '--config', str(path), '--source-control-stdin'], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pending = bytearray()
    def read_line(timeout=10):
        deadline = time.monotonic() + timeout
        while b'\n' not in pending and time.monotonic() < deadline:
            if select.select([process.stdout], [], [], 0.2)[0]:
                block = os.read(process.stdout.fileno(), 4096)
                if not block: break
                pending.extend(block)
        assert b'\n' in pending
        line, _, tail = pending.partition(b'\n')
        pending[:] = tail
        return line.decode()
    client = None
    try:
        first, second, third = read_line(), read_line(), read_line()
        assert first in ('EXECUTION_COMBINED_PARENT_READY', 'EXECUTION_COMBINED_PARENT_UNREADY')
        assert second == 'EXECUTION_COMBINED_MANAGEMENT_READY'
        assert third == 'EXECUTION_COMBINED_GATEWAY_SOURCE_READY'
        peer = WireIdentity(WireRole.SERVICE, env['service_identity'].principal_ref, env['service_identity'].actor_ref)
        client = ZmqServiceClient(endpoint, peer, credentials=CurveClientCredentials(*parent_keys))
        time.sleep(0.2)
        payload = submit_parent(client, rig)
        command = {'parent_ref': payload['spec']['parent_ref'], 'request_id': payload['request_id'],
                   'request_sha256': hashlib.sha256(encode_wire_value(payload)).hexdigest()}
        process.stdin.write(json.dumps(command).encode() + b'\n'); process.stdin.flush()
        line = read_line()
        assert line == 'EXECUTION_SOURCE_OBSERVED', (line, len(source_calls(env)))
        process.stdin.write(json.dumps(command).encode() + b'\n'); process.stdin.flush()
        assert read_line() == 'EXECUTION_SOURCE_HISTORICAL'
        assert len(source_calls(env)) == 1 and parent_counts(env) == (0, 0)
        process.stdin.close(); process.stdin = None
        assert process.poll() is None
        process.send_signal(signal.SIGTERM)
        output, error = process.communicate(timeout=10)
        assert process.returncode == 0 and output.strip() == b'EXECUTION_COMBINED_STOPPED' and not error
    finally:
        if client is not None: client.close()
        if process.poll() is None:
            process.send_signal(signal.SIGTERM); process.wait(timeout=10)
