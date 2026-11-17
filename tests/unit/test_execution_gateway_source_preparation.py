"""Synthetic local journal and authenticated localhost Gateway only."""
import hashlib
import json
import copy
import os
import queue
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
import zmq
import test_iter65_atomic_submission_intake as atomic_fixture
from bt_api_base.service_protocol import WireIdentity, WireRole, encode_wire_value
from bt_api_execution.account_journal_host import AccountJournalHost
from bt_api_execution.atomic_submission_intake import _receipt_origin_key
from bt_api_execution.frozen_algorithm_plan import algorithm_plan_digest
from bt_api_execution.local_gateway_parent_preparation import (
    LocalGatewayParentPreparation, LocalGatewayParentPreparationError, _preparation_key,
    _source_attempt_key, _source_observation_key,
)
from bt_api_execution.submission_intake import _identity
from bt_api_py.execution_gateway_source_preparation import GatewaySourcePreparation
from bt_api_py.execution_parent_grant import OPERATIONS, PORT, SUBMIT
from bt_api_py.execution_parent_service_host import ParentServiceHost, parse_config
from bt_api_py.service_runtime import ServiceCallOutcomeUnknownError, ServiceRuntimeRequestNotSentError
from bt_api_transport_zmq.server import CurveClientCredentials
from bt_api_transport_zmq.service_v2 import ZmqServiceClient
from tests.unit.test_iter65_execution_parent_service_host import setup_config
from tests.unit.test_service_host import _request
from tests.unit.test_iter65_prepared_parent_curve_loopback import local_curve, parent_counts, source_calls
from test_iter65_atomic_submission_intake import atomic  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get('BT_API_SYNTHETIC_CURVE_LOOPBACK') != '1' or not zmq.has('curve'),
    reason='requires explicit synthetic localhost CURVE sandbox opt-in',
)


@pytest.fixture(autouse=True)
def align_synthetic_execution_identity(monkeypatch):
    """Give the synthetic intake and Gateway the same Execution identity."""
    original = atomic_fixture.PrototypeHarness.__init__
    def initialize(self, intake, registry, scope):
        original(self, intake, registry, scope)
        assert registry.revoke_execution_action(self.action_grant)
        self.identity = WireIdentity(WireRole.SERVICE, 'curve-execution', 'execution')
        self.action_grant = replace(self.action_grant, identity=self.identity)
        registry.configure_execution_actions(grants=(self.action_grant,))
    monkeypatch.setattr(atomic_fixture.PrototypeHarness, '__init__', initialize)


def call(env, *, timeout_seconds=5.0):
    request = env['request']
    return env['threaded'].record_prepared_parent_source(env['target'], env['preparation'],
        request['spec']['parent_ref'], request_id=request['request_id'],
        request_sha256=hashlib.sha256(encode_wire_value(request)).hexdigest(),
        instance_id='exec-a', attempt_id=str(uuid.uuid4()), timeout_seconds=timeout_seconds)


def rows(env):
    prep, store = env['preparation'], env['store']
    parent_ref = env['request']['spec']['parent_ref']
    with store._read_transaction() as cursor:
        return [cursor.execute('SELECT value FROM execution_meta WHERE key=?',
            (key(prep._parent_scope, parent_ref),)).fetchone() for key in
            (_receipt_origin_key, _source_attempt_key, _source_observation_key)]


def test_recorded_source_is_local_once_and_never_dispatches(local_curve):
    env = local_curve
    assert call(env) == {'status': 'OBSERVED'}
    origin, attempt, observation = rows(env)
    assert origin and attempt and observation
    origin, attempt, observation = (json.loads(row[0]) for row in (origin, attempt, observation))
    assert origin['origin']['lease_token'] == env['lease'].fencing_token
    assert origin['origin']['instance_id'] == 'exec-a'
    assert attempt['status'] == 'UNKNOWN' and observation['status'] == 'OBSERVED'
    assert observation['attempt_id'] == attempt['attempt_id']
    assert observation['journal_id'] == env['journal'].journal_id
    assert call(env) == {'status': 'HISTORICAL'}
    assert len(source_calls(env)) == 1 and parent_counts(env) == (0, 0)


