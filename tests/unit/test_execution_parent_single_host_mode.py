"""Synthetic local parent listener configuration; no account operations."""
import sqlite3
import queue
import threading

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.frozen_algorithm_plan import algorithm_plan_digest
from bt_api_transport_zmq.server import CurveClientCredentials
from bt_api_transport_zmq.service_v2 import ZmqServiceClient

from bt_api_py.execution_parent_grant import OPERATIONS, PORT, QUERY, SUBMIT
from bt_api_py.execution_parent_service_host import (
    ParentServiceError, ParentServiceHost, parse_config,
)
from tests.unit.test_iter65_execution_parent_service_host import setup_config
from tests.unit.test_iter65_fresh_intake_host import parent_payload
from tests.unit.test_service_host import _request


@pytest.mark.parametrize('storage_options,expected', [
    ({}, ('quarantined', False)),
    ({'writer_mode': 'quarantined'}, ('quarantined', False)),
    ({'require_existing': True}, ('quarantined', True)),
    ({'writer_mode': 'single_host_cooperative', 'require_existing': True},
     ('single_host_cooperative', True)),
])
def test_storage_options_parse(tmp_path, storage_options, expected):
    raw, _, _ = setup_config(tmp_path)
    raw['storage'].update(storage_options)
    config = parse_config(raw)
    assert (config.writer_mode, config.require_existing) == expected


@pytest.mark.parametrize('storage_options', [
    {'writer_mode': 'single_host_cooperative'},
    {'writer_mode': 'single_host_cooperative', 'require_existing': False},
    {'writer_mode': 'single_host_cooperative', 'require_existing': 1},
    {'writer_mode': 'quarantined', 'require_existing': 0},
    {'writer_mode': None},
    {'writer_mode': 'unknown'},
    {'require_existing': 'true'},
    {'unexpected': True},
])
def test_invalid_options_fail_before_paths_are_used(tmp_path, monkeypatch, storage_options):
    import bt_api_py.execution_parent_service_host as module

    raw, _, _ = setup_config(tmp_path)
    raw['storage'].update(storage_options)
    used_paths = []
    monkeypatch.setattr(module, '_path', lambda value: used_paths.append(value))
    with pytest.raises(ParentServiceError, match='INVALID_CONFIGURATION'):
        parse_config(raw)
    assert not used_paths


@pytest.mark.parametrize('field', ['journal_root', 'registry_path', 'storage_id'])
def test_missing_required_storage_field_is_rejected(tmp_path, field):
    raw, _, _ = setup_config(tmp_path)
    del raw['storage'][field]
    with pytest.raises(ParentServiceError, match='INVALID_CONFIGURATION'):
        parse_config(raw)


def test_cooperative_mode_requires_original_journal_and_reopens_it(tmp_path):
    raw, _, _ = setup_config(tmp_path)
    raw['storage'].update(writer_mode='single_host_cooperative', require_existing=True)
    config = parse_config(raw)
    missing = ParentServiceHost(config)
    with pytest.raises(ParentServiceError, match='START_FAILED'):
        missing.start()
    assert not list(config.journal_root.iterdir())

    # Establish a synthetic original with the default quarantined mode.
    raw['storage'].pop('writer_mode')
    raw['storage'].pop('require_existing')
    original = ParentServiceHost(parse_config(raw))
    original.start()
    assert original.host.healthy is False
    original.close()

    database, = config.journal_root.glob('*.sqlite3')
    with sqlite3.connect(database) as connection:
        before = connection.execute(
            'SELECT fencing_token FROM execution_writer_leases').fetchone()[0]
    reopened = ParentServiceHost(config)
    reopened.start()
    try:
        with sqlite3.connect(database) as connection:
            after = connection.execute(
                'SELECT fencing_token FROM execution_writer_leases').fetchone()[0]
        assert after == before + 1
    finally:
        reopened.close()


