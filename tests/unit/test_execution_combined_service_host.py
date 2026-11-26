"""Synthetic local CURVE listeners and journals; no account or provider calls."""
import copy
import hashlib
import json
import os
import queue
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.frozen_algorithm_plan import algorithm_plan_digest
from bt_api_transport_zmq.server import CurveClientCredentials
from bt_api_transport_zmq.service_v2 import ZmqServiceClient

from bt_api_py import execution_combined_service_host as combined
from bt_api_py.execution_management_read_client import ExecutionManagementReadClient
from bt_api_py.execution_parent_grant import OPERATIONS, PORT, QUERY, SUBMIT
from bt_api_py.execution_parent_service_host import ParentServiceHost, parse_config as parse_parent
from bt_api_py.execution_read_service_host import ReadServiceError, ReadServiceHost, parse_config as parse_read
from bt_api_py.service_runtime import BoundExecutionManagementReadTarget, ServiceRuntimeError
from tests.integration.test_execution_read_service_process import setup as setup_read, runtime
from tests.unit.test_iter65_execution_parent_service_host import setup_config as setup_parent
from tests.unit.test_iter65_fresh_intake_host import KEY, parent_payload
from tests.unit.test_service_host import _request


def _owner_only_fixture_tree(root):
    """chmod does not set Windows DACLs on the synthetic Read fixtures."""
    if os.name == 'nt':
        from bt_api_py._win32_security import _set_owner_only_acl

        for path in (root, *root.rglob('*')):
            _set_owner_only_acl(path)


def setup_combined(tmp_path):
    parent_dir, read_dir = tmp_path / 'parent', tmp_path / 'read'
    parent_dir.mkdir(mode=0o700); read_dir.mkdir(mode=0o700)
    parent, parent_endpoint, parent_keys = setup_parent(parent_dir)
    read, _, read_keys = setup_read(read_dir)
    _owner_only_fixture_tree(read_dir)
    read['storage'].update(journal_root=parent['storage']['journal_root'], storage_id=parent['storage']['storage_id'])
    read['execution_scope'] = copy.deepcopy(parent['execution_scope'])
    read['wire_scope'] = {'domain': 'paper', 'account_ref': 'account-a', 'strategy_ref': 'strategy-a', 'run_id': None}
    read['grant']['instruments'] = [KEY.to_dict()]
    raw = {'schema': combined.SCHEMA, 'parent': parent, 'management': read}
    return raw, parent_endpoint, parent_keys, read_keys


def seed(raw):
    original = ParentServiceHost(parse_parent(raw['parent']))
    original.start()
    assert not original.host.healthy
    original.close()
    raw['parent']['storage'].update(writer_mode='single_host_cooperative', require_existing=True)


def management_client(raw, keys):
    wire = WireScope('paper', account_ref='account-a', strategy_ref='strategy-a')
    wrapper, _, app, peer = runtime(raw, keys, scope=wire)
    binding, = wrapper.start(timeout_seconds=8).bindings
    grant = raw['grant']
    target = BoundExecutionManagementReadTarget('application.execution_management',
        'service.execution_management_read', grant['service_id'], grant['instance_id'], grant['scope_ref'],
        wire, app, 'synthetic-run', 'management-zmq', 'key', raw['listener']['endpoint'], peer,
        hashlib.sha256(keys[0]).hexdigest(), binding.registration_id, binding.service_epoch)
    return wrapper, ExecutionManagementReadClient(wrapper, target=target, grant_revision=grant['revision'])