def test_missing_original_binding_never_sends_source(local_curve):
    env = local_curve
    prep, store = env['preparation'], env['store']
    with store._transaction() as cursor:
        cursor.execute('DELETE FROM execution_meta WHERE key=?',
            (_receipt_origin_key(prep._parent_scope, env['request']['spec']['parent_ref']),))
        cursor.execute('DELETE FROM execution_meta WHERE key=?',
            (_preparation_key(prep._parent_scope, env['request']['spec']['parent_ref']),))
    with pytest.raises(Exception):
        call(env)
    assert len(source_calls(env)) == 0 and rows(env)[1:] == [None, None]
    with store._read_transaction() as cursor:
        assert cursor.execute('SELECT value FROM execution_meta WHERE key=?',
            (_preparation_key(prep._parent_scope, env['request']['spec']['parent_ref']),)).fetchone() is None


def test_different_execution_identity_same_instance_and_lease_is_rejected(local_curve):
    env = local_curve
    request = env['request']
    other = WireIdentity(WireRole.SERVICE, 'different-execution', 'execution')
    with pytest.raises(LocalGatewayParentPreparationError):
        env['preparation'].begin_source(request['spec']['parent_ref'], request_id=request['request_id'],
            request_sha256=hashlib.sha256(encode_wire_value(request)).hexdigest(), instance_id='exec-a',
            attempt_id=str(uuid.uuid4()), target={'identity': _identity(other)})
    assert rows(env)[1:] == [None, None] and len(source_calls(env)) == 0


@pytest.mark.parametrize('field', ['principal', 'actor', 'role', 'delegate', 'account', 'strategy',
                                   'domain', 'run', 'missing', 'extra'])
def test_mismatched_subject_leaves_unknown_and_does_not_retry(local_curve, field):
    env = local_curve
    def change(result):
        subject = copy.deepcopy(result['result']['strategy_subject'])
        if field in ('principal', 'actor', 'role', 'delegate'):
            key = {'principal': 'principal_ref', 'actor': 'actor_ref', 'role': 'role', 'delegate': 'delegate_ref'}[field]
            subject['identity'][key] = 'other'
        elif field in ('account', 'strategy', 'domain', 'run'):
            key = {'account': 'account_ref', 'strategy': 'strategy_ref',
                   'domain': 'execution_domain_ref', 'run': 'run_id'}[field]
            subject['scope'][key] = 'other'
        elif field == 'missing':
            del subject['identity']['delegate_ref']
        else:
            subject['identity']['extra'] = 'other'
        result['result']['strategy_subject'] = subject
    env['hook'][0] = change
    with pytest.raises(ServiceCallOutcomeUnknownError):
        call(env)
    _, attempt, observation = rows(env)
    assert attempt and json.loads(attempt[0])['status'] == 'UNKNOWN' and observation is None
    env['hook'][0] = None
    assert call(env) == {'status': 'UNKNOWN'}
    assert len(source_calls(env)) == 1 and parent_counts(env) == (0, 0)


@pytest.mark.parametrize('state', ['FROZEN_UNSENT', 'UNKNOWN', 'REQUESTED', 'CORRUPT'])
def test_cancelled_parent_never_starts_recorded_source(local_curve, state):
    env = local_curve
    with env['store']._transaction() as cursor:
        cursor.execute('UPDATE execution_parent_records SET cancel_state=? WHERE parent_id=?',
                       (state, env['request']['spec']['parent_ref']))
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        call(env)
    assert rows(env)[1:] == [None, None] and len(source_calls(env)) == 0


@pytest.mark.parametrize('change', ['account', 'strategy', 'run'])
def test_gateway_target_scope_change_never_starts_recorded_source(local_curve, change):
    env = local_curve
    target = env['target']
    field = {'account': 'account_ref', 'strategy': 'strategy_ref', 'run': 'run_id'}[change]
    scope = replace(target.scope, **{field: 'other'})
    env['target'] = replace(target, scope=scope, run_id=scope.run_id)
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        call(env)
    assert rows(env)[1:] == [None, None] and len(source_calls(env)) == 0


def test_local_parent_change_during_source_keeps_only_unknown_attempt(local_curve):
    env = local_curve
    def change(_result):
        with env['store']._transaction() as cursor:
            cursor.execute("UPDATE execution_parent_records SET cancel_state='UNKNOWN' WHERE parent_id=?",
                           (env['request']['spec']['parent_ref'],))
    env['hook'][0] = change
    with pytest.raises(ServiceCallOutcomeUnknownError):
        call(env)
    _, attempt, observation = rows(env)
    assert attempt and observation is None and json.loads(attempt[0])['status'] == 'UNKNOWN'
    assert len(source_calls(env)) == 1 and parent_counts(env) == (0, 0)


