"""Synthetic local configuration and CURVE listener; no account operations."""
import copy
import json
import queue
import socket
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_transport_zmq.server import CurveClientCredentials
from bt_api_transport_zmq.service_v2 import ZmqServiceClient
from bt_api_py.execution_parent_grant import OPERATIONS, PORT
from bt_api_py.execution_parent_service_host import ParentServiceError, ParentServiceHost, load_config, parse_config
from tests.unit.test_iter65_fresh_intake_host import KEY
from tests.unit.test_service_host import _request, _service_config


def setup_config(tmp_path):
    root = tmp_path.resolve()
    journal = root / 'journal'
    registry = root / 'registry'
    journal.mkdir(mode=0o700)
    registry.mkdir(mode=0o700)
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    paths = []
    for name, data in [('server.pub', server_public), ('server.key', server_secret), ('client.pub', client_public)]:
        path = root / name
        path.write_bytes(data)
        path.chmod(0o600)
        paths.append(str(path))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    endpoint = f'tcp://127.0.0.1:{port}'
    service = _service_config({'execution': {'config_schema_version': 2, 'settings': {
        'instance_id': 'exec-a', 'bind_ports': [PORT], 'options': {'role': 'atomic_intake'}}}},
        service_id='execution-service', listener_endpoint=endpoint).to_public_dict()
    key = KEY.to_dict()
    raw = {'schema': 'bt_api_execution.parent_service.v1', 'service': service,
        'service_identity': {'role': 'service', 'principal_ref': 'service-principal', 'actor_ref': 'execution-service'},
        'authority_versions': {'scope': 'v1', 'security': 'v1', 'registration_policy': 'v1', 'requirements': 'v1'},
        'execution_scope': {'provider': 'ctp', 'environment': 'simnow', 'account_ref': 'account-a', 'strategy_id': 'strategy-a'},
        'wire_scope': {'execution_domain_ref': 'paper', 'account_ref': 'account-a', 'strategy_ref': 'strategy-a', 'run_id': 'run-a'},
        'storage': {'journal_root': str(journal), 'registry_path': str(registry / 'registry.sqlite'), 'storage_id': str(uuid.uuid4())},
        'server_keys': {'public_key_file': paths[0], 'secret_key_file': paths[1]},
        'strategy_grants': [{'identity': {'role': 'strategy_client', 'principal_ref': 'strategy-principal', 'actor_ref': 'strategy-a'},
            'scope_ref': 'scope-a', 'revision': 'v1', 'instruments': [key], 'operations': sorted(OPERATIONS), 'public_key_file': paths[2]}],
        'action_grant': {'revision': 'v1', 'instruments': [key], 'plan_versions': ['v1'], 'actions': ['accept', 'query']},
        'schedule_build_id': 'build-a', 'algorithm_policies': [{'plan_version': 'v1', 'schedules': [
            {'name': 'PASS', 'version': 1, 'params': {}, 'max_retry': 0}]}],
        'lease': {'owner': 'owner-a', 'ttl_ns': 300_000_000_000}}
    return raw, endpoint, (server_public, client_public, client_secret)