def test_cooperative_reopen_refuses_original_missing_lease(tmp_path):
    raw, _, _ = setup_config(tmp_path)
    original = ParentServiceHost(parse_config(raw))
    original.start()
    original.close()
    raw['storage'].update(writer_mode='single_host_cooperative', require_existing=True)
    config = parse_config(raw)
    database, = config.journal_root.glob('*.sqlite3')
    with sqlite3.connect(database) as connection:
        connection.execute('DELETE FROM execution_writer_leases')
    reopened = ParentServiceHost(config)
    with pytest.raises(ParentServiceError, match='START_FAILED'):
        reopened.start()
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT count(*) FROM execution_writer_leases').fetchone() == (0,)


def test_curve_parent_receipt_and_replay_across_cooperative_restart(tmp_path):
    raw, endpoint, keys = setup_config(tmp_path)
    original = ParentServiceHost(parse_config(raw))
    original.start()
    assert not original.host.healthy
    original.close()
    raw['storage'].update(writer_mode='single_host_cooperative', require_existing=True)
    config = parse_config(raw)
    payload = parent_payload()
    payload['spec']['execution_domain_ref'] = 'paper'
    spec = payload['spec']
    payload['algorithm_plan'] = {'schema_version': 1, 'plan_version': 'v1', 'build_id': 'build-a',
        'schedule': {'class': 'PASS', 'version': 1, 'binding': {
            'deadline_ns': spec['expires_at_ns'], 'quantity_step': '1', 'parent_limit': spec['limit_price'],
            'price_step': '1', 'retry_limit': 0}, 'params': {}}}
    spec['plan_digest'] = algorithm_plan_digest(payload['algorithm_plan'])
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-principal', 'strategy-a')
    peer = WireIdentity(WireRole.SERVICE, 'service-principal', 'execution-service')
    scope = WireScope('paper', account_ref='account-a', strategy_ref='strategy-a', run_id='run-a')

    def run_round(restarted):
        ready, failures, done = queue.Queue(), queue.Queue(), threading.Event()
        def serve():
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
                try:
                    host.close()
                except BaseException as error:
                    failures.put(error)
        thread = threading.Thread(target=serve)
        thread.start()
        client = None
        try:
            assert ready.get(timeout=10) == (True, True)
            client = ZmqServiceClient(endpoint, peer, credentials=CurveClientCredentials(*keys))
            def request(operation, arguments, schema):
                return client.request(_request(operation, arguments, identity=identity, scope=scope,
                    schema_id=schema), timeout=3).payload
            registered = request('service.register', {
                'registration_key': 'registration-' + str(restarted),
                'client_session_id': 'session-' + str(restarted), 'instance_id': 'exec-a',
                'port': PORT, 'scope_ref': 'scope-a', 'operations': sorted(OPERATIONS)},
                'sdk.service.control')
            assert registered['ok'], registered
            binding = registered['result']
            def invoke(operation, arguments):
                return request(operation, {'registration_id': binding['registration_id'],
                    'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a',
                    'arguments': arguments}, 'sdk.service.call')
            if restarted:
                queried = invoke(QUERY, {'request_id': payload['request_id']})
                assert queried['ok'] and queried['result']['found'] is True, queried
                assert queried['result']['status'] == 'RECEIVED' and queried['result']['local_only'] is True
            submitted = invoke(SUBMIT, payload)
            assert submitted['ok'] and submitted['result']['status'] == 'RECEIVED', submitted
            assert submitted['result']['local_only'] is True
            if not restarted:
                # Exceed the two-second readiness window to exercise renewal.
                assert not done.wait(2.5)
            queried = invoke(QUERY, {'request_id': payload['request_id']})
            assert queried['ok'] and queried['result']['found'] is True, queried
            assert queried['result']['request_sha256'] == submitted['result']['request_sha256']
            return submitted['result']
        finally:
            if client is not None:
                client.close()
            done.set()
            thread.join(10)
            assert not thread.is_alive() and failures.empty()

    first = run_round(False)
    second = run_round(True)
    assert second == first
    database, = config.journal_root.glob('*.sqlite3')
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT count(*) FROM execution_parent_records').fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM execution_meta "
            "WHERE key LIKE 'execution_parent_local_events.v1.%' AND value LIKE '%LOCAL_RECEIVED%'").fetchone() == (1,)