@pytest.mark.parametrize('field', ['request_id', 'request_sha256', 'instance_id'])
def test_wrong_original_receipt_binding_never_sends(local_curve, field):
    env = local_curve
    request = env['request']
    args = {'request_id': request['request_id'], 'request_sha256': hashlib.sha256(
        encode_wire_value(request)).hexdigest(), 'instance_id': 'exec-a'}
    args[field] = 'a' * 64 if field == 'request_sha256' else 'wrong'
    with pytest.raises(Exception):
        env['threaded'].record_prepared_parent_source(env['target'], env['preparation'],
            request['spec']['parent_ref'], **args, attempt_id=str(uuid.uuid4()))
    assert rows(env)[1:] == [None, None] and len(source_calls(env)) == 0


def test_replaced_gateway_registration_never_starts_source(local_curve):
    env = local_curve
    env['target'] = replace(env['target'], registration_id='replacement-registration')
    with pytest.raises(Exception):
        call(env)
    assert rows(env)[1:] == [None, None] and len(source_calls(env)) == 0


def test_registration_lost_during_source_preserves_unknown(local_curve):
    env = local_curve
    env['hook'][0] = lambda _result: env['runtime']._strict_registration_transports.clear()
    with pytest.raises(ServiceCallOutcomeUnknownError):
        call(env)
    _, attempt, observation = rows(env)
    assert attempt and observation is None and json.loads(attempt[0])['status'] == 'UNKNOWN'
    assert len(source_calls(env)) == 1 and parent_counts(env) == (0, 0)


def test_attempt_changed_during_source_cannot_create_observation(local_curve):
    env = local_curve
    prep = env['preparation']
    def change(_result):
        with env['store']._transaction() as cursor:
            cursor.execute('UPDATE execution_meta SET value=? WHERE key=?', ('{}',
                _source_attempt_key(prep._parent_scope, env['request']['spec']['parent_ref'])))
    env['hook'][0] = change
    with pytest.raises(ServiceCallOutcomeUnknownError):
        call(env)
    _, attempt, observation = rows(env)
    assert attempt and attempt[0] == '{}' and observation is None
    assert len(source_calls(env)) == 1 and parent_counts(env) == (0, 0)


def test_no_local_host_or_store_lock_is_held_during_gateway_source(local_curve):
    env = local_curve
    verified = []
    def check(_result):
        host_lock, store_lock = env['rig'].host._lock, env['store']._lock
        host_acquired = host_lock.acquire(blocking=False)
        store_acquired = store_lock.acquire(blocking=False)
        try:
            verified.append(host_acquired and store_acquired)
        finally:
            if store_acquired: store_lock.release()
            if host_acquired: host_lock.release()
    env['hook'][0] = check
    assert call(env) == {'status': 'OBSERVED'}
    assert verified == [True]


def test_timeout_after_source_started_never_becomes_retry_success(local_curve):
    env = local_curve
    entered, release = threading.Event(), threading.Event()
    def pause(_result):
        entered.set()
        assert release.wait(2)
    env['hook'][0] = pause
    try:
        with pytest.raises(Exception):
            call(env, timeout_seconds=0.3)
        assert entered.is_set()
    finally:
        release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not env['threaded'].status().healthy:
        time.sleep(0.01)
    _, attempt, _ = rows(env)
    assert attempt and json.loads(attempt[0])['status'] == 'UNKNOWN'
    try:
        result = call(env)
    except ServiceRuntimeRequestNotSentError:
        # A timed-out strict transport may have lost its registration proof.
        pass
    else:
        assert result == {'status': 'UNKNOWN'}
    assert len(source_calls(env)) == 1 and parent_counts(env) == (0, 0)


def test_changed_lease_cannot_claim_historic_receipt(local_curve):
    env = local_curve
    rig = env['rig']
    old = rig.host
    root, storage = old._root, old._storage_id
    old.close()
    rig.host = AccountJournalHost(root=root, scope=rig.target, storage_id=storage,
                                  writer_mode='single_host_cooperative', require_existing=True)
    lease = rig.host.acquire_intake_lease(rig.target, 'new-owner')
    env['store'] = rig.host._store
    env['preparation'] = LocalGatewayParentPreparation(host=rig.host, scope=rig.target, lease=lease,
                                                       wire_scope=rig.scope, identity=rig.identity)
    with pytest.raises(Exception):
        call(env)
    assert len(source_calls(env)) == 0 and rows(env)[1:] == [None, None]


