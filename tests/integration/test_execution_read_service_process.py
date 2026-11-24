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
import threading

import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.execution_management_read_client import ExecutionManagementReadClient
from bt_api_py.execution_read_service_host import OPERATIONS
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import BoundExecutionManagementReadTarget, ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples/services/execution-read-service.example.json'
pytestmark = pytest.mark.skipif(not zmq.has('curve'), reason='CURVE unavailable')


def _set_owner_only(path, mode):
    if os.name == 'nt':
        from bt_api_py._win32_security import _set_owner_only_acl
        _set_owner_only_acl(path)
    else:
        path.chmod(mode)


def setup(tmp_path):
    tmp_path = tmp_path.resolve()

    raw = json.loads(EXAMPLE.read_text())
    for name in ('journal', 'audit', 'keys'):
        directory = tmp_path / name
        directory.mkdir(mode=0o700)
        _set_owner_only(directory, 0o700)
    raw['storage'].update(journal_root=str(tmp_path / 'journal'), audit_root=str(tmp_path / 'audit'))
    server_pub, server_sec = zmq.curve_keypair()
    client_pub, client_sec = zmq.curve_keypair()
    for name, value in (('server.public', server_pub), ('server.secret', server_sec), ('client.public', client_pub)):
        path = tmp_path / 'keys' / name
        path.write_bytes(value)
        _set_owner_only(path, 0o600)
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
    ready, received = threading.Event(), []
    reader = threading.Thread(target=lambda: (received.append(process.stdout.readline().strip()), ready.set()),
                              daemon=True)
    reader.start()
    if not ready.wait(20):
        process.kill()
        process.wait(timeout=5)
        ready.wait(2)
        process.communicate(timeout=5)
        raise AssertionError('listener start timed out')
    line = received[0]
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


def _client_for_host(raw, keys, *, operations=OPERATIONS):
    wrapper, scope, app, peer = runtime(raw, keys, operations=operations)
    binding, = wrapper.start(timeout_seconds=8).bindings
    target = BoundExecutionManagementReadTarget('application.execution_management',
        'service.execution_management_read', raw['grant']['service_id'], raw['grant']['instance_id'],
        raw['grant']['scope_ref'], scope, app, 'synthetic-run', 'management-zmq', 'key',
        raw['listener']['endpoint'], peer, hashlib.sha256(keys[0]).hexdigest(),
        binding.registration_id, binding.service_epoch)
    return wrapper, ExecutionManagementReadClient(
        wrapper, target=target, grant_revision=raw['grant']['revision'])


def test_authorized_discovery_audits_empty_then_inserted_refs_and_readback(tmp_path, monkeypatch):
    from bt_api_execution.local_parent_snapshot import LocalParentDiscoverySnapshot, LocalParentSnapshot, LocalParentSnapshotReader
    from bt_api_py.execution_read_service_host import ReadServiceHost, parse_config

    raw, path, keys = setup(tmp_path)
    wrapper = None
    host = ReadServiceHost(parse_config(json.loads(path.read_text())))
    try:
        host.start()
        wrapper, client = _client_for_host(raw, keys)
        empty = client.discover_recent(query_id='discovery-empty')
        assert empty.status == 'OBSERVED'
        assert empty.source == 'LOCAL_JOURNAL' and empty.parent_refs == ()
        assert type(empty.journal_incarnation) is str and len(empty.journal_incarnation) == 32
        with pytest.raises(TypeError):
            client.discover_recent(parent_ref='browser-selected', query_id='forbidden-filter')

        incarnation = 'b' * 32
        authorized_instruments = frozenset({MarketInstrumentKey(
            'example', 'EXAMPLE', 'paper', 'future', 'EXAMPLE:TEST', 'TEST')})
        observations = []

        def discover_recent(reader, *, authorized_instruments):
            observations.append(authorized_instruments)
            return LocalParentDiscoverySnapshot(
                'OBSERVED', journal_incarnation=incarnation, parent_refs=('parent-inserted',))

        def query(reader, parent_ref):
            assert parent_ref == 'parent-inserted'
            return LocalParentSnapshot(
                'OBSERVED', source='LOCAL_JOURNAL', journal_incarnation=incarnation,
                parent_ref=parent_ref, instrument=next(iter(authorized_instruments)),
                side='buy', offset='open', quantity='1', quantity_unit='contracts', limit_price='100',
                time_in_force='DAY', expires_at_ns=1000, created_at_ns=10, updated_at_ns=10,
                intake_local_state='RECEIVED', risk_selected=False, risk_local_state='UNKNOWN',
                gateway_local_state='UNKNOWN', cancel_local_state='UNKNOWN')

        monkeypatch.setattr(LocalParentSnapshotReader, 'discover_recent', discover_recent)
        monkeypatch.setattr(LocalParentSnapshotReader, 'query', query)
        inserted = client.discover_recent(query_id='discovery-inserted')
        assert inserted.status == 'OBSERVED' and inserted.parent_refs == ('parent-inserted',)
        assert inserted.journal_incarnation == incarnation
        readback = client.query('parent-inserted', query_id='readback')
        assert readback.status == 'OBSERVED' and readback.snapshot.parent_ref == 'parent-inserted'
        assert readback.snapshot.journal_incarnation == inserted.journal_incarnation
        assert observations == [authorized_instruments]
    finally:
        if wrapper is not None:
            wrapper.close(timeout_seconds=5)
        host.close()
    audit_db, = (tmp_path / 'audit').glob('*.sqlite3')
    with sqlite3.connect(audit_db) as audit:
        rows = audit.execute('SELECT operation_version,outcome,journal_incarnation,target_digest,result_digest '
                             'FROM audit_attempts ORDER BY sequence').fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        (4, 'OBSERVED'), (4, 'OBSERVED'), (1, 'OBSERVED')]
    assert rows[0][2] == empty.journal_incarnation
    assert rows[1][2] == rows[2][2] == incarnation
    assert all(len(row[3]) == len(row[4]) == 64 for row in rows)


def test_discovery_without_explicit_operation_grant_is_unknown_without_audit_attempt(tmp_path):
    from bt_api_py.execution_read_service_host import ReadServiceHost, parse_config

    raw, path, keys = setup(tmp_path)
    raw['grant']['operations'] = [operation for operation in raw['grant']['operations']
                                  if operation != 'execution.management.parent.discover_recent.v1']
    path.write_text(json.dumps(raw))
    wrapper = None
    host = ReadServiceHost(parse_config(raw))
    try:
        host.start()
        granted = frozenset(raw['grant']['operations'])
        wrapper, client = _client_for_host(raw, keys, operations=granted)
        observation = client.discover_recent(query_id='discovery-denied')
        assert observation.status == 'UNKNOWN'
        assert observation.parent_refs == () and observation.journal_incarnation is None
    finally:
        if wrapper is not None:
            wrapper.close(timeout_seconds=5)
        host.close()
    audit_db, = (tmp_path / 'audit').glob('*.sqlite3')
    with sqlite3.connect(audit_db) as audit:
        assert audit.execute('SELECT count(*) FROM audit_attempts').fetchone()[0] == 0


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
