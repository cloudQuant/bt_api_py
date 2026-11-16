"""Synthetic loopback only: no provider, account, strategy or order calls."""
import copy
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import sqlite3
import subprocess
import sys

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.execution_management_read_client import ExecutionManagementReadClient
from bt_api_py.execution_read_service_host import OPERATIONS
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import BoundExecutionManagementReadTarget, ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples/services/execution-read-service.example.json'
pytestmark = pytest.mark.skipif(not zmq.has('curve'), reason='CURVE unavailable')


def setup(tmp_path):
    tmp_path = tmp_path.resolve()

    raw = json.loads(EXAMPLE.read_text())
    for name in ('journal', 'audit', 'keys'):
        (tmp_path / name).mkdir(mode=0o700)
    raw['storage'].update(journal_root=str(tmp_path / 'journal'), audit_root=str(tmp_path / 'audit'))
    server_pub, server_sec = zmq.curve_keypair()
    client_pub, client_sec = zmq.curve_keypair()
    for name, value in (('server.public', server_pub), ('server.secret', server_sec), ('client.public', client_pub)):
        path = tmp_path / 'keys' / name
        path.write_bytes(value); path.chmod(0o600)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    raw['listener'].update(endpoint=f'tcp://127.0.0.1:{port}',
        server_public_key_file=str(tmp_path / 'keys/server.public'),
        server_secret_key_file=str(tmp_path / 'keys/server.secret'),
        client_public_key_file=str(tmp_path / 'keys/client.public'))
    path = tmp_path / 'service.json'
    path.write_text(json.dumps(raw))
    return raw, path, (server_pub, client_pub, client_sec)


