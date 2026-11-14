"""Synthetic Host and SQLite assembly; no sockets or external accounts."""
import importlib
import threading
import time
from dataclasses import replace

import pytest

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_execution.account_journal_host import AccountJournalHost, AccountJournalHostError
from bt_api_execution.atomic_submission_intake import AtomicSubmissionIntake
from bt_api_execution.contracts import ExecutionScope
from bt_api_execution.service_atomic_intake import AtomicIntakeService
from bt_api_py.execution_action_grant import ExecutionActionGrant
from bt_api_py.execution_parent_grant import ExecutionParentGrant
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost, ServiceHostError
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.unit.test_iter65_fresh_intake_host import (
    IDENTITY, KEY, OPS, PORT, QUERY, SCOPE, SUBMIT, TEMPLATE, PollingServer, call, parent_payload,
)
from tests.unit.test_service_host import _authority, _service_config


def make_host(tmp_path, monkeypatch, *, wrong_identity=False, lease_ttl_ns=300_000_000_000,
              simulate_availability=True):
    if simulate_availability:
        # Exercise wiring only. This bypass is test-local and is not evidence
        # that an existing writer has stopped or the Host is deployable.
        monkeypatch.setattr(AtomicIntakeService, 'local_intake_available', property(
            lambda self: self.lease_ready and all(callable(getattr(self._intake, name, None))
                for name in ('submit', 'query', 'query_action_facts', 'check_lease', 'renew_lease'))))
    monkeypatch.setattr(importlib.import_module('bt_api_transport_zmq.service_v2'),
                        'ZmqServiceServer', PollingServer)
    component = {'config_schema_version': 2, 'settings': {'instance_id': 'exec-a',
        'bind_ports': [PORT], 'options': {'role': 'atomic_intake'}}}
    config = _service_config({'execution': component}, service_id='execution-service',
                             listener_endpoint='tcp://127.0.0.1:18971')
    authority = _authority(config)
    resource = ResourceGrant('exec-a', PORT, 'scope-a', TEMPLATE, OPS)
    authority = replace(authority, grants={'curve-principal': (PrincipalGrant(IDENTITY, (resource,)),)})
    identity = (WireIdentity(WireRole.SERVICE, 'other-service', 'execution-service')
                if wrong_identity else authority.identity)
    strategy = ExecutionParentGrant('exec-a', 'scope-a', IDENTITY, SCOPE.account_ref,
        SCOPE.strategy_ref, SCOPE.execution_domain_ref, 'strategy-v1', frozenset({KEY}), OPS)
    action = ExecutionActionGrant('exec-a', identity, SCOPE.account_ref, SCOPE.strategy_ref,
        SCOPE.execution_domain_ref, 'action-v1', frozenset({KEY}), frozenset({'v1', 'v2'}),
        frozenset({'accept', 'query'}))
    target = ExecutionScope('FAKE', 'test', 'local-account', 'local-strategy')
    root = tmp_path / 'journal'
    root.mkdir(mode=0o700)
    created = {}
    def provider(*_args):
        journal = AccountJournalHost(root=str(root), scope=target,
            storage_id='d9044d18-dc74-4cb4-a574-3f5d8e5ca7a5')
        created['journal'] = journal
        try:
            lease = journal.acquire_intake_lease(target, 'writer', ttl_ns=lease_ttl_ns)
            intake = AtomicSubmissionIntake(host=journal, targets={SCOPE: target}, lease=lease)
            service = AtomicIntakeService('exec-a', intake, (strategy,), (action,), identity)
            created['service'] = service
            return ComponentResources.owned({'service': service}, ready=lambda: True, close=journal.close)
        except BaseException:
            journal.close()
            raise
    return ServiceHost(config, authority, tmp_path / 'registry.sqlite3', provider), created, strategy, action


