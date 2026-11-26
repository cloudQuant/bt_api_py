"""Synthetic local configuration and CURVE listener; no account operations."""
import copy
import json
import os
import queue
import socket
import sqlite3
import tempfile
import threading
import uuid
from pathlib import Path

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_transport_zmq.server import CurveClientCredentials
from bt_api_transport_zmq.service_v2 import ZmqServiceClient
from bt_api_py.execution_parent_grant import OPERATIONS, PORT
from bt_api_py.execution_parent_service_host import (
    ParentObservationHost, ParentServiceError, ParentServiceHost, load_config, parse_config,
)
from tests.unit.test_iter65_fresh_intake_host import KEY
from tests.unit.test_service_host import _request, _service_config


def _private_file(path):
    """chmod(0o600/0o700) is a no-op for DACLs on Windows; set owner-only ACL."""
    if os.name == "nt":
        from bt_api_py._win32_security import _set_owner_only_acl

        _set_owner_only_acl(path)
    else:
        path.chmod(0o700 if path.is_dir() else 0o600)


def setup_config(tmp_path):
    root = tmp_path.resolve()
    journal = root / 'journal'
    registry = root / 'registry'
    journal.mkdir(mode=0o700)
    registry.mkdir(mode=0o700)
    _private_file(journal)
    _private_file(registry)
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    paths = []
    for name, data in [('server.pub', server_public), ('server.key', server_secret), ('client.pub', client_public)]:
        path = root / name
        path.write_bytes(data)
        _private_file(path)
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
    _private_file(path)
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
    if os.name != "nt":
        # On Windows chmod(0o644) does not loosen the DACL, so the
        # "insecure config is rejected" precondition cannot be constructed
        # this way; POSIX keeps the original check unchanged.
        path.chmod(0o644)
        with pytest.raises(ParentServiceError):
            load_config(str(path.resolve()))
    _private_file(path)
    Path(raw['server_keys']['secret_key_file']).write_bytes(zmq.curve_keypair()[1])
    host = ParentServiceHost(load_config(str(path.resolve())))
    with pytest.raises(ParentServiceError, match='START_FAILED'):
        host.start()
    assert host.state == 'closed'
    assert not list((tmp_path / 'journal').iterdir())


def test_observe_only_requires_existing_and_preserves_journal_rows(tmp_path):
    raw, _, _ = setup_config(tmp_path)
    config = parse_config(raw)
    root = tmp_path / 'journal'
    observer = ParentObservationHost(config)
    with pytest.raises(ParentServiceError, match='START_FAILED'):
        observer.start()
    assert observer.state == 'closed' and not list(root.iterdir())

    owner = ParentServiceHost(config)
    owner.start()
    owner.close()
    database, = root.glob('*.sqlite3')
    marker, = root.glob('*.owner')

    def rows():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            return (
                tuple(db.execute('SELECT * FROM execution_writer_leases ORDER BY scope_key')),
                tuple(db.execute('SELECT * FROM execution_parent_records')),
            )

    marker_before, rows_before = marker.read_bytes(), rows()
    observer = ParentObservationHost(config)
    observer.start()
    try:
        assert observer.poll() == 'OBSERVE_ONLY'
    finally:
        observer.close()
    assert observer.state == 'closed'
    assert marker.read_bytes() == marker_before and rows() == rows_before


def test_observe_only_rejects_dangling_owner_manifest(tmp_path):
    raw, _, _ = setup_config(tmp_path)
    config = parse_config(raw)
    owner = ParentServiceHost(config)
    owner.start()
    owner.close()
    marker, = (tmp_path / 'journal').glob('*.owner')
    marker.unlink()
    if os.name == 'nt':
        import subprocess

        target = tmp_path / 'owner-junction-target'
        target.mkdir()
        result = subprocess.run(
            ['cmd', '/c', 'mklink', '/J', os.fspath(marker), os.fspath(target)],
            capture_output=True,
        )
        if result.returncode != 0 or not os.path.lexists(marker):
            pytest.skip('junction creation unavailable on this host')
        target.rmdir()
    else:
        marker.symlink_to(tmp_path / 'missing-owner-manifest')
    try:
        observer = ParentObservationHost(config)
        with pytest.raises(ParentServiceError, match='START_FAILED'):
            observer.start()
        assert observer.state == 'closed'
    finally:
        if os.name == 'nt' and os.path.lexists(marker):
            os.rmdir(marker)


def test_observe_only_rejects_directory_in_place_of_host_guard(tmp_path):
    raw, _, _ = setup_config(tmp_path)
    config = parse_config(raw)
    owner = ParentServiceHost(config)
    owner.start()
    owner.close()
    guard = tmp_path / 'journal' / '.execution-journal-host.guard'
    guard.unlink()
    guard.mkdir()
    _private_file(guard)
    observer = ParentObservationHost(config)
    with pytest.raises(ParentServiceError, match='START_FAILED'):
        observer.start()
    assert observer.state == 'closed'