def launch(path):
    process = subprocess.Popen([sys.executable, '-m', 'bt_api_py.execution_read_service_host', 'run', '--config', str(path)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert select.select([process.stdout], [], [], 20)[0], 'listener start timed out'
    line = process.stdout.readline().strip()
    return process, line


def runtime(raw, keys, identity=None, scope=None, operations=OPERATIONS):
    grant = raw['grant']; listener = raw['listener']
    app = identity or WireIdentity(WireRole.APPLICATION_CLIENT, 'management-client', 'operator-client')
    scope = scope or WireScope('example-domain', account_ref='account-example', strategy_ref='strategy-example')
    peer = WireIdentity(WireRole.SERVICE, 'execution-read-service', 'execution-read-host')
    config = parse_client_config({'client_config_schema_version': 1, 'process_role': 'application_client',
        'registration': {'application_ref': app.principal_ref},
        'clients': {'execution': {'config_schema_version': 1, 'settings': {
            'bindings': [{'mount': 'application.execution_management', 'service_id': grant['service_id'],
                'instance_id': grant['instance_id'], 'scope_ref': grant['scope_ref'], 'account_ref': scope.account_ref,
                'transport_ref': 'management-zmq'}],
            'options': {'request_timeout_ms': 1000, 'heartbeat_interval_seconds': 5}}}},
        'communication': {'config_schema_version': 1, 'profiles': {'management-zmq': {
            'backend': 'zmq', 'config_schema_version': 1, 'endpoints': {'rpc_connect': listener['endpoint']},
            'security_profile_ref': 'key'}}}})
    def factory():
        return ServiceClientRuntime(config, identity=app, scope_resolver=lambda _: scope, peer_resolver=lambda _: peer,
            transport_factory=ZmqTransportFactory({'key': ClientCurveKeys(*keys)}, strict_single_connection=True),
            run_id='synthetic-run', requested_operations={'application.execution_management': operations})
    return ThreadedServiceClientRuntime(factory), scope, app, peer


def test_read_only_process_restart_and_denials(tmp_path):
    raw, path, keys = setup(tmp_path)
    proc, ready = launch(path)
    assert ready == 'EXECUTION_MANAGEMENT_LISTENER_READY'
    wrapper = None
    try:
        wrapper, scope, app, peer = runtime(raw, keys)
        binding, = wrapper.start(timeout_seconds=8).bindings
        target = BoundExecutionManagementReadTarget('application.execution_management',
            'service.execution_management_read', raw['grant']['service_id'], raw['grant']['instance_id'],
            raw['grant']['scope_ref'], scope, app, 'synthetic-run', 'management-zmq', 'key',
            raw['listener']['endpoint'], peer, hashlib.sha256(keys[0]).hexdigest(),
            binding.registration_id, binding.service_epoch)
        client = ExecutionManagementReadClient(wrapper, target=target, grant_revision=raw['grant']['revision'])
        assert client.query('absent', query_id='read').status == 'UNKNOWN'
        assert client.query_plan('absent', query_id='plan').status == 'UNKNOWN'
        assert client.query_by_request('old-run', 'absent', query_id='request').status == 'UNKNOWN'
        from bt_api_py.service_runtime import ServiceRuntimeError
        with pytest.raises(ServiceRuntimeError):
            wrapper.call('application.execution_management', 'execution.parent.submit', {})
    finally:
        if wrapper is not None:
            wrapper.close(timeout_seconds=5)
        proc.send_signal(signal.SIGINT)
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=15)
        assert proc.returncode == 0, (stdout, stderr)
        assert 'EXECUTION_MANAGEMENT_STOPPED' in stdout
    marker, = (tmp_path / 'journal').glob('*.owner')
    marker_before = marker.read_bytes()
    db, = (tmp_path / 'journal').glob('*.sqlite3')
    with sqlite3.connect(db) as con:
        counts = tuple(con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in
                       ('execution_parent_records', 'execution_records', 'execution_writer_leases'))
    assert counts == (0, 0, 0)
    proc, ready = launch(path)
    assert ready == 'EXECUTION_MANAGEMENT_LISTENER_READY'
    proc.send_signal(signal.SIGTERM)
    stdout, stderr = proc.communicate(timeout=15)
    assert proc.returncode == 0, (stdout, stderr)
    with sqlite3.connect(db) as con:
        assert tuple(con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in
                     ('execution_parent_records', 'execution_records', 'execution_writer_leases')) == counts
    raw['storage']['storage_id'] = '8fc3b92e-919e-4be5-8781-7d63c94db4e5'
    path.write_text(json.dumps(raw))
    proc, line = launch(path)
    assert line == 'EXECUTION_MANAGEMENT_FAILED'
    proc.communicate(timeout=15)
    assert proc.returncode != 0 and marker.read_bytes() == marker_before


def test_wrong_principal_key_and_scope_cannot_register(tmp_path):
    raw, path, keys = setup(tmp_path)
    proc, ready = launch(path)
    assert ready == 'EXECUTION_MANAGEMENT_LISTENER_READY'
    try:
        wrong_public, wrong_secret = zmq.curve_keypair()
        for options in (
            {'identity': WireIdentity(WireRole.APPLICATION_CLIENT, 'wrong-principal', 'operator-client')},
            {'scope': WireScope('other-domain', account_ref='account-example', strategy_ref='strategy-example')},
            {'keys': (keys[0], wrong_public, wrong_secret)},
            {'operations': OPERATIONS | {'execution.parent.submit'}},
        ):
            options = copy.copy(options)
            client_keys = options.pop('keys', keys)
            wrapper, *_ = runtime(raw, client_keys, **options)
            try:
                with pytest.raises(Exception):
                    wrapper.start(timeout_seconds=5)
            finally:
                wrapper.close(timeout_seconds=5)
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=15)
        assert proc.returncode == 0



