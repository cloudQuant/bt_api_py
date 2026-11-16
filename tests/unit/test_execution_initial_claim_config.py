"""Local management-only bootstrap and explicit initial parent claim; no account calls."""
import copy
import queue
import sqlite3
import threading
import uuid

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.account_journal_host import AccountJournalHost
from bt_api_execution.frozen_algorithm_plan import algorithm_plan_digest
from bt_api_transport_zmq.server import CurveClientCredentials
from bt_api_transport_zmq.service_v2 import ZmqServiceClient

from bt_api_py.execution_parent_grant import OPERATIONS, PORT, QUERY, SUBMIT
from bt_api_py.execution_parent_service_host import ParentServiceError, ParentServiceHost, parse_config
from tests.unit.test_iter65_execution_parent_service_host import setup_config
from tests.unit.test_iter65_fresh_intake_host import parent_payload
from tests.unit.test_service_host import _request


def claim(incarnation='a' * 32):
    return {'claim_id': str(uuid.uuid4()), 'expected_incarnation': incarnation,
            'history_assertion': 'management_read_only_no_prior_intake'}


@pytest.mark.parametrize('mutation', [
    lambda raw: raw['storage'].clear(),
    lambda raw: raw['storage'].update(writer_mode='quarantined', require_existing=True),
    lambda raw: raw['storage'].update(writer_mode='single_host_cooperative', require_existing=False),
    lambda raw: raw['lease'].update(initial_claim=None),
    lambda raw: raw['lease']['initial_claim'].pop('claim_id'),
    lambda raw: raw['lease']['initial_claim'].update(extra=True),
    lambda raw: raw['lease']['initial_claim'].update(claim_id='not-a-uuid'),
    lambda raw: raw['lease']['initial_claim'].update(claim_id=str(uuid.uuid4()).upper()),
    lambda raw: raw['lease']['initial_claim'].update(expected_incarnation='A' * 32),
    lambda raw: raw['lease']['initial_claim'].update(expected_incarnation='a' * 31),
    lambda raw: raw['lease']['initial_claim'].update(expected_incarnation='g' * 32),
    lambda raw: raw['lease']['initial_claim'].update(history_assertion='other'),
    lambda raw: raw['lease'].update(unexpected=True),
])
def test_invalid_claim_rejected_before_path_and_key_access(tmp_path, monkeypatch, mutation):
    import bt_api_py.execution_parent_service_host as module
    raw, _, _ = setup_config(tmp_path)
    raw['storage'].update(writer_mode='single_host_cooperative', require_existing=True)
    raw['lease']['initial_claim'] = claim()
    mutation(raw)
    used = []
    monkeypatch.setattr(module, '_path', lambda value: used.append(value))
    monkeypatch.setattr(module, 'read_bounded', lambda *args, **kwargs: used.append(args))
    with pytest.raises(ParentServiceError, match='INVALID_CONFIGURATION'):
        parse_config(raw)
    assert not used


def create_management_only(raw):
    config = parse_config(raw)
    host = AccountJournalHost(root=str(config.journal_root), scope=config.local_scope,
                              storage_id=config.storage_id)
    try:
        incarnation = host._incarnation(host._store._connection)
    finally:
        host.close()
    raw['storage'].update(writer_mode='single_host_cooperative', require_existing=True)
    raw['lease']['initial_claim'] = claim(incarnation)
    database, = config.journal_root.glob('*.sqlite3')
    return database


def snapshot(database):
    with sqlite3.connect(database) as db:
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: sorted(db.execute('SELECT * FROM "' + name + '"').fetchall(), key=repr) for name in names}


def test_claim_refuses_unknown_metadata_without_mutating_original(tmp_path):
    raw, _, _ = setup_config(tmp_path)
    database = create_management_only(raw)
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO execution_meta(key,value) VALUES ('unknown-initial-metadata','x')")
    before = snapshot(database)
    with pytest.raises(ParentServiceError, match='START_FAILED'):
        ParentServiceHost(parse_config(raw)).start()
    assert snapshot(database) == before


def test_management_only_first_claim_submit_query_and_resume(tmp_path):
    raw, endpoint, keys = setup_config(tmp_path)
    database = create_management_only(raw)
    first_config = parse_config(raw)
    assert first_config.initial_claim == tuple(raw['lease']['initial_claim'][k] for k in
                                               ('claim_id', 'expected_incarnation', 'history_assertion'))
    # Mutating the input after parsing cannot alter the declaration used at startup.
    saved_claim = copy.deepcopy(raw['lease']['initial_claim'])
    raw['lease']['initial_claim']['claim_id'] = str(uuid.uuid4())
    assert first_config.initial_claim[0] == saved_claim['claim_id']
    raw['lease']['initial_claim'] = saved_claim
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

    def run(config, resuming):
        ready, errors, stop = queue.Queue(), queue.Queue(), threading.Event()
        def serve():
            parent = ParentServiceHost(config)
            try:
                parent.start()
                ready.put(parent.host.healthy and parent.host._registry.ready)
                while not stop.is_set():
                    parent.poll()
            except BaseException as error:
                errors.put(error)
                ready.put(False)
            finally:
                try:
                    parent.close()
                except BaseException as error:
                    errors.put(error)
        thread = threading.Thread(target=serve)
        thread.start()
        client = None
        try:
            assert ready.get(timeout=10) is True
            client = ZmqServiceClient(endpoint, peer, credentials=CurveClientCredentials(*keys))
            def request(operation, args, schema):
                return client.request(_request(operation, args, identity=identity, scope=scope,
                    schema_id=schema), timeout=3).payload
            registered = request('service.register', {'registration_key': f'reg-{resuming}',
                'client_session_id': f'session-{resuming}', 'instance_id': 'exec-a', 'port': PORT,
                'scope_ref': 'scope-a', 'operations': sorted(OPERATIONS)}, 'sdk.service.control')
            assert registered['ok'], registered
            binding = registered['result']
            def invoke(operation, args):
                return request(operation, {'registration_id': binding['registration_id'],
                    'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a',
                    'arguments': args}, 'sdk.service.call')
            if not resuming:
                submitted = invoke(SUBMIT, payload)
                assert submitted['ok'] and submitted['result']['status'] == 'RECEIVED', submitted
                assert submitted['result']['local_only'] is True
            queried = invoke(QUERY, {'request_id': payload['request_id']})
            assert queried['ok'] and queried['result']['found'] is True, queried
            assert queried['result']['status'] == 'RECEIVED' and queried['result']['local_only'] is True
        finally:
            if client is not None:
                client.close()
            stop.set()
            thread.join(10)
            assert not thread.is_alive() and errors.empty()

    run(first_config, False)
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT fencing_token FROM execution_writer_leases').fetchone() == (1,)
    before = snapshot(database)
    with pytest.raises(ParentServiceError, match='START_FAILED'):
        ParentServiceHost(parse_config(raw)).start()
    assert snapshot(database) == before
    del raw['lease']['initial_claim']
    resumed = parse_config(raw)
    assert resumed.initial_claim is None
    run(resumed, True)
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT fencing_token FROM execution_writer_leases').fetchone() == (2,)
        assert db.execute('SELECT count(*) FROM execution_parent_records').fetchone() == (1,)