def test_unready_curve_listener_rejects_registration_without_parent_write(tmp_path):
    raw, endpoint, keys = setup_config(tmp_path)
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    path.chmod(0o600)
    config = load_config(str(path.resolve()))
    ready, failures, done = queue.Queue(), queue.Queue(), threading.Event()
    def run():
        host = ParentServiceHost(config)
        try:
            host.start()
            ready.put((host.host.healthy, host.host._registry.ready))
            while not done.is_set():
                host.poll()
        except BaseException as error:
            failures.put(error)
            ready.put(None)
        finally:
            host.close()
    thread = threading.Thread(target=run)
    thread.start()
    client = None
    try:
        assert ready.get(timeout=10) == (False, False)
        peer = WireIdentity(WireRole.SERVICE, 'service-principal', 'execution-service')
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-principal', 'strategy-a')
        scope = WireScope('paper', account_ref='account-a', strategy_ref='strategy-a', run_id='run-a')
        client = ZmqServiceClient(endpoint, peer, credentials=CurveClientCredentials(*keys))
        discover = client.request(_request('service.discover', {}, identity=identity, scope=scope,
            schema_id='sdk.service.control'), timeout=3).payload
        assert discover['ok'] is True and discover['result']['ready'] is False
        registered = client.request(_request('service.register', {'registration_key': 'registration',
            'client_session_id': 'session', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': sorted(OPERATIONS)}, identity=identity, scope=scope,
            schema_id='sdk.service.control'), timeout=3).payload
        assert registered['ok'] is False
        denied = client.request(_request('execution.parent.submit', {'registration_id': 'missing',
            'service_epoch': 'missing', 'instance_id': 'exec-a', 'arguments': {}},
            identity=identity, scope=scope, schema_id='sdk.service.call'), timeout=3).payload
        assert denied['ok'] is False
        wrong_public, wrong_secret = zmq.curve_keypair()
        wrong = ZmqServiceClient(endpoint, peer, credentials=CurveClientCredentials(
            keys[0], wrong_public, wrong_secret))
        try:
            with pytest.raises(TimeoutError):
                wrong.request(_request('service.discover', {}, identity=identity, scope=scope,
                                       schema_id='sdk.service.control'), timeout=0.2)
        finally:
            wrong.close()
    finally:
        if client is not None:
            client.close()
        done.set()
        thread.join(10)
    assert not thread.is_alive() and failures.empty()
    database, = (tmp_path / 'journal').glob('*.sqlite3')
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT count(*) FROM execution_parent_records').fetchone() == (0,)


@pytest.mark.parametrize('mutation', [
    lambda raw: raw.update(unexpected=True),
    lambda raw: raw['wire_scope'].update(account_ref='wrong'),
    lambda raw: raw['strategy_grants'].append(copy.deepcopy(raw['strategy_grants'][0])),
    lambda raw: raw['server_keys'].update(secret_key_file=raw['server_keys']['public_key_file']),
    lambda raw: raw['algorithm_policies'][0]['schedules'][0].update(name='UNKNOWN'),
])
def test_invalid_config_rejected(tmp_path, mutation):
    raw, _, _ = setup_config(tmp_path)
    mutation(raw)
    with pytest.raises(ParentServiceError, match='INVALID_CONFIGURATION'):
        parse_config(raw)


def test_wrong_server_key_and_unsafe_config_fail_before_start(tmp_path):
    raw, _, _ = setup_config(tmp_path)
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    path.chmod(0o644)
    with pytest.raises(ParentServiceError):
        load_config(str(path.resolve()))
    path.chmod(0o600)
    Path(raw['server_keys']['secret_key_file']).write_bytes(zmq.curve_keypair()[1])
    host = ParentServiceHost(load_config(str(path.resolve())))
    with pytest.raises(ParentServiceError, match='START_FAILED'):
        host.start()
    assert host.state == 'closed'
    assert not list((tmp_path / 'journal').iterdir())


def test_cleanup_failure_keeps_same_host_for_retry(tmp_path):
    raw, _, _ = setup_config(tmp_path)
    parent = ParentServiceHost(parse_config(raw))
    class Fake:
        def __init__(self): self.calls = 0
        def close(self):
            self.calls += 1
            if self.calls == 1: raise RuntimeError('private')
    owned = Fake()
    parent.host, parent.state = owned, 'running'
    with pytest.raises(ParentServiceError, match='CLEANUP_UNKNOWN'):
        parent.close()
    assert parent.host is owned and parent.state == 'close_uncertain'
    parent.close()
    assert owned.calls == 2 and parent.state == 'closed'


def test_readme_sample_parses():
    text = Path('README.md').read_text(encoding='utf-8')
    section = text.split('### Atomic Execution parent listener (currently unready)', 1)[1]
    sample = section.split('```json\n', 1)[1].split('\n```', 1)[0]
    assert parse_config(json.loads(sample)).wire_scope.run_id == 'run-a'


def test_cli_process_reports_unready_and_closes_on_signal(tmp_path):
    import select
    import signal
    import subprocess
    import sys
    raw, _, _ = setup_config(tmp_path)
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    path.chmod(0o600)
    process = subprocess.Popen([sys.executable, '-m', 'bt_api_py.execution_parent_service_host',
                                '--config', str(path.resolve())], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 10)[0]
        assert process.stdout.readline().strip() == 'EXECUTION_PARENT_LISTENER_UNREADY'
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=10)
        assert process.returncode == 0 and out.strip() == 'EXECUTION_PARENT_STOPPED'
        assert not err
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_failed_provider_cleanup_retains_original_journal_for_retry(tmp_path, monkeypatch):
    import bt_api_py.execution_parent_service_host as module
    raw, _, _ = setup_config(tmp_path)
    parent = ParentServiceHost(parse_config(raw))
    original_close = module.AccountJournalHost.close
    calls = []
    def fail_close_twice(journal):
        calls.append(journal)
        if len(calls) <= 2:
            raise RuntimeError('synthetic cleanup failure')
        return original_close(journal)
    def fail_adapter(*_args, **_kwargs):
        raise RuntimeError('synthetic construction failure')
    monkeypatch.setattr(module.AccountJournalHost, 'close', fail_close_twice)
    monkeypatch.setattr(module, 'AtomicIntakeService', fail_adapter)
    with pytest.raises(ParentServiceError, match='START_FAILED'):
        parent.start()
    journal = parent.journal
    assert journal is not None and parent.state == 'close_uncertain'
    assert calls == [journal, journal]
    parent.close()
    assert calls == [journal, journal, journal] and parent.state == 'closed'