def test_explicit_sdk_composition_uses_original_intake_and_target(local_curve):
    env = local_curve
    rig = env['rig']
    service = SimpleNamespace(_intake=rig.intake.intake, instance_id='exec-a', lease_ready=True)
    owner = object.__new__(ParentServiceHost)
    owner.state, owner.journal = 'running', rig.host
    owner.host = SimpleNamespace(healthy=True, _atomic_intake_services=(service,))
    owner.config = SimpleNamespace(identity=env['service_identity'], wire_scope=rig.scope,
        local_scope=rig.target, parent_grants=(rig.grant,), action_grant=SimpleNamespace(instance_id='exec-a'))
    adapter = GatewaySourcePreparation(owner=owner, runtime=env['threaded'], target=env['target'],
                                       strategy_identity=rig.identity)
    request = env['request']
    digest = hashlib.sha256(encode_wire_value(request)).hexdigest()
    assert adapter.observe(request['spec']['parent_ref'], request_id=request['request_id'],
                           request_sha256=digest) == 'OBSERVED'
    assert adapter.observe(request['spec']['parent_ref'], request_id=request['request_id'],
                           request_sha256=digest) == 'HISTORICAL'
    assert len(source_calls(env)) == 1 and parent_counts(env) == (0, 0)


@pytest.mark.parametrize('change', ['lease', 'registration'])
def test_success_followed_by_binding_change_is_unknown(local_curve, change):
    env, rig = local_curve, local_curve['rig']
    service = SimpleNamespace(_intake=rig.intake.intake, instance_id='exec-a', lease_ready=True)
    owner = object.__new__(ParentServiceHost)
    owner.state, owner.journal = 'running', rig.host
    owner.host = SimpleNamespace(healthy=True, _atomic_intake_services=(service,))
    owner.config = SimpleNamespace(identity=env['service_identity'], wire_scope=rig.scope,
        local_scope=rig.target, parent_grants=(rig.grant,), action_grant=SimpleNamespace(instance_id='exec-a'))
    adapter = GatewaySourcePreparation(owner=owner, runtime=env['threaded'], target=env['target'],
                                       strategy_identity=rig.identity)
    request = env['request']
    digest = hashlib.sha256(encode_wire_value(request)).hexdigest()
    assert adapter.observe(request['spec']['parent_ref'], request_id=request['request_id'],
                           request_sha256=digest) == 'OBSERVED'
    original_lease = rig.host._issued_lease
    try:
        if change == 'lease':
            object.__setattr__(rig.host, '_issued_lease', object())
        else:
            adapter._target = replace(env['target'], registration_id='replaced')
        assert adapter.observe(request['spec']['parent_ref'], request_id=request['request_id'],
                               request_sha256=digest) == 'UNKNOWN'
    finally:
        object.__setattr__(rig.host, '_issued_lease', original_lease)
    assert len(source_calls(env)) == 1


def test_retired_prepared_entrypoints_never_send(local_curve):
    env = local_curve
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        env['threaded'].capture_prepared_parent_source(
            env['target'], env['preparation'], env['request']['spec']['parent_ref'])
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        env['threaded']._parent_capability_call('prepared_source', env['target'], {},
            (env['preparation'], env['request']['spec']['parent_ref'], env['target']), 5.0, None)
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        type(env['runtime']).capture_prepared_parent_source(env['runtime'], env['target'], env['preparation'],
            env['request']['spec']['parent_ref'], deadline_monotonic=time.monotonic() + 5)
    assert len(source_calls(env)) == 0 and rows(env)[1:] == [None, None]


def test_malformed_wrapper_request_has_no_local_side_effect(local_curve):
    env, rig = local_curve, local_curve['rig']
    service = SimpleNamespace(_intake=rig.intake.intake, instance_id='exec-a', lease_ready=True)
    owner = object.__new__(ParentServiceHost)
    owner.state, owner.journal = 'running', rig.host
    owner.host = SimpleNamespace(healthy=True, _atomic_intake_services=(service,))
    owner.config = SimpleNamespace(identity=env['service_identity'], wire_scope=rig.scope,
        local_scope=rig.target, parent_grants=(rig.grant,), action_grant=SimpleNamespace(instance_id='exec-a'))
    adapter = GatewaySourcePreparation(owner=owner, runtime=env['threaded'], target=env['target'],
                                       strategy_identity=rig.identity)
    assert adapter.observe('', request_id='a', request_sha256='a' * 64) == 'INVALID_REQUEST'
    assert adapter.observe('p', request_id='a', request_sha256='wrong') == 'INVALID_REQUEST'
    assert rows(env)[1:] == [None, None] and len(source_calls(env)) == 0