def bootstrap_management_read(raw, keys):
    read = ReadServiceHost(parse_read(raw['management']))
    read.start()
    wrapper = None
    try:
        incarnation = read.journal._manifest['incarnation']
        wrapper, client = management_client(raw['management'], keys)
        assert client.query('parent-a', query_id='pre-claim-read').status == 'UNKNOWN'
    finally:
        if wrapper is not None:
            wrapper.close(timeout_seconds=5)
        read.close()
    audit = Path(raw['management']['storage']['audit_root']) / 'execution-management-read.sqlite3'
    with sqlite3.connect(audit) as db:
        assert db.execute('SELECT count(*) FROM audit_attempts').fetchone()[0] >= 1
    raw['parent']['storage'].update(writer_mode='single_host_cooperative', require_existing=True)
    raw['parent']['lease']['initial_claim'] = {
        'claim_id': str(uuid.uuid4()), 'expected_incarnation': incarnation,
        'history_assertion': 'management_read_only_no_prior_intake'}


@contextmanager
def running_combined(raw):
    ready, failures, done = queue.Queue(), queue.Queue(), threading.Event()
    host = combined.CombinedServiceHost(combined.parse_config(raw))
    def serve():
        try:
            host.start()
            ready.put(host.poll())
            while not done.is_set():
                host.poll()
        except BaseException as error:
            failures.put(error); ready.put(None)
        finally:
            try: host.close()
            except BaseException as error: failures.put(error)
    thread = threading.Thread(target=serve)
    thread.start()
    try:
        assert ready.get(timeout=15) == {'parent': 'READY', 'management': 'READY'}
        yield host
    finally:
        done.set(); thread.join(15)
        assert not thread.is_alive() and failures.empty()