def test_observe_only_cli_paces_storage_checks(monkeypatch, capsys):
    import bt_api_py.execution_parent_service_host as module

    calls = []

    class Host:
        def start(self):
            calls.append('start')

        def poll(self):
            calls.append('poll')
            return 'OBSERVE_ONLY'

        def close(self):
            calls.append('close')

    host = Host()
    checks = iter((False, True))
    monkeypatch.setattr(module, 'load_config', lambda _path: object())
    monkeypatch.setattr(module, 'ParentObservationHost', lambda _config: host)
    monkeypatch.setattr(module, 'marker_requested', lambda _path: next(checks))
    monkeypatch.setattr(module.signal, 'signal', lambda _sig, _handler: None)
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: calls.append(('sleep', seconds)))
    assert module.main(['--config', 'not-read.json', '--observe-only']) == 0
    assert calls == ['start', 'poll', ('sleep', 0.5), 'close']
    assert capsys.readouterr().out.splitlines() == [
        'EXECUTION_PARENT_CONTROL_MODE observe_only',
        'EXECUTION_PARENT_OBSERVER_READY',
        'EXECUTION_PARENT_STOPPED',
    ]


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
    text = (Path(__file__).resolve().parents[2] / 'README.md').read_text(encoding='utf-8')
    section = text.split('### Atomic Execution parent listener (quarantined by default)', 1)[1]
    sample = section.split('```json\n', 1)[1].split('\n```', 1)[0]
    raw = json.loads(sample)
    if os.name != 'posix':
        # README example paths are POSIX absolute; adapt them to this platform
        # so the schema contract (not the OS path spelling) is what is checked.
        root = Path(tempfile.mkdtemp())
        (root / 'journal').mkdir()
        (root / 'registry').mkdir()
        raw['storage']['journal_root'] = str(root / 'journal')
        raw['storage']['registry_path'] = str(root / 'registry' / 'registry.sqlite')
        for key in ('public_key_file', 'secret_key_file'):
            raw['server_keys'][key] = str(root / key)
        for grant in raw['strategy_grants']:
            grant['public_key_file'] = str(root / 'client.pub')
    assert parse_config(raw).wire_scope.run_id == 'run-a'


def test_cli_process_reports_unready_and_closes_on_signal(tmp_path):
    import signal
    import subprocess
    import sys
    if os.name == "nt":
        pytest.skip(
            "POSIX SIGTERM graceful-shutdown semantics are not reproducible "
            "on Windows (send_signal hard-kills; CTRL_BREAK_EVENT exits via "
            "STATUS_CONTROL_C_EXIT). Covered unchanged on POSIX hosts."
        )
    raw, _, _ = setup_config(tmp_path)
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    _private_file(path)
    # On Windows, Popen.send_signal(SIGTERM) hard-kills the child (TerminateProcess),
    # so the CLI's signal handler never runs.  A new process group + CTRL_BREAK_EVENT
    # is delivered as a catchable SIGINT there; POSIX keeps the original SIGTERM.
    popen_kwargs = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen([sys.executable, '-m', 'bt_api_py.execution_parent_service_host',
                                '--config', str(path.resolve())], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, **popen_kwargs)
    try:
        # select() only accepts sockets on Windows; a reader thread with a
        # queue gives the same bounded first-line wait on every platform.
        ready: queue.Queue = queue.Queue(maxsize=1)

        def _read_first_line():
            ready.put(process.stdout.readline())

        threading.Thread(target=_read_first_line, daemon=True).start()
        try:
            first_line = ready.get(timeout=10).strip()
        except queue.Empty as exc:
            raise AssertionError("execution parent host did not report startup") from exc
        assert first_line == 'EXECUTION_PARENT_LISTENER_UNREADY'
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
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


def test_closed_journal_reopen_fails_without_changing_original_rows(tmp_path):
    raw, _, _ = setup_config(tmp_path)
    config = parse_config(raw)
    first = ParentServiceHost(config)
    first.start()
    assert first.host.healthy is False
    first.close()
    journal_root = tmp_path / 'journal'
    database, = journal_root.glob('*.sqlite3')
    marker, = journal_root.glob('*.owner')
    def observed():
        with sqlite3.connect(database) as db:
            return (db.execute('SELECT * FROM execution_writer_leases').fetchall(),
                    db.execute('SELECT * FROM execution_parent_records').fetchall())
    marker_before = marker.read_bytes()
    before = observed()
    assert len(before[0]) == 1 and before[1] == []
    reopened = ParentServiceHost(config)
    with pytest.raises(ParentServiceError, match='START_FAILED'):
        reopened.start()
    assert reopened.state == 'closed' and reopened.host is None and reopened.journal is None
    assert marker.read_bytes() == marker_before and observed() == before
