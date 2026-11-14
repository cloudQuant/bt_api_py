"""Atomic Host optional monitoring over synthetic transport and local journals."""
import builtins
import threading
import time
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireScope
from bt_api_execution.contracts import ExecutionScope
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceClients
from bt_api_py.service_atomic_monitor_assembly import TrustedAtomicMonitor
from tests.unit.test_iter65_atomic_intake_host import make_host, parent_payload
from tests.unit.test_iter65_fresh_intake_host import SCOPE, SUBMIT, QUERY, call
from tests.unit.test_service_monitor_outbox import (
    _config, _RemoteOutbox, _RuntimeRpcLeaf, _RegistrationIds, _PEER, _SCOPE_REF,
)


def _wait(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        threading.Event().wait(.01)
    return False


def _with_monitor(tmp_path, monkeypatch, *, factory=None, simulate_availability=True):
    base, created, _, _ = make_host(tmp_path, monkeypatch, simulate_availability=simulate_availability)
    target = ExecutionScope('FAKE', 'test', 'local-account', 'local-strategy')
    wire = WireScope(SCOPE.execution_domain_ref, account_ref=target.account_ref,
                     strategy_ref=target.strategy_id, run_id='monitor-run')
    config_raw = _config().to_public_dict()
    config_raw['service_id'] = base._config.service_id
    config = parse_service_config(config_raw)
    remote = _RemoteOutbox()
    ids = _RegistrationIds()
    transports = []
    def transport_factory(*_args):
        leaf = _RuntimeRpcLeaf(remote, ids)
        transports.append(leaf)
        if factory is not None:
            return factory(leaf, len(transports))
        return leaf
    clients = TrustedServiceClients(lambda _ref: wire, lambda _service: _PEER,
                                    transport_factory,
                                    {'upstream.telemetry': frozenset({'events.append'})})
    selection = TrustedAtomicMonitor('exec-a', target, wire, _SCOPE_REF, config, clients,
                                     tmp_path / 'monitor-checkpoint.sqlite', .01, .03)
    host = ServiceHost(base._config, base._authority, base._registry_path,
                       base._resource_provider, trusted_atomic_monitors=(selection,))
    return host, created, remote, transports


def _registered_submit(host, request):
    registration = call(host, 'service.register', {'registration_key': 'reg-a',
        'client_session_id': 'client-a', 'instance_id': 'exec-a', 'port': 'service.execution_parent',
        'scope_ref': 'scope-a', 'operations': [SUBMIT, QUERY]})
    assert registration['ok'], registration
    binding = registration['result']
    return call(host, SUBMIT, {'registration_id': binding['registration_id'],
        'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': request})


def test_unselected_does_not_import_monitor(tmp_path, monkeypatch):
    host, _, _, _ = make_host(tmp_path, monkeypatch)
    real_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name.startswith('bt_api_monitor'):
            raise AssertionError('unselected Monitor imported')
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    try:
        host.start()
        assert host.summary['atomic_monitor']['selected'] == 0
    finally:
        host.close()


def test_selected_real_runtime_replays_lost_ack_without_gating_host(tmp_path, monkeypatch):
    def factory(leaf, number):
        if number == 1:
            leaf.fail_after_append = True
        return leaf
    host, created, remote, transports = _with_monitor(tmp_path, monkeypatch, factory=factory)
    try:
        host.start()
        assert _wait(lambda: host._atomic_monitor_owners and host._atomic_monitor_owners[0].resource
                     and host._atomic_monitor_owners[0].resource.initialized)
        result = _registered_submit(host, parent_payload())
        assert result['ok'] and result['result']['status'] == 'RECEIVED'
        assert _wait(lambda: len(remote.append_attempts) == 1)
        assert _wait(lambda: host._atomic_monitor_owners[0].owner.status().outcome == 'FAILED')
        assert host.healthy and host._registry.ready
        host._atomic_monitor_next_attempt = 0
        host.serve_once(0)
        assert _wait(lambda: len(remote.append_attempts) == 2)
        assert remote.append_attempts[0] == remote.append_attempts[1]
        assert len(remote.events) == 1 and len(transports) == 2
    finally:
        host.close()
    assert created['journal']._closed


def test_blocked_network_preserves_hold_and_close_retries(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def factory(leaf, _number):
        original = leaf.request
        def request(envelope, *, timeout):
            if envelope.header.operation == 'events.append':
                entered.set()
                assert release.wait(5)
            return original(envelope, timeout=timeout)
        leaf.request = request
        return leaf
    host, created, _, _ = _with_monitor(tmp_path, monkeypatch, factory=factory)
    try:
        host.start()
        assert _wait(lambda: host._atomic_monitor_owners and host._atomic_monitor_owners[0].resource
                     and host._atomic_monitor_owners[0].resource.initialized)
        assert _registered_submit(host, parent_payload())['ok']
        assert entered.wait(2)
        # The RPC is outside the journal read transaction; another strategy
        # request can be accepted while the fake Monitor transport is blocked.
        request = parent_payload()
        request['request_id'] = 'second'
        request['spec'].update(parent_ref='parent-second', registration_operation_id='gateway-second',
                               risk_operation_id='risk-second')
        binding = host._registry._db.execute("SELECT registration_id,service_epoch FROM sessions WHERE port='service.execution_parent'").fetchone()
        result = call(host, SUBMIT, {'registration_id': binding['registration_id'],
            'service_epoch': binding['service_epoch'], 'instance_id': 'exec-a', 'arguments': request})
        assert result['ok'], result
        with pytest.raises(ServiceHostError, match='CLOSE_FAILED'):
            host.close()
        assert host.state == 'close_failed' and not created['journal']._closed
        assert host._server is None and host._atomic_monitor_owners
        assert 'atomic_monitor_cleanup_unknown' in host.summary['cleanup_failures']
    finally:
        release.set()
        if host.state not in ('closed', 'close_failed'):
            try:
                host.close()
            except ServiceHostError:
                pass
        if host.state != 'closed':
            assert _wait(lambda: not host._atomic_monitor_owners[0].owner._thread.is_alive())
            host.close()
    assert created['journal']._closed


def test_unavailable_atomic_remains_unready_with_monitor(tmp_path, monkeypatch):
    host, _, _, _ = _with_monitor(tmp_path, monkeypatch, simulate_availability=False)
    try:
        host.start()
        assert not host.healthy and not host._registry.ready
    finally:
        host.close()


def test_duplicate_canonical_stream_rejected_even_with_other_day(tmp_path, monkeypatch):
    host, _, _, _ = _with_monitor(tmp_path, monkeypatch)
    selected = host._trusted_atomic_monitors[0]
    other = replace(selected, source_scope=replace(selected.source_scope, trading_day='next-day'),
                    checkpoint_path=tmp_path / 'different.sqlite')
    with pytest.raises(ServiceHostError, match='INVALID_ATOMIC_MONITOR_CONFIGURATION'):
        ServiceHost(host._config, host._authority, host._registry_path, host._resource_provider,
                    trusted_atomic_monitors=(selected, other))


def test_open_read_snapshot_does_not_hold_writer_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from bt_api_execution.isolated_local_parent_events import IsolatedLocalParentEventReader

    entered, release = threading.Event(), threading.Event()
    original = IsolatedLocalParentEventReader._snapshot
    paused = [False]

    @contextmanager
    def snapshot(reader):
        with original(reader) as pair:
            if not paused[0]:
                paused[0] = True
                pair[0].execute('SELECT count(*) FROM sqlite_master').fetchone()
                entered.set()
                assert release.wait(5)
            yield pair

    monkeypatch.setattr(IsolatedLocalParentEventReader, '_snapshot', snapshot)
    host, _, _, _ = _with_monitor(tmp_path, monkeypatch)
    try:
        host.start()
        assert entered.wait(2)
        result = _registered_submit(host, parent_payload())
        assert result['ok'] and result['result']['status'] == 'RECEIVED', result
    finally:
        release.set()
        host.close()


def test_observer_hold_busy_retries_without_gating_host(tmp_path, monkeypatch):
    from bt_api_execution.account_journal_host import AccountJournalHost, AccountJournalHostError

    original = AccountJournalHost.activate_observer
    attempts = [0]
    def fail_once(journal, hold):
        attempts[0] += 1
        if attempts[0] == 1:
            raise AccountJournalHostError('HOST_UNAVAILABLE')
        return original(journal, hold)
    monkeypatch.setattr(AccountJournalHost, 'activate_observer', fail_once)
    host, _, _, _ = _with_monitor(tmp_path, monkeypatch)
    try:
        host.start()
        assert host.healthy and host._registry.ready
        assert _wait(lambda: host._atomic_monitor_owners
                     and host._atomic_monitor_owners[0].owner.status().outcome == 'FAILED')
        assembly = host._atomic_monitor_owners[0]
        failed_owner = assembly.owner
        assert failed_owner.status().cleanup == 'CONFIRMED'
        result = _registered_submit(host, parent_payload())
        assert result['ok'] and result['result']['status'] == 'RECEIVED'
        host._atomic_monitor_next_attempt = 0
        host.serve_once(0)
        assert _wait(lambda: assembly.owner is not failed_owner and assembly.resource
                     and assembly.resource.initialized)
        assert attempts[0] >= 2
        state, = host.summary['atomic_monitor']['states']
        assert state['outcome'] == 'RUNNING' and state['fault'] is None
        assert 'MONITOR_UNAVAILABLE' in host.summary['atomic_monitor']['fault_history']
    finally:
        host.close()


def test_gap_stays_faulted_without_restarting_or_moving_checkpoint(tmp_path, monkeypatch):
    from bt_api_py.service_local_parent_monitor import LocalParentMonitorRelay, LocalParentRelayError

    calls = [0]
    def gap(_relay):
        calls[0] += 1
        raise LocalParentRelayError('GAP')
    monkeypatch.setattr(LocalParentMonitorRelay, 'poll', gap)
    host, _, _, transports = _with_monitor(tmp_path, monkeypatch)
    try:
        host.start()
        assert _wait(lambda: host._atomic_monitor_owners and
                     host._atomic_monitor_owners[0].owner.status().outcome == 'FAILED')
        assembly = host._atomic_monitor_owners[0]
        assert assembly.resource.fault_code == 'GAP'
        assert assembly.owner.status().cleanup == 'CONFIRMED'
        host._atomic_monitor_next_attempt = 0
        host.serve_once(0)
        assert calls[0] == 1 and len(transports) == 1
        assert host.healthy and host._registry.ready
    finally:
        host.close()


def test_reader_close_failure_keeps_owner_and_retries_on_same_thread(tmp_path, monkeypatch):
    from bt_api_execution.isolated_local_parent_events import IsolatedLocalParentEventReader

    allowed = threading.Event()
    original = IsolatedLocalParentEventReader.close
    threads = []
    def close(reader):
        threads.append(threading.get_ident())
        if not allowed.is_set():
            raise RuntimeError('synthetic close failure')
        return original(reader)
    monkeypatch.setattr(IsolatedLocalParentEventReader, 'close', close)
    host, created, _, _ = _with_monitor(tmp_path, monkeypatch)
    try:
        host.start()
        assert _wait(lambda: host._atomic_monitor_owners and host._atomic_monitor_owners[0].resource
                     and host._atomic_monitor_owners[0].resource.initialized)
        with pytest.raises(ServiceHostError, match='CLOSE_FAILED'):
            host.close()
        assert host.state == 'close_failed' and not created['journal']._closed
        assert host._atomic_monitor_owners[0].owner._thread.is_alive()
        owner = host._atomic_monitor_owners[0].owner
        allowed.set()
        # Each close explicitly wakes cleanup on the original owner thread.
        try:
            host.close()
        except ServiceHostError:
            pass
        assert _wait(lambda: not owner._thread.is_alive())
        if host.state != 'closed':
            host.close()
        assert len(threads) >= 2 and len(set(threads)) == 1
        assert created['journal']._closed
    finally:
        allowed.set()
        if host.state != 'closed':
            try:
                host.close()
            except ServiceHostError:
                pass


def test_reader_is_closed_when_sink_construction_fails(tmp_path, monkeypatch):
    from bt_api_execution.isolated_local_parent_events import IsolatedLocalParentEventReader
    from bt_api_py.service_monitor import ServiceMonitorOutboxSink

    original_init = ServiceMonitorOutboxSink.__init__
    original_close = IsolatedLocalParentEventReader.close
    fail_once, closed = [True], []
    def sink_init(sink, *args, **kwargs):
        if fail_once[0]:
            fail_once[0] = False
            raise RuntimeError('synthetic sink construction failure')
        original_init(sink, *args, **kwargs)
    def close(reader):
        original_close(reader)
        closed.append(threading.get_ident())
    monkeypatch.setattr(ServiceMonitorOutboxSink, '__init__', sink_init)
    monkeypatch.setattr(IsolatedLocalParentEventReader, 'close', close)
    host, _, _, _ = _with_monitor(tmp_path, monkeypatch)
    try:
        host.start()
        assert _wait(lambda: host._atomic_monitor_owners and
                     host._atomic_monitor_owners[0].owner.status().outcome == 'FAILED')
        assert closed and host._atomic_monitor_owners[0].owner.status().cleanup == 'CONFIRMED'
        assert host.healthy and host._registry.ready
        host._atomic_monitor_next_attempt = 0
        host.serve_once(0)
        assert _wait(lambda: host._atomic_monitor_owners[0].resource.initialized)
    finally:
        host.close()


def test_untrusted_domain_does_not_start_observer(tmp_path, monkeypatch):
    base, _, _, _ = _with_monitor(tmp_path, monkeypatch)
    selected = base._trusted_atomic_monitors[0]
    invalid = replace(selected, wire_scope=replace(selected.wire_scope, execution_domain_ref='other-domain'))
    host = ServiceHost(base._config, base._authority, base._registry_path, base._resource_provider,
                       trusted_atomic_monitors=(invalid,))
    try:
        host.start()
        assert host.healthy and host._registry.ready
        assert not host._atomic_monitor_owners
        assert 'MONITOR_START_FAILED' in host.summary['atomic_monitor']['fault_history']
    finally:
        host.close()


def test_corruption_during_reader_construction_is_not_retried(tmp_path, monkeypatch):
    from bt_api_execution.isolated_local_parent_events import IsolatedLocalParentEventReader
    from bt_api_execution.local_parent_events import LocalParentEventError

    calls = [0]
    def invalid(reader, *_args):
        calls[0] += 1
        reader._broken = True
        raise LocalParentEventError('JOURNAL_INVALID')
    monkeypatch.setattr(IsolatedLocalParentEventReader, '_check', invalid)
    host, _, _, transports = _with_monitor(tmp_path, monkeypatch)
    try:
        host.start()
        assert _wait(lambda: host._atomic_monitor_owners and
                     host._atomic_monitor_owners[0].owner.status().outcome == 'FAILED')
        assembly = host._atomic_monitor_owners[0]
        assert assembly.resource.fault_code == 'SOURCE_CORRUPT'
        assert assembly.owner.status().cleanup == 'CONFIRMED'
        host._atomic_monitor_next_attempt = 0
        host.serve_once(0)
        assert calls[0] == 1 and len(transports) == 1
        assert 'MONITOR_SOURCE_CORRUPT' in host.summary['atomic_monitor']['fault_history']
    finally:
        host.close()


def test_corrupt_event_stops_without_restart_or_checkpoint_advance(tmp_path, monkeypatch):
    import sqlite3
    from bt_api_execution.local_parent_events import _key, _stream
    from bt_api_py.service_local_parent_monitor import LocalParentMonitorRelay

    def checkpoints():
        with sqlite3.connect(tmp_path / 'monitor-checkpoint.sqlite') as db:
            return db.execute('SELECT * FROM relay_checkpoints ORDER BY namespace_id').fetchall()

    entered, release = threading.Event(), threading.Event()
    original = LocalParentMonitorRelay.poll
    def paused(relay):
        entered.set()
        assert release.wait(5)
        return original(relay)
    monkeypatch.setattr(LocalParentMonitorRelay, 'poll', paused)
    host, created, remote, transports = _with_monitor(tmp_path, monkeypatch)
    try:
        host.start()
        assert entered.wait(2)
        before = checkpoints()
        assert _registered_submit(host, parent_payload())['ok']
        scope = host._trusted_atomic_monitors[0].source_scope
        _, prefix = _stream(scope.key)
        with created['journal'].store._transaction() as cursor:
            cursor.execute('UPDATE execution_meta SET value=? WHERE key=?', ('{}', _key(prefix, 1)))
            assert cursor.rowcount == 1
        release.set()
        assert _wait(lambda: host._atomic_monitor_owners[0].owner.status().outcome == 'FAILED')
        assembly = host._atomic_monitor_owners[0]
        assert assembly.resource.fault_code == 'SOURCE_CORRUPT'
        assert assembly.owner.status().cleanup == 'CONFIRMED'
        host._atomic_monitor_next_attempt = 0
        host.serve_once(0)
        assert len(transports) == 1 and not remote.append_attempts
        assert checkpoints() == before
        assert 'MONITOR_SOURCE_CORRUPT' in host.summary['atomic_monitor']['fault_history']
    finally:
        release.set()
        host.close()


def test_busy_reader_restarts_after_confirmed_cleanup(tmp_path, monkeypatch):
    from bt_api_execution.isolated_local_parent_events import IsolatedLocalParentEventReader
    from bt_api_execution.local_parent_events import LocalParentEventError

    original = IsolatedLocalParentEventReader.page
    fail_once = [True]
    def page(reader, **kwargs):
        if fail_once[0]:
            fail_once[0] = False
            raise LocalParentEventError('SOURCE_BUSY')
        return original(reader, **kwargs)
    monkeypatch.setattr(IsolatedLocalParentEventReader, 'page', page)
    host, _, remote, transports = _with_monitor(tmp_path, monkeypatch)
    try:
        host.start()
        assert _wait(lambda: host._atomic_monitor_owners and
                     host._atomic_monitor_owners[0].owner.status().outcome == 'FAILED')
        assembly = host._atomic_monitor_owners[0]
        assert assembly.resource.fault_code == 'SOURCE_UNAVAILABLE'
        assert assembly.owner.status().cleanup == 'CONFIRMED'
        assert _registered_submit(host, parent_payload())['ok']
        host._atomic_monitor_next_attempt = 0
        host.serve_once(0)
        assert _wait(lambda: len(remote.events) == 1)
        assert len(transports) == 2 and host.healthy and host._registry.ready
    finally:
        host.close()


@pytest.mark.parametrize('code', ['synthetic-sensitive-code', 'GAP', 'SOURCE_CHANGED'])
def test_untrusted_runtime_failure_has_finite_diagnostic(tmp_path, monkeypatch, code):
    from bt_api_py.service_runtime import ServiceRuntimeError

    def factory(_leaf, _number):
        raise ServiceRuntimeError(code)
    host, _, _, _ = _with_monitor(tmp_path, monkeypatch, factory=factory)
    try:
        host.start()
        assert _wait(lambda: host._atomic_monitor_owners and
                     host._atomic_monitor_owners[0].owner.status().outcome == 'FAILED')
        state, = host.summary['atomic_monitor']['states']
        assert state['fault'] == 'MONITOR_UNAVAILABLE'
        assert host._atomic_monitor_owners[0].owner.status().cleanup == 'CONFIRMED'
        assert host.healthy and host._registry.ready
        if code == 'synthetic-sensitive-code':
            assert code not in repr(host.summary)
    finally:
        host.close()


def test_observer_fault_histories_remain_scoped_to_selection(tmp_path, monkeypatch):
    base, _, _, _ = _with_monitor(tmp_path, monkeypatch)
    good = base._trusted_atomic_monitors[0]
    bad_scope = replace(good.source_scope, strategy_id='other-strategy')
    bad_wire = replace(good.wire_scope, strategy_ref='other-strategy')
    bad = replace(good, source_scope=bad_scope, wire_scope=bad_wire,
                  checkpoint_path=tmp_path / 'bad-checkpoint.sqlite')
    host = ServiceHost(base._config, base._authority, base._registry_path, base._resource_provider,
                       trusted_atomic_monitors=(good, bad))
    try:
        host.start()
        assert _wait(lambda: host._atomic_monitor_owners and host._atomic_monitor_owners[0].resource
                     and host._atomic_monitor_owners[0].resource.initialized)
        summary = host.summary['atomic_monitor']
        assert summary['pending_selections'] == (1,)
        assert summary['selection_history'] == ((), ('MONITOR_START_FAILED',))
        state, = summary['states']
        assert state['selection'] == 0 and state['fault'] is None and state['outcome'] == 'RUNNING'
        host._atomic_monitor_next_attempt = 0
        host.serve_once(0)
        assert host.summary['atomic_monitor']['selection_history'] == ((), ('MONITOR_START_FAILED',))
    finally:
        host.close()


def test_observer_lifetime_lock_does_not_block_submit_and_close_is_bounded(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    host, created, _, _ = _with_monitor(tmp_path, monkeypatch)
    holder = None
    try:
        host.start()
        assert _wait(lambda: host._atomic_monitor_owners and host._atomic_monitor_owners[0].resource
                     and host._atomic_monitor_owners[0].resource.initialized)
        journal = created['journal']
        def hold_observer_lock():
            with journal._observer_lock:
                entered.set()
                assert release.wait(5)
        holder = threading.Thread(target=hold_observer_lock)
        holder.start()
        assert entered.wait(2)
        result = _registered_submit(host, parent_payload())
        assert result['ok'] and result['result']['status'] == 'RECEIVED', result
        with pytest.raises(ServiceHostError, match='CLOSE_FAILED'):
            host.close()
        assert host.state == 'close_failed' and not journal._closed
        assert host._atomic_monitor_owners
    finally:
        release.set()
        if holder is not None:
            holder.join(2)
        if host.state != 'closed':
            try:
                host.close()
            except ServiceHostError:
                assert _wait(lambda: not host._atomic_monitor_owners[0].owner._thread.is_alive())
                host.close()
    assert created['journal']._closed