def test_existing_synthetic_parent_is_observed_without_mutating_source(tmp_path):
    from dataclasses import replace
    from bt_api_execution.account_journal_host import AccountJournalHost
    from bt_api_execution.contracts import ExecutionScope
    from bt_api_py.execution_parent_grant import SUBMIT
    from test_iter65_atomic_submission_intake import AtomicSubmissionIntake, PrototypeHarness, planned
    from test_iter65_fresh_submission_intake import KEY, Rig

    raw, path, keys = setup(tmp_path)
    seed = tmp_path.resolve() / 'seed'; seed.mkdir(mode=0o700)
    target = ExecutionScope('ctp', 'simnow', 'account-a', 'strategy-a')
    rig = Rig(seed, target=target)
    rig.intake.close()
    original = rig.parent
    rig.parent = lambda **kw: planned(original(**kw))
    rig.host = AccountJournalHost(root=raw['storage']['journal_root'], scope=target,
                                  storage_id=raw['storage']['storage_id'])
    lease = rig.host.acquire_intake_lease(target, 'synthetic-test', ttl_ns=300_000_000_000)
    rig.intake = PrototypeHarness(AtomicSubmissionIntake(host=rig.host, targets={rig.scope: target}, lease=lease),
                                  rig.registry, rig.scope)
    parent = rig.parent()
    assert rig.call(SUBMIT, parent)['ok']
    wire = replace(rig.scope, run_id=None)
    rig.host.close()
    rig.registry.close()
    raw['execution_scope'].update(provider=target.provider, environment=target.environment,
                                  account_ref=target.account_ref, strategy_id=target.strategy_id)
    raw['wire_scope'].update(domain=wire.execution_domain_ref, account_ref=wire.account_ref,
                             strategy_ref=wire.strategy_ref)
    raw['grant']['instruments'] = [KEY.to_dict()]
    path.write_text(json.dumps(raw))
    database, = Path(raw['storage']['journal_root']).glob('*.sqlite3')
    def rows():
        with sqlite3.connect(database) as db:
            return tuple(db.execute(f'SELECT * FROM {name}').fetchall() for name in
                         ('execution_parent_records', 'execution_records', 'execution_writer_leases'))
    before = rows()
    assert len(before[0]) == 1 and len(before[2]) == 1
    proc, ready = launch(path)
    assert ready == 'EXECUTION_MANAGEMENT_LISTENER_READY'
    wrapper = None
    try:
        wrapper, scope, app, peer = runtime(raw, keys, scope=wire)
        binding, = wrapper.start(timeout_seconds=8).bindings
        target_binding = BoundExecutionManagementReadTarget('application.execution_management',
            'service.execution_management_read', raw['grant']['service_id'], raw['grant']['instance_id'],
            raw['grant']['scope_ref'], scope, app, 'synthetic-run', 'management-zmq', 'key',
            raw['listener']['endpoint'], peer, hashlib.sha256(keys[0]).hexdigest(),
            binding.registration_id, binding.service_epoch)
        client = ExecutionManagementReadClient(wrapper, target=target_binding, grant_revision=raw['grant']['revision'])
        observation = client.query(parent['spec']['parent_ref'], query_id='observed')
        assert observation.status == 'OBSERVED' and observation.snapshot.parent_ref == parent['spec']['parent_ref']
    finally:
        if wrapper is not None:
            wrapper.close(timeout_seconds=5)
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=15)
        assert proc.returncode == 0, (stdout, stderr)
    assert rows() == before


@pytest.mark.parametrize('start_fails', [False, True])
def test_cleanup_unknown_keeps_process_until_signal_retry(start_fails):
    code = '''
import bt_api_py.execution_read_service_host as module
class Host:
    def __init__(self, config): self.closes = 0
    def start(self):
        if START_FAILS: raise module.ReadServiceError("CLEANUP_UNKNOWN")
    def poll(self): pass
    def close(self):
        self.closes += 1
        if self.closes < 2: raise module.ReadServiceError("CLEANUP_UNKNOWN")
module.ReadServiceHost = Host
module.load_config = lambda path: object()
raise SystemExit(module.main(["run", "--config", "unused"]))
'''
    code = code.replace('START_FAILS', repr(start_fails))
    proc = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([proc.stdout], [], [], 10)[0]
        first = proc.stdout.readline().strip()
        if not start_fails:
            assert first == 'EXECUTION_MANAGEMENT_LISTENER_READY'
            proc.send_signal(signal.SIGTERM)
            assert select.select([proc.stdout], [], [], 10)[0]
            first = proc.stdout.readline().strip()
        assert first == 'EXECUTION_MANAGEMENT_CLEANUP_PENDING'
        assert proc.poll() is None
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=10)
        assert proc.returncode == 1, (stdout, stderr)
        assert 'EXECUTION_MANAGEMENT_FAILED' in stdout
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=10)


def test_occupied_port_cleans_observer_and_can_reopen(tmp_path, monkeypatch):
    from bt_api_py import execution_read_service_host as module
    raw, _, _ = setup(tmp_path)
    captured = []
    original = module.ExecutionManagementReadHost
    def capture(selection):
        listener = original(selection)
        captured.append((listener, selection.host))
        return listener
    monkeypatch.setattr(module, 'ExecutionManagementReadHost', capture)
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        raw['listener']['endpoint'] = f'tcp://127.0.0.1:{occupied.getsockname()[1]}'
        host = module.ReadServiceHost(module.parse_config(raw))
        with pytest.raises(module.ReadServiceError, match='START_FAILED'):
            host.start()
        listener, journal = captured[0]
        assert listener.status().outcome == 'FAILED'
        assert listener.status().cleanup == 'CONFIRMED'
        assert not journal._observer_holds
        assert host.state == 'closed' and host.journal is None
    restarted = module.ReadServiceHost(module.parse_config(raw))
    restarted.start()
    try:
        assert restarted.state == 'running'
    finally:
        restarted.close()
    assert restarted.state == 'closed'