def test_simulated_available_host_submits_and_queries_local_receipt(tmp_path, monkeypatch):
    host, created, strategy, action = make_host(tmp_path, monkeypatch)
    try:
        host.start()
        registry = host._registry
        assert registry.execution_parent_grant(instance_id='exec-a', scope_ref='scope-a',
            identity=IDENTITY, scope=SCOPE, operation=SUBMIT) is strategy
        assert action in registry._execution_action_grants.values()
        assert host.healthy and registry.ready
        result = call(host, 'service.register', {'registration_key': 'reg-a',
            'client_session_id': 'client-a', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert result['ok'], result
        binding = result['result']
        def invoke(operation, arguments):
            return call(host, operation, {'registration_id': binding['registration_id'],
                'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': arguments})
        request = parent_payload()
        submitted = invoke(SUBMIT, request)
        assert submitted['ok'] and submitted['result']['local_only'] is True
        assert submitted['result']['status'] == 'RECEIVED'
        queried = invoke(QUERY, {'request_id': request['request_id']})
        assert queried['ok'] and queried['result']['found'] and queried['result']['local_only']
        assert queried['result']['request_sha256'] == submitted['result']['request_sha256']
        assert invoke(SUBMIT, request)['result'] == submitted['result']
        original_lookup = AtomicSubmissionIntake.query_action_facts
        def mismatched_facts(intake, context, arguments, *, grant):
            facts = original_lookup(intake, context, arguments, grant=grant)
            return None if facts is None else (facts[0], 'v2')
        monkeypatch.setattr(AtomicSubmissionIntake, 'query_action_facts', mismatched_facts)
        assert invoke(QUERY, {'request_id': request['request_id']})['result'] == {
            'local_only': True, 'found': False, 'status': 'UNKNOWN'}
        monkeypatch.setattr(AtomicSubmissionIntake, 'query_action_facts', original_lookup)
        assert invoke(QUERY, {'request_id': request['request_id']})['ok']
        assert not invoke(QUERY, {'request_id': request['request_id'],
            'instrument': KEY.to_dict(), 'plan_version': 'v1'})['ok']
        unknown = invoke(QUERY, {'request_id': 'unknown-request'})
        assert unknown['ok'] and unknown['result'] == {
            'local_only': True, 'found': False, 'status': 'UNKNOWN'}
        other = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'ag', 'ag')
        narrowed = replace(strategy, revision='narrow', instruments=frozenset({other}))
        assert host.replace_execution_parent_grant(strategy, narrowed)
        narrow_existing = invoke(QUERY, {'request_id': request['request_id']})
        narrow_missing = invoke(QUERY, {'request_id': 'unknown-request'})
        assert narrow_existing['ok'] and narrow_existing['result'] == narrow_missing['result'] == unknown['result']
        assert host.replace_execution_parent_grant(narrowed, strategy)
        assert host.revoke_execution_parent_grant(strategy)
        revoked_existing = invoke(QUERY, {'request_id': request['request_id']})
        revoked_missing = invoke(QUERY, {'request_id': 'unknown-request'})
        assert not revoked_existing['ok'] and not revoked_missing['ok']
        assert revoked_existing['code'] == revoked_missing['code']
        host.install_execution_parent_grant(strategy)
        assert invoke(QUERY, {'request_id': request['request_id']})['ok']
        with created['journal'].store._read_transaction() as cursor:
            assert cursor.execute('SELECT COUNT(*) FROM execution_parent_records').fetchone()[0] == 1
        assert host.revoke_atomic_intake_action(action)
        action_existing = invoke(QUERY, {'request_id': request['request_id']})
        action_missing = invoke(QUERY, {'request_id': 'unknown-request'})
        assert action_existing['ok'] and action_existing['result'] == action_missing['result'] == unknown['result']
        assert not invoke(SUBMIT, request)['ok']
    finally:
        host.close()
    with pytest.raises(Exception):
        _ = created['journal'].store


def test_real_host_stays_unready_without_legacy_writer_fence(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch, simulate_availability=False)
    try:
        host.start()
        assert callable(created['service']._intake.submit)
        assert callable(created['service']._intake.query)
        assert created['service'].lease_ready
        assert not created['service'].local_intake_available
        assert not host.healthy and not host._registry.ready
        response = call(host, 'service.register', {'registration_key': 'reg-unproven',
            'client_session_id': 'client-a', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert not response['ok']
        assert not host.healthy and not host._registry.ready
    finally:
        host.close()


def test_startup_rejects_service_identity_mismatch_and_closes_journal(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch, wrong_identity=True)
    try:
        with pytest.raises(ServiceHostError, match='ATOMIC_INTAKE_SERVICE_INVALID'):
            host.start()
        # The owned resource was released even though component construction failed.
        with pytest.raises(Exception):
            _ = created['journal'].store
    finally:
        host.close()


def test_external_close_rejected_while_service_idle(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch)
    try:
        host.start()
        journal = created['journal']
        store = journal.store
        with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
            journal.close()
        with pytest.raises(Exception):
            store.close()
        assert journal.store is store
        assert host._components[0].readiness() is True
        assert host.healthy and host._registry.ready
    finally:
        host.close()
    with pytest.raises(AccountJournalHostError):
        _ = journal.store


def test_absent_trusted_lookup_keeps_host_unready(tmp_path, monkeypatch):
    monkeypatch.setattr(AtomicSubmissionIntake, 'query_action_facts', None)
    host, _, _, _ = make_host(tmp_path, monkeypatch)
    try:
        host.start()
        assert not host.healthy and not host._registry.ready
        result = call(host, 'service.register', {'registration_key': 'reg-a',
            'client_session_id': 'client-a', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert not result['ok']
    finally:
        host.close()


def test_lost_lookup_withdraws_registry_readiness(tmp_path, monkeypatch):
    host, _, _, _ = make_host(tmp_path, monkeypatch)
    try:
        host.start()
        assert host.healthy and host._registry.ready
        monkeypatch.setattr(AtomicSubmissionIntake, 'query_action_facts', None)
        deadline = time.monotonic() + 2
        while host.healthy and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not host.healthy
        result = call(host, 'service.register', {'registration_key': 'reg-lost',
            'client_session_id': 'client-a', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert not result['ok'] and not host._registry.ready
    finally:
        host.close()


def test_expired_lease_withdraws_readiness_without_reacquiring(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch)
    try:
        host.start()
        store = created['journal'].store
        with store._transaction() as cursor:
            before = cursor.execute('SELECT fencing_token FROM execution_writer_leases').fetchone()[0]
            cursor.execute('UPDATE execution_writer_leases SET expires_at_ns=?', (time.time_ns() - 1,))
        deadline = time.monotonic() + 2
        while host.healthy and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not host.healthy
        assert not host._registry.ready
        time.sleep(0.65)
        with store._read_transaction() as cursor:
            after = cursor.execute('SELECT fencing_token FROM execution_writer_leases').fetchone()[0]
        assert before == after and not host.healthy and not host._registry.ready
        result = call(host, 'service.register', {'registration_key': 'expired',
            'client_session_id': 'client-a', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert not result['ok']
    finally:
        host.close()


def test_worker_renews_same_unexpired_fencing_token(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch, lease_ttl_ns=2_000_000_000)
    try:
        host.start()
        store = created['journal'].store
        with store._read_transaction() as cursor:
            before = tuple(cursor.execute('SELECT fencing_token, expires_at_ns FROM execution_writer_leases').fetchone())
        time.sleep(0.7)
        with store._read_transaction() as cursor:
            after = tuple(cursor.execute('SELECT fencing_token, expires_at_ns FROM execution_writer_leases').fetchone())
        assert after[0] == before[0] and after[1] > before[1]
        assert host.healthy and host._registry.ready
    finally:
        host.close()


def test_clock_rollback_cannot_extend_cached_status_and_forward_jump_closes(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch, lease_ttl_ns=2_000_000_000)
    original_wall = time.time_ns
    try:
        host.start()
        monkeypatch.setattr(time, 'time_ns', lambda: original_wall() - 3_600_000_000_000)
        time.sleep(0.7)
        service = created['service']
        assert host.healthy
        with service._lease_lock:
            assert 0 < service._lease_valid_until - time.monotonic_ns() <= 2_000_000_000
        monkeypatch.setattr(time, 'time_ns', lambda: original_wall() + 3_600_000_000_000)
        assert not host.healthy and not host._registry.ready
    finally:
        monkeypatch.setattr(time, 'time_ns', original_wall)
        host.close()


def test_stale_monotonic_status_after_pause_withdraws_readiness(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch)
    try:
        host.start()
        service = created['service']
        with service._lease_lock:
            service._lease_valid_until = time.monotonic_ns() - 1
        assert not host.healthy and not host._registry.ready
    finally:
        host.close()


def test_missing_and_damaged_record_both_return_unknown(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch)
    try:
        host.start()
        result = call(host, 'service.register', {'registration_key': 'reg-damaged',
            'client_session_id': 'client-a', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert result['ok']
        binding = result['result']
        def invoke(operation, arguments):
            return call(host, operation, {'registration_id': binding['registration_id'],
                'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': arguments})
        request = parent_payload()
        assert invoke(SUBMIT, request)['ok']
        store = created['journal'].store
        with store._transaction() as cursor:
            cursor.execute("DELETE FROM execution_meta WHERE key LIKE 'execution_parent_intake.v2.%' "
                           "AND key NOT IN (SELECT key FROM execution_meta WHERE key LIKE "
                           "'execution_parent_intake.v2.%' LIMIT 1)")
        missing = invoke(QUERY, {'request_id': 'missing'})
        damaged = invoke(QUERY, {'request_id': request['request_id']})
        expected = {'local_only': True, 'found': False, 'status': 'UNKNOWN'}
        assert missing['ok'] and damaged['ok']
        assert missing['result'] == expected and damaged['result'] == expected
    finally:
        host.close()


def test_action_revoked_after_query_lookup_is_denied(tmp_path, monkeypatch):
    host, _, _, action = make_host(tmp_path, monkeypatch)
    try:
        host.start()
        result = call(host, 'service.register', {'registration_key': 'reg-action-race',
            'client_session_id': 'client-a', 'instance_id': 'exec-a', 'port': PORT,
            'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
        assert result['ok']
        binding = result['result']
        def invoke(operation, arguments):
            return call(host, operation, {'registration_id': binding['registration_id'],
                'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': arguments})
        request = parent_payload()
        assert invoke(SUBMIT, request)['ok']
        lookup = AtomicSubmissionIntake.query_action_facts
        def revoke_after_lookup(intake, context, arguments, *, grant):
            facts = lookup(intake, context, arguments, grant=grant)
            assert facts is not None and host.revoke_atomic_intake_action(action)
            return facts
        monkeypatch.setattr(AtomicSubmissionIntake, 'query_action_facts', revoke_after_lookup)
        assert invoke(QUERY, {'request_id': request['request_id']})['result'] == {
            'local_only': True, 'found': False, 'status': 'UNKNOWN'}
    finally:
        host.close()


def test_maintenance_exit_marks_registry_unready(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch)
    try:
        host.start()
        service = created['service']
        service._lease_stop.set()
        service._lease_thread.join(timeout=2)
        assert not service._lease_thread.is_alive()
        assert not host.healthy and not host._registry.ready
    finally:
        host.close()


def test_stopped_owner_causes_worker_to_withdraw_readiness(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch)
    try:
        host.start()
        stopped = threading.Thread(target=lambda: None)
        stopped.start()
        stopped.join()
        service = created['service']
        service._lease_owner = stopped
        service._lease_thread.join(timeout=2)
        assert not service._lease_thread.is_alive()
        assert not host.healthy and not host._registry.ready
    finally:
        host.close()


def test_renewal_failure_marks_registry_unready(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch)
    try:
        host.start()
        def fail_renewal(*_args, **_kwargs):
            raise RuntimeError('synthetic renewal failure')
        monkeypatch.setattr(created['service']._intake, 'renew_lease', fail_renewal)
        created['service']._lease_thread.join(timeout=2)
        assert not created['service']._lease_thread.is_alive()
        assert not host.healthy and not host._registry.ready
    finally:
        host.close()


def test_blocked_renewal_keeps_journal_pinned_until_close_retry(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch)
    host.start()
    registration = call(host, 'service.register', {'registration_key': 'reg-blocked',
        'client_session_id': 'client-a', 'instance_id': 'exec-a', 'port': PORT,
        'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
    assert registration['ok']
    binding = registration['result']
    entered, release = threading.Event(), threading.Event()
    original = created['service']._intake.renew_lease
    store = created['journal'].store
    def blocked_renewal(*args, **kwargs):
        with store._lock:
            entered.set()
            assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(created['service']._intake, 'renew_lease', blocked_renewal)
    try:
        assert entered.wait(2)
        started = time.monotonic()
        response = call(host, SUBMIT, {'registration_id': binding['registration_id'],
            'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': parent_payload()})
        assert not response['ok']
        assert time.monotonic() - started < 1
        deadline = time.monotonic() + 3
        while host._registry.ready and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not host._registry.ready and not host.healthy
        with pytest.raises(ServiceHostError, match='CLOSE_FAILED'):
            host.close()
        assert host._server is None
        with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
            created['journal'].close()
    finally:
        release.set()
        host.close()


def test_store_lock_busy_does_not_block_admitted_submit(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch)
    host.start()
    registration = call(host, 'service.register', {'registration_key': 'reg-store-busy',
        'client_session_id': 'client-a', 'instance_id': 'exec-a', 'port': PORT,
        'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
    assert registration['ok']
    binding = registration['result']
    entered, release = threading.Event(), threading.Event()
    def hold_store():
        with created['journal'].store._lock:
            entered.set()
            assert release.wait(5)
    worker = threading.Thread(target=hold_store)
    worker.start()
    try:
        assert entered.wait(2)
        started = time.monotonic()
        response = call(host, SUBMIT, {'registration_id': binding['registration_id'],
            'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': parent_payload()})
        assert not response['ok'] and time.monotonic() - started < 1
    finally:
        release.set()
        worker.join(5)
        host.close()
    assert not worker.is_alive()


def test_listener_start_failure_releases_journal_after_hold(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch)
    class FailingServer:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError('synthetic listener failure')
    monkeypatch.setattr(importlib.import_module('bt_api_transport_zmq.service_v2'),
                        'ZmqServiceServer', FailingServer)
    try:
        with pytest.raises(ServiceHostError):
            host.start()
        with pytest.raises(AccountJournalHostError):
            _ = created['journal'].store
        assert not host._pending_resources and not host._components
    finally:
        host.close()


def test_failed_hold_release_retains_account_lock_for_retry(tmp_path, monkeypatch):
    from bt_api_execution.account_journal_host import _ServiceHold
    original = _ServiceHold.close
    attempts = []
    armed = [False]
    def fail_once(hold):
        if not armed[0]:
            return original(hold)
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError('synthetic hold release failure')
        return original(hold)
    monkeypatch.setattr(_ServiceHold, 'close', fail_once)
    host, created, _, _ = make_host(tmp_path, monkeypatch)
    host.start()
    armed[0] = True
    try:
        with pytest.raises(ServiceHostError, match='CLOSE_FAILED'):
            host.close()
        assert host._server is None
        with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
            created['journal'].close()
        with pytest.raises(AccountJournalHostError, match='ACCOUNT_BUSY'):
            AccountJournalHost(root=str(tmp_path / 'journal'),
                scope=ExecutionScope('FAKE', 'test', 'local-account', 'local-strategy'),
                storage_id='d9044d18-dc74-4cb4-a574-3f5d8e5ca7a5')
    finally:
        host.close()
    assert len(attempts) >= 2


def test_listener_close_failure_keeps_journal_pinned(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch)
    host.start()
    server = host._server
    original = server.close
    calls = []
    def close():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('synthetic listener close failure')
        original()
    server.close = close
    try:
        with pytest.raises(ServiceHostError, match='CLOSE_FAILED'):
            host.close()
        with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
            created['journal'].close()
    finally:
        host.close()
    assert len(calls) == 2


def test_shutdown_retries_after_inflight_journal_borrow(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    def borrow():
        with created['journal'].intake_scope():
            entered.set()
            assert release.wait(5)
    host.start()
    journal = created['journal']
    worker = threading.Thread(target=borrow)
    worker.start()
    try:
        assert entered.wait(5)
        with pytest.raises(ServiceHostError, match='CLOSE_FAILED'):
            host.close()
        assert host._server is None
        with pytest.raises(AccountJournalHostError, match='ACCOUNT_BUSY'):
            AccountJournalHost(root=str(tmp_path / 'journal'),
                scope=ExecutionScope('FAKE', 'test', 'local-account', 'local-strategy'),
                storage_id='d9044d18-dc74-4cb4-a574-3f5d8e5ca7a5')
    finally:
        release.set()
        worker.join(5)
        host.close()
    assert not worker.is_alive()
    with pytest.raises(AccountJournalHostError):
        _ = journal.store