def parent_submit_and_query(endpoint, keys, payload, *, submit=True):
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-principal', 'strategy-a')
    peer = WireIdentity(WireRole.SERVICE, 'service-principal', 'execution-service')
    scope = WireScope('paper', account_ref='account-a', strategy_ref='strategy-a', run_id='run-a')
    client = ZmqServiceClient(endpoint, peer, credentials=CurveClientCredentials(*keys))
    try:
        def request(operation, args, schema):
            return client.request(_request(operation, args, identity=identity, scope=scope, schema_id=schema),
                                  timeout=3).payload
        registered = request('service.register', {'registration_key': str(uuid.uuid4()),
            'client_session_id': str(uuid.uuid4()), 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': sorted(OPERATIONS)}, 'sdk.service.control')
        assert registered['ok'], registered
        binding = registered['result']
        def invoke(operation, args):
            return request(operation, {'registration_id': binding['registration_id'],
                'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': args},
                'sdk.service.call')
        if submit:
            submitted = invoke(SUBMIT, payload)
            assert submitted['ok'] and submitted['result']['local_only'] is True
            assert submitted['result']['status'] == 'RECEIVED'
        queried = invoke(QUERY, {'request_id': payload['request_id']})
        assert queried['ok'] and queried['result']['found'] is True
        assert queried['result']['local_only'] is True and queried['result']['status'] == 'RECEIVED'
    finally:
        client.close()


def planned_parent():
    payload = parent_payload()
    spec = payload['spec']; spec['execution_domain_ref'] = 'paper'
    payload['algorithm_plan'] = {'schema_version': 1, 'plan_version': 'v1', 'build_id': 'build-a',
        'schedule': {'class': 'PASS', 'version': 1, 'binding': {'deadline_ns': spec['expires_at_ns'],
            'quantity_step': '1', 'parent_limit': spec['limit_price'], 'price_step': '1', 'retry_limit': 0},
            'params': {}}}
    spec['plan_digest'] = algorithm_plan_digest(payload['algorithm_plan'])
    return payload


@pytest.mark.parametrize('change', [
    lambda raw: raw['management']['storage'].update(storage_id='8fc3b92e-919e-4be5-8781-7d63c94db4e5'),
    lambda raw: raw['management']['storage'].update(journal_root='/different/root'),
    lambda raw: raw['management']['execution_scope'].update(strategy_id='other'),
    lambda raw: raw['management']['wire_scope'].update(domain='other'),
    lambda raw: raw['management']['listener'].update(endpoint=raw['parent']['service']['communication']['profiles']['listener-v1']['endpoints']['rpc_bind']),
    lambda raw: raw['management']['storage'].update(audit_root=str(Path(raw['parent']['storage']['registry_path']).parent)),
])
def test_config_mismatch_rejected(tmp_path, change):
    raw, _, _, _ = setup_combined(tmp_path)
    change(raw)
    with pytest.raises(combined.CombinedServiceError, match='INVALID_CONFIGURATION'):
        combined.parse_config(raw)


@pytest.mark.parametrize('mismatch', ['root', 'storage', 'account', 'strategy', 'owner_type'])
def test_borrow_mismatch_rejected_before_keys_or_audit(tmp_path, monkeypatch, mismatch):
    from bt_api_py import execution_read_service_host as read_module
    raw, _, _, _ = setup_combined(tmp_path)
    parent = ParentServiceHost(parse_parent(raw['parent']))
    parent.start()
    try:
        wrong = parse_read(raw['management'])
        if mismatch == 'root':
            wrong = replace(wrong, journal_root=tmp_path / 'other')
        elif mismatch == 'storage':
            wrong = replace(wrong, storage_id='8fc3b92e-919e-4be5-8781-7d63c94db4e5')
        elif mismatch == 'account':
            wrong = replace(wrong, local_scope=replace(wrong.local_scope, account_ref='other'))
        elif mismatch == 'strategy':
            wrong = replace(wrong, local_scope=replace(wrong.local_scope, strategy_id='other'))
        keys_called = []
        monkeypatch.setattr(read_module, '_keys', lambda _: keys_called.append(True))
        audit = wrong.audit_root
        read = ReadServiceHost(wrong, borrowed_from=object() if mismatch == 'owner_type' else parent)
        with pytest.raises(ReadServiceError, match='INVALID_BORROWED_JOURNAL'):
            read.start()
        assert not keys_called and not list(audit.iterdir())
        assert parent.journal is not None and parent.state == 'running'
    finally:
        parent.close()


def test_default_quarantined_parent_keeps_independent_readiness(tmp_path):
    raw, _, _, _ = setup_combined(tmp_path)
    host = combined.CombinedServiceHost(combined.parse_config(raw))
    host.start()
    try:
        assert host.poll() == {'parent': 'UNREADY', 'management': 'READY'}
        assert host.parent.journal is host.management.journal
    finally:
        host.close()


def test_observe_only_starts_existing_management_without_parent_intake(tmp_path, monkeypatch):
    raw, _, _, _ = setup_combined(tmp_path)
    config = combined.parse_config(raw)
    calls = []

    class Parent:
        def __init__(self, _config):
            raise AssertionError('observe-only constructed the parent intake')

    class Read:
        def __init__(self, read_config, *, borrowed_from=None, require_existing=False):
            assert read_config is config.management
            assert borrowed_from is None and require_existing is True
            calls.append('management-created')

        def start(self):
            calls.append('management-started')

        def poll(self):
            calls.append('management-polled')

        def close(self):
            calls.append('management-closed')

    monkeypatch.setattr(combined, 'ParentServiceHost', Parent)
    monkeypatch.setattr(combined, 'ReadServiceHost', Read)
    host = combined.CombinedServiceHost(config, observe_only=True)
    host.start()
    try:
        assert host.poll() == {'parent': 'OBSERVE_ONLY', 'management': 'READY'}
        assert host.parent is None and host.gateway_source is None
    finally:
        host.close()
    assert calls == [
        'management-created', 'management-started', 'management-polled', 'management-closed']


def test_observe_only_preserves_existing_journal_rows_and_intake_lease(tmp_path):
    raw, endpoint, parent_keys, _ = setup_combined(tmp_path)
    seed(raw)
    payload = planned_parent()
    with running_combined(raw):
        parent_submit_and_query(endpoint, parent_keys, payload)

    database, = Path(raw['parent']['storage']['journal_root']).glob('*.sqlite3')

    def rows():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            return {
                'execution_parent_records': tuple(db.execute(
                    'SELECT * FROM execution_parent_records')),
                'execution_writer_leases': tuple(db.execute(
                    'SELECT * FROM execution_writer_leases ORDER BY scope_key')),
            }

    before = rows()
    assert before['execution_parent_records']
    assert before['execution_writer_leases']
    host = combined.CombinedServiceHost(combined.parse_config(raw), observe_only=True)
    host.start()
    try:
        assert host.poll() == {'parent': 'OBSERVE_ONLY', 'management': 'READY'}
        assert host.parent is None and host.gateway_source is None
    finally:
        host.close()
    assert rows() == before


def test_observe_only_refuses_to_create_a_missing_journal(tmp_path):
    raw, _, _, _ = setup_combined(tmp_path)
    journal_root = Path(raw['parent']['storage']['journal_root'])
    before = tuple(journal_root.iterdir())
    host = combined.CombinedServiceHost(combined.parse_config(raw), observe_only=True)
    with pytest.raises(combined.CombinedServiceError, match='START_FAILED'):
        host.start()
    assert host.state == 'closed'
    assert tuple(journal_root.iterdir()) == before


def test_observe_only_rejects_source_control_before_loading_config(monkeypatch):
    monkeypatch.setattr(combined, 'load_config',
                        lambda _path: (_ for _ in ()).throw(AssertionError('config loaded')))
    with pytest.raises(SystemExit) as error:
        combined.main(['run', '--config', 'not-read.json', '--observe-only',
                       '--source-control-stdin'])
    assert error.value.code == 2


def test_observe_only_cli_paces_management_checks(monkeypatch, capsys):
    calls = []

    class Host:
        def start(self):
            calls.append('start')

        def poll(self):
            calls.append('poll')
            return {'parent': 'OBSERVE_ONLY', 'management': 'READY'}

        def close(self):
            calls.append('close')

    host = Host()
    checks = iter((False, False, True))
    monkeypatch.setattr(combined, 'load_config', lambda _path: object())
    monkeypatch.setattr(combined, 'CombinedServiceHost',
                        lambda _config, *, observe_only: host if observe_only else None)
    monkeypatch.setattr(combined, 'marker_requested', lambda _path: next(checks))
    monkeypatch.setattr(combined.signal, 'signal', lambda _sig, _handler: None)
    monkeypatch.setattr(combined.time, 'sleep',
                        lambda seconds: calls.append(('sleep', seconds)))
    assert combined.main(['run', '--config', 'not-read.json', '--observe-only']) == 0
    assert calls == ['start', 'poll', 'poll', ('sleep', 0.5), 'close']
    assert capsys.readouterr().out.splitlines() == [
        'EXECUTION_COMBINED_CONTROL_MODE observe_only',
        'EXECUTION_COMBINED_PARENT_OBSERVE_ONLY',
        'EXECUTION_COMBINED_MANAGEMENT_READY',
        'EXECUTION_COMBINED_STOPPED',
    ]


def test_poll_checks_both_listeners_after_parent_failure(tmp_path):
    raw, _, _, _ = setup_combined(tmp_path)
    host = combined.CombinedServiceHost(combined.parse_config(raw))
    called = []
    class Parent:
        def poll(self): called.append('parent'); raise RuntimeError
    class Read:
        def poll(self): called.append('read')
    host.parent, host.management, host.state = Parent(), Read(), 'running'
    assert host.poll() == {'parent': 'UNAVAILABLE', 'management': 'READY'}
    assert called == ['parent', 'read']


def test_partial_start_and_close_failure_keep_original_owner(tmp_path, monkeypatch):
    raw, _, _, _ = setup_combined(tmp_path)
    config = combined.parse_config(raw)
    calls = []
    class Parent:
        def __init__(self, _): self.closed = False
        def start(self): calls.append('parent-start')
        def close(self): calls.append('parent-close'); self.closed = True
    class Read:
        def __init__(self, _, *, borrowed_from): self.owner = borrowed_from; self.attempts = 0
        def start(self): calls.append('read-start'); raise RuntimeError
        def close(self):
            calls.append('read-close'); self.attempts += 1
            if self.attempts == 1: raise RuntimeError
    monkeypatch.setattr(combined, 'ParentServiceHost', Parent)
    monkeypatch.setattr(combined, 'ReadServiceHost', Read)
    host = combined.CombinedServiceHost(config)
    with pytest.raises(combined.CombinedServiceError, match='CLEANUP_UNKNOWN'):
        host.start()
    owner, read = host.parent, host.management
    assert read.owner is owner and not owner.closed and host.state == 'close_uncertain'
    assert calls == ['parent-start', 'read-start', 'read-close']
    host.close()
    assert owner.closed and host.state == 'closed'
    assert calls == ['parent-start', 'read-start', 'read-close', 'read-close', 'parent-close']


@pytest.mark.skipif(os.name == 'nt', reason='Windows SIGTERM terminates instead of cooperative shutdown')
def test_cli_validate_and_signal_shutdown(tmp_path):
    raw, _, _, _ = setup_combined(tmp_path)
    seed(raw)
    path = tmp_path / 'combined.json'
    path.write_text(json.dumps(raw), encoding='utf-8'); path.chmod(0o600)
    _owner_only_fixture_tree(path)
    command = [sys.executable, '-m', 'bt_api_py.execution_combined_service_host']
    validation = subprocess.run([*command, 'validate', '--config', str(path)], capture_output=True, text=True, timeout=10)
    assert validation.returncode == 0 and validation.stdout.strip() == 'EXECUTION_COMBINED_CONFIG_VALID'
    process = subprocess.Popen([*command, 'run', '--config', str(path)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    ready = queue.Queue()

    def read_ready():
        for _ in range(2):
            ready.put(process.stdout.readline().strip())

    reader = threading.Thread(target=read_ready, daemon=True)
    reader.start()
    try:
        assert ready.get(timeout=15) == 'EXECUTION_COMBINED_PARENT_READY'
        assert ready.get(timeout=15) == 'EXECUTION_COMBINED_MANAGEMENT_READY'
        process.send_signal(signal.SIGTERM)
        output, error = process.communicate(timeout=15)
        reader.join(timeout=5)
        assert not reader.is_alive()
        assert process.returncode == 0 and output.strip() == 'EXECUTION_COMBINED_STOPPED' and not error
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=5)


def test_management_bootstrap_claim_atomic_read_and_resume(tmp_path, monkeypatch):
    from bt_api_execution import atomic_submission_intake as atomic

    raw, endpoint, parent_keys, read_keys = setup_combined(tmp_path)
    bootstrap_management_read(raw, read_keys)
    payload = planned_parent()
    entered, release = threading.Event(), threading.Event()
    original_transaction = atomic._nonblocking_store_transaction

    @contextmanager
    def paused_transaction(store, *, write):
        with original_transaction(store, write=write) as cursor:
            yield cursor
            if write and not entered.is_set():
                count = cursor.execute('SELECT count(*) FROM execution_parent_records').fetchone()[0]
                indexes = cursor.execute("SELECT count(*) FROM execution_meta "
                    "WHERE key LIKE 'execution_parent_intake.v2.%'").fetchone()[0]
                if count == 1 and indexes >= 1:
                    entered.set()
                    assert release.wait(1.5), 'bounded transaction barrier timed out'
    monkeypatch.setattr(atomic, '_nonblocking_store_transaction', paused_transaction)
    with running_combined(raw) as host:
        assert host.parent.journal is host.management.journal
        wrapper, management = management_client(raw['management'], read_keys)
        errors = queue.Queue()
        def submit():
            try:
                parent_submit_and_query(endpoint, parent_keys, payload)
            except BaseException as error:
                errors.put(error)
        thread = threading.Thread(target=submit)
        try:
            try:
                thread.start()
                assert entered.wait(3), 'submit never reached the uncommitted parent/index barrier'
                database, = Path(raw['parent']['storage']['journal_root']).glob('*.sqlite3')
                with sqlite3.connect(database) as db:
                    assert db.execute('SELECT count(*) FROM execution_parent_records').fetchone() == (0,)
                    assert db.execute("SELECT count(*) FROM execution_meta "
                        "WHERE key LIKE 'execution_parent_intake.v2.%'").fetchone() == (0,)
                assert management.query(payload['spec']['parent_ref'], query_id='during-transaction').status == 'UNKNOWN'
            finally:
                release.set(); thread.join(5)
            assert not thread.is_alive() and errors.empty()
            observed = management.query(payload['spec']['parent_ref'], query_id='after-commit')
            assert observed.status == 'OBSERVED' and observed.snapshot.intake_local_state == 'RECEIVED'
        finally:
            wrapper.close(timeout_seconds=5)
    del raw['parent']['lease']['initial_claim']
    with running_combined(raw):
        parent_submit_and_query(endpoint, parent_keys, payload, submit=False)
        wrapper, management = management_client(raw['management'], read_keys)
        try:
            observed = management.query(payload['spec']['parent_ref'], query_id='after-resume')
            assert observed.status == 'OBSERVED' and observed.snapshot.parent_ref == payload['spec']['parent_ref']
        finally:
            wrapper.close(timeout_seconds=5)
    database, = Path(raw['parent']['storage']['journal_root']).glob('*.sqlite3')
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT count(*) FROM execution_parent_records').fetchone() == (1,)
        assert db.execute('SELECT fencing_token FROM execution_writer_leases').fetchone() == (2,)
    audit = Path(raw['management']['storage']['audit_root']) / 'execution-management-read.sqlite3'
    with sqlite3.connect(audit) as db:
        assert db.execute('SELECT count(*) FROM audit_attempts').fetchone()[0] >= 4


def test_claim_survives_management_start_failure_and_requires_explicit_resume(tmp_path):
    raw, _, _, read_keys = setup_combined(tmp_path)
    bootstrap_management_read(raw, read_keys)
    config = combined.parse_config(raw)
    with socket.socket() as occupied:
        address, port = raw['management']['listener']['endpoint'].removeprefix('tcp://').rsplit(':', 1)
        occupied.bind((address, int(port)))
        occupied.listen()
        failed = combined.CombinedServiceHost(config)
        with pytest.raises(combined.CombinedServiceError, match='START_FAILED'):
            failed.start()
        assert failed.state == 'closed'
    database, = Path(raw['parent']['storage']['journal_root']).glob('*.sqlite3')
    from bt_api_execution.account_journal_host import _INITIAL_INTAKE_CLAIM_KEY
    with sqlite3.connect(database) as db:
        marker = db.execute('SELECT value FROM execution_meta WHERE key=?',
                            (_INITIAL_INTAKE_CLAIM_KEY,)).fetchone()
        lease = db.execute('SELECT fencing_token FROM execution_writer_leases').fetchone()
    assert marker is not None and lease == (1,)
    retry = combined.CombinedServiceHost(config)
    with pytest.raises(combined.CombinedServiceError, match='START_FAILED'):
        retry.start()
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT value FROM execution_meta WHERE key=?',
                          (_INITIAL_INTAKE_CLAIM_KEY,)).fetchone() == marker
        assert db.execute('SELECT fencing_token FROM execution_writer_leases').fetchone() == lease
    del raw['parent']['lease']['initial_claim']
    with running_combined(raw):
        wrapper, management = management_client(raw['management'], read_keys)
        try:
            assert management.query('parent-a', query_id='explicit-resume').status == 'UNKNOWN'
        finally:
            wrapper.close(timeout_seconds=5)
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT value FROM execution_meta WHERE key=?',
                          (_INITIAL_INTAKE_CLAIM_KEY,)).fetchone() == marker
        assert db.execute('SELECT fencing_token FROM execution_writer_leases').fetchone() == (2,)


def test_curve_combined_parent_and_management_share_original_journal(tmp_path):
    raw, endpoint, parent_keys, read_keys = setup_combined(tmp_path)
    seed(raw)
    config = combined.parse_config(raw)
    ready, failures, done = queue.Queue(), queue.Queue(), threading.Event()
    def serve():
        host = combined.CombinedServiceHost(config)
        try:
            host.start()
            ready.put((host.poll(), host.parent.journal is host.management.journal))
            while not done.is_set():
                host.poll()
        except BaseException as error:
            failures.put(error); ready.put(None)
        finally:
            try: host.close()
            except BaseException as error: failures.put(error)
    thread = threading.Thread(target=serve)
    thread.start()
    parent_client = wrapper = None
    try:
        assert ready.get(timeout=15) == ({'parent': 'READY', 'management': 'READY'}, True)
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-principal', 'strategy-a')
        peer = WireIdentity(WireRole.SERVICE, 'service-principal', 'execution-service')
        scope = WireScope('paper', account_ref='account-a', strategy_ref='strategy-a', run_id='run-a')
        parent_client = ZmqServiceClient(endpoint, peer, credentials=CurveClientCredentials(*parent_keys))
        def request(operation, arguments, schema):
            return parent_client.request(_request(operation, arguments, identity=identity, scope=scope,
                                                  schema_id=schema), timeout=3).payload
        registered = request('service.register', {'registration_key': 'combined', 'client_session_id': 'combined',
            'instance_id': 'exec-a', 'port': PORT, 'scope_ref': 'scope-a', 'operations': sorted(OPERATIONS)},
            'sdk.service.control')
        assert registered['ok'], registered
        binding = registered['result']
        def invoke(operation, args):
            return request(operation, {'registration_id': binding['registration_id'],
                'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': args}, 'sdk.service.call')
        payload = parent_payload()
        spec = payload['spec']; spec['execution_domain_ref'] = 'paper'
        payload['algorithm_plan'] = {'schema_version': 1, 'plan_version': 'v1', 'build_id': 'build-a',
            'schedule': {'class': 'PASS', 'version': 1, 'binding': {'deadline_ns': spec['expires_at_ns'],
                'quantity_step': '1', 'parent_limit': spec['limit_price'], 'price_step': '1', 'retry_limit': 0},
                'params': {}}}
        spec['plan_digest'] = algorithm_plan_digest(payload['algorithm_plan'])
        submitted = invoke(SUBMIT, payload)
        assert submitted['ok'] and submitted['result']['local_only'] and submitted['result']['status'] == 'RECEIVED'
        assert invoke(QUERY, {'request_id': payload['request_id']})['result']['found'] is True
        assert not invoke('execution.management.parent.read.v1', {'parent_ref': spec['parent_ref']})['ok']
        wire = replace(scope, run_id=None)
        wrapper, _, app, read_peer = runtime(raw['management'], read_keys, scope=wire)
        read_binding, = wrapper.start(timeout_seconds=8).bindings
        grant = raw['management']['grant']
        target = BoundExecutionManagementReadTarget('application.execution_management',
            'service.execution_management_read', grant['service_id'], grant['instance_id'], grant['scope_ref'],
            wire, app, 'synthetic-run', 'management-zmq', 'key', raw['management']['listener']['endpoint'],
            read_peer, hashlib.sha256(read_keys[0]).hexdigest(), read_binding.registration_id, read_binding.service_epoch)
        client = ExecutionManagementReadClient(wrapper, target=target, grant_revision=grant['revision'])
        observation = client.query(spec['parent_ref'], query_id='combined-read')
        assert observation.status == 'OBSERVED' and observation.snapshot.parent_ref == spec['parent_ref']
        assert observation.snapshot.intake_local_state == 'RECEIVED'
        with pytest.raises(ServiceRuntimeError):
            wrapper.call('application.execution_management', SUBMIT, {})
    finally:
        if wrapper is not None: wrapper.close(timeout_seconds=5)
        if parent_client is not None: parent_client.close()
        done.set(); thread.join(15)
    assert not thread.is_alive() and failures.empty()