def test_real_parent_listener_composes_with_authenticated_gateway(local_curve, tmp_path):
    env, rig = local_curve, local_curve['rig']
    directory = tmp_path / 'actual-parent'; directory.mkdir(mode=0o700)
    raw, endpoint, keys = setup_config(directory)
    raw['service']['service_id'] = env['service_identity'].actor_ref
    raw['service_identity'].update(principal_ref=env['service_identity'].principal_ref,
                                   actor_ref=env['service_identity'].actor_ref)
    raw['wire_scope']['execution_domain_ref'] = rig.scope.execution_domain_ref
    raw['strategy_grants'][0]['identity'].update(principal_ref=rig.identity.principal_ref,
                                                 actor_ref=rig.identity.actor_ref)
    seed = ParentServiceHost(parse_config(raw)); seed.start(); seed.close()
    raw['storage'].update(writer_mode='single_host_cooperative', require_existing=True)
    config = parse_config(raw)
    ready, errors, done = queue.Queue(), queue.Queue(), threading.Event()
    def serve():
        owner = ParentServiceHost(config)
        try:
            owner.start(); ready.put(owner)
            while not done.is_set(): owner.poll()
        except BaseException as error:
            errors.put(error); ready.put(None)
        finally:
            try: owner.close()
            except BaseException as error: errors.put(error)
    thread = threading.Thread(target=serve); thread.start()
    client = None
    try:
        owner = ready.get(timeout=10)
        deadline = time.monotonic() + 2
        while owner is not None and not owner.host.healthy and time.monotonic() < deadline:
            time.sleep(0.01)
        assert owner is not None and owner.host.healthy
        peer = WireIdentity(WireRole.SERVICE, env['service_identity'].principal_ref,
                            env['service_identity'].actor_ref)
        client = ZmqServiceClient(endpoint, peer, credentials=CurveClientCredentials(*keys))
        def request(operation, arguments, schema):
            return client.request(_request(operation, arguments, identity=rig.identity, scope=rig.scope,
                                           schema_id=schema), timeout=3).payload
        registered = request('service.register', {'registration_key': 'source-parent',
            'client_session_id': 'source-session', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': sorted(OPERATIONS)}, 'sdk.service.control')
        assert registered['ok'], registered
        binding = registered['result']
        payload = rig.parent(parent_ref='combined-parent')
        payload['algorithm_plan']['build_id'] = 'build-a'
        payload['spec']['plan_digest'] = algorithm_plan_digest(payload['algorithm_plan'])
        result = request(SUBMIT, {'registration_id': binding['registration_id'],
            'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': payload},
            'sdk.service.call')
        assert result['ok'] and result['result']['local_only'] and result['result']['status'] == 'RECEIVED'
        deadline = time.monotonic() + 2
        while not owner.host.healthy and time.monotonic() < deadline:
            time.sleep(0.01)
        service, = owner.host._atomic_intake_services
        route = service._intake._routes[env['target'].scope]
        assert owner.host.healthy, owner.host.readiness_diagnostics
        assert (owner.state, owner.host.healthy, env['target'].identity == config.identity,
                env['target'].scope == config.wire_scope, any(g.identity == rig.identity for g in config.parent_grants),
                service._intake._host is owner.journal, route.scope == config.local_scope,
                route.lease is service._intake._lease, service.instance_id == config.action_grant.instance_id,
                service.lease_ready) == ('running', True, True, True, True, True, True, True, True, True)
        adapter = GatewaySourcePreparation(owner=owner, runtime=env['threaded'], target=env['target'],
                                           strategy_identity=rig.identity)
        digest = hashlib.sha256(encode_wire_value(payload)).hexdigest()
        status = adapter.observe('combined-parent', request_id=payload['request_id'], request_sha256=digest)
        assert status == 'OBSERVED'
        assert adapter.observe('combined-parent', request_id=payload['request_id'], request_sha256=digest) == 'HISTORICAL'
        assert owner.journal is not rig.host and len(source_calls(env)) == 1 and parent_counts(env) == (0, 0)
    finally:
        if client is not None: client.close()
        done.set(); thread.join(10)
    assert not thread.is_alive() and errors.empty()


def test_origin_binding_rolls_back_with_failed_receipt_transaction(atomic, monkeypatch):
    from bt_api_execution import atomic_submission_intake as module
    from bt_api_py.execution_parent_grant import SUBMIT

    rig, store, _ = atomic
    request = rig.parent()
    original = module._nonblocking_store_transaction
    @contextmanager
    def fail_before_commit(store, *, write):
        with original(store, write=write) as cursor:
            yield cursor
            if write and cursor.execute('SELECT count(*) FROM execution_parent_records').fetchone()[0]:
                raise RuntimeError('synthetic precommit failure')
    monkeypatch.setattr(module, '_nonblocking_store_transaction', fail_before_commit)
    assert not rig.call(SUBMIT, request)['ok']
    with store._read_transaction() as cursor:
        assert cursor.execute('SELECT count(*) FROM execution_parent_records').fetchone()[0] == 0
        assert cursor.execute("SELECT count(*) FROM execution_meta "
                              "WHERE key LIKE 'execution_parent_intake_origin.v1.%'").fetchone()[0] == 0


def test_ambiguous_receipt_commit_keeps_original_binding_on_replay(atomic, monkeypatch):
    from bt_api_execution import atomic_submission_intake as module
    from bt_api_py.execution_parent_grant import SUBMIT

    rig, store, _ = atomic
    request = rig.parent()
    original = module._nonblocking_store_transaction
    @contextmanager
    def fail_after_commit(store, *, write):
        inserted = False
        with original(store, write=write) as cursor:
            yield cursor
            if write and cursor.execute('SELECT count(*) FROM execution_parent_records').fetchone()[0]:
                inserted = True
        if inserted:
            raise RuntimeError('synthetic ambiguous receipt commit')
    monkeypatch.setattr(module, '_nonblocking_store_transaction', fail_after_commit)
    assert not rig.call(SUBMIT, request)['ok']
    monkeypatch.setattr(module, '_nonblocking_store_transaction', original)
    with store._read_transaction() as cursor:
        before = cursor.execute("SELECT key,value FROM execution_meta "
                                "WHERE key LIKE 'execution_parent_intake_origin.v1.%'").fetchall()
        assert len(before) == 1
    assert rig.call(SUBMIT, request)['ok']
    with store._read_transaction() as cursor:
        assert cursor.execute("SELECT key,value FROM execution_meta "
                              "WHERE key LIKE 'execution_parent_intake_origin.v1.%'").fetchall() == before


@pytest.mark.parametrize('stage,after_commit', [('attempt', False), ('attempt', True),
                                                ('observation', False), ('observation', True)])
def test_commit_fault_never_repeats_unknown_source(local_curve, monkeypatch, stage, after_commit):
    from bt_api_execution import atomic_submission_intake as module
    from bt_api_execution import local_gateway_parent_preparation as preparation_module

    env = local_curve
    original = module._nonblocking_store_transaction
    hit = []
    prefix = ('execution_gateway_source_attempt.v1.%' if stage == 'attempt'
              else 'execution_gateway_source_observation.v1.%')
    @contextmanager
    def fault(store, *, write):
        injected = False
        with original(store, write=write) as cursor:
            yield cursor
            if write and not hit and cursor.execute(
                    'SELECT count(*) FROM execution_meta WHERE key LIKE ?', (prefix,)).fetchone()[0]:
                hit.append(True)
                injected = True
                if not after_commit:
                    raise RuntimeError('synthetic failure before commit')
        if injected and after_commit:
            raise RuntimeError('synthetic failure after commit')
    monkeypatch.setattr(module, '_nonblocking_store_transaction', fault)
    monkeypatch.setattr(preparation_module, '_nonblocking_store_transaction', fault)
    with pytest.raises(Exception):
        call(env)
    assert hit
    monkeypatch.setattr(module, '_nonblocking_store_transaction', original)
    monkeypatch.setattr(preparation_module, '_nonblocking_store_transaction', original)
    _, attempt, observation = rows(env)
    if stage == 'attempt' and not after_commit:
        assert attempt is None and observation is None and len(source_calls(env)) == 0
    else:
        assert attempt is not None
        expected = 'HISTORICAL' if stage == 'observation' and after_commit else 'UNKNOWN'
        assert call(env) == {'status': expected}
        assert len(source_calls(env)) == (0 if stage == 'attempt' else 1)
        assert (observation is not None) is (stage == 'observation' and after_commit)
    assert parent_counts(env) == (0, 0)
