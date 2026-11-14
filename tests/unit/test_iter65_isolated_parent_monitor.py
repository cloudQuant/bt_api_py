"""Isolated WAL reader with the real local Monitor ingress; synthetic RPC only."""
import os
import shutil
import sqlite3
import threading
from dataclasses import replace

import pytest

from bt_api_base.service_protocol import WireScope
from bt_api_execution.account_journal_host import AccountJournalHostError
from bt_api_execution.isolated_local_parent_events import IsolatedLocalParentEventReader
from bt_api_execution.local_parent_events import _key, _stream
from bt_api_execution.contracts import ExecutionScope
from bt_api_py.execution_parent_grant import SUBMIT
from bt_api_py.service_local_parent_monitor import LocalParentMonitorRelay, LocalParentRelayError
from bt_api_py.service_local_parent_monitor_sink import (
    LOCAL_PARENT_EVENT_TYPE, LOCAL_PARENT_OUTBOX_SCOPE, LocalParentMonitorBinding,
    LocalParentMonitorServiceSink, LocalParentMonitorSinkError,
    create_local_parent_service_relay,
)
from bt_api_py.service_local_parent_monitor_owner import (
    LocalParentMonitorCleanup, LocalParentMonitorRuntimeResource,
)
from bt_api_py.service_monitor import ServiceMonitorOutboxSink
from test_iter65_atomic_submission_intake import atomic
from tests.unit.test_iter65_local_parent_monitor_sink import IngressRemote
from tests.unit.test_service_monitor_outbox import _runtime, _SCOPE_REF


def _wire(target):
    return WireScope('configured-domain', account_ref=target.account_ref,
                     strategy_ref=target.strategy_id, run_id='synthetic-run')


def _build(rig, tmp_path, *, remote=None):
    wire = _wire(rig.target)
    reader = IsolatedLocalParentEventReader(host=rig.host, scope=rig.target)
    remote = remote or IngressRemote(tmp_path / 'monitor.sqlite', wire)
    runtime = None
    try:
        runtime, transports, _ = _runtime(remote, scope=wire)
        sink = ServiceMonitorOutboxSink(runtime, scope_ref=_SCOPE_REF,
                                        outbox_scope=LOCAL_PARENT_OUTBOX_SCOPE)
        relay = create_local_parent_service_relay(
            reader=reader, sink=sink, binding=LocalParentMonitorBinding(rig.target, wire),
            checkpoint_path=tmp_path / 'checkpoint.sqlite')
        return reader, runtime, transports[0], relay, remote
    except BaseException:
        if runtime is not None:
            runtime.close()
        reader.close()
        raise


def test_real_ingress_has_no_read_snapshot_during_rpc_and_releases_hold(atomic, tmp_path):
    rig, _, _ = atomic
    reader, runtime, transport, relay, remote = _build(rig, tmp_path)
    try:
        assert rig.call(SUBMIT, rig.parent())['ok']
        original = transport.request
        snapshots = []
        def request(envelope, *, timeout):
            if envelope.header.operation == 'events.append':
                snapshots.append(reader._connection.in_transaction)
            return original(envelope, timeout=timeout)
        transport.request = request
        assert relay.poll() == (1, 1)
        assert snapshots == [False]
        rows, more = remote.outbox.read_page_types('monitor:local-parent',
                                                   (LOCAL_PARENT_EVENT_TYPE,), 0, 10)
        assert not more and len(rows) == 1
        assert rows[0].event.data['payload']['observation'] == 'LOCAL_RECEIVED'
        with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
            rig.host.close()
        relay.close()
        assert reader._connection is None and reader._hold is None
        relay.close()  # idempotent for the owned reader
    finally:
        if not reader._closed:
            reader.close()
        runtime.close()
    assert not rig.host._service_holds


def test_paused_reader_snapshot_does_not_hold_store_lock_for_submit(atomic, tmp_path):
    rig, _, _ = atomic
    ready, release = threading.Event(), threading.Event()
    errors, results = [], []

    def observe():
        reader = runtime = relay = None
        try:
            reader, runtime, transport, relay, remote = _build(rig, tmp_path)
            with reader._snapshot() as (cursor, _):
                before = cursor.execute('SELECT COUNT(*) FROM execution_parent_records').fetchone()[0]
                ready.set()
                if not release.wait(5):
                    raise AssertionError('snapshot wait timed out')
                during = cursor.execute('SELECT COUNT(*) FROM execution_parent_records').fetchone()[0]
            original = transport.request
            def request(envelope, *, timeout):
                if envelope.header.operation == 'events.append':
                    assert not reader._connection.in_transaction
                return original(envelope, timeout=timeout)
            transport.request = request
            results.append((before, during, relay.poll()))
            rows, _ = remote.outbox.read_page_types('monitor:local-parent',
                                                    (LOCAL_PARENT_EVENT_TYPE,), 0, 10)
            results.append(len(rows))
        except BaseException as error:
            errors.append(error)
        finally:
            if relay is not None:
                relay.close()
            elif reader is not None:
                reader.close()
            if runtime is not None:
                runtime.close()

    thread = threading.Thread(target=observe)
    thread.start()
    try:
        assert ready.wait(5)
        accepted = rig.call(SUBMIT, rig.parent())
        assert accepted['ok'] and accepted['result']['status'] == 'RECEIVED'
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not errors
    assert results == [(0, 0, (1, 1)), 1]
    assert not rig.host._service_holds


def test_close_on_wrong_thread_and_failed_connection_close_retain_hold(atomic, tmp_path):
    rig, _, _ = atomic
    reader, runtime, _, relay, _ = _build(rig, tmp_path)
    errors = []
    def wrong_thread():
        try:
            relay.close()
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=wrong_thread)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], LocalParentRelayError)
    with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
        rig.host.close()
    connection = reader._connection
    class FailOnce:
        failed = False
        def close(self):
            if not self.failed:
                self.failed = True
                raise sqlite3.Error('synthetic close failure')
            connection.close()
    reader._connection = FailOnce()
    owned = LocalParentMonitorRuntimeResource(runtime=runtime, relay=relay)
    assert owned.close() is None
    with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
        rig.host.close()
    assert owned.close() is LocalParentMonitorCleanup.CONFIRMED
    assert not rig.host._service_holds


def test_broken_source_does_not_prevent_owner_thread_cleanup(atomic, tmp_path):
    rig, store, _ = atomic
    reader, runtime, _, relay, _ = _build(rig, tmp_path)
    path, backup = str(store.path), str(store.path) + '.backup'
    try:
        os.rename(path, backup)
        shutil.copyfile(backup, path)
        os.chmod(path, 0o600)
        with pytest.raises(LocalParentRelayError, match='SOURCE_UNAVAILABLE'):
            relay.poll()
        os.unlink(path)
        os.rename(backup, path)
        # Cleanup must not demand another successful source read after the
        # reader has latched its failure.
        relay.close()
        assert reader._closed and not rig.host._service_holds
    finally:
        if os.path.exists(backup):
            if os.path.exists(path):
                os.unlink(path)
            os.rename(backup, path)
        if not reader._closed:
            reader.close()
        runtime.close()


def test_stream_change_keeps_checkpoint_and_does_not_replay_new_stream(atomic, tmp_path):
    rig, store, _ = atomic
    reader, runtime, _, relay, remote = _build(rig, tmp_path)
    try:
        assert rig.call(SUBMIT, rig.parent())['ok']
        assert relay.poll() == (1, 1)
        initial = relay.checkpoint()
        scope = ExecutionScope(rig.target.provider, rig.target.environment,
                               rig.target.account_ref, rig.target.strategy_id).key
        meta, prefix = _stream(scope)
        with store._transaction() as cursor:
            cursor.execute('DELETE FROM execution_meta WHERE key=?', (meta,))
            cursor.execute('DELETE FROM execution_meta WHERE key=?', (_key(prefix, 1),))
        assert rig.call(SUBMIT, rig.parent(request_id='new-request', parent_ref='new-parent'))['ok']
        with pytest.raises(LocalParentRelayError, match='STREAM_CHANGED'):
            relay.poll()
        assert relay.checkpoint() == initial
        assert len(remote.attempts) == 1
    finally:
        relay.close()
        runtime.close()


def test_retention_gap_keeps_checkpoint_and_never_skips_events(atomic, tmp_path, monkeypatch):
    import bt_api_execution.local_parent_events as events
    rig, _, _ = atomic
    monkeypatch.setattr(events, '_RETENTION', 2)
    reader, runtime, _, relay, remote = _build(rig, tmp_path)
    try:
        assert rig.call(SUBMIT, rig.parent())['ok']
        assert relay.poll() == (1, 1)
        checkpoint = relay.checkpoint()
        for number in (2, 3, 4):
            assert rig.call(SUBMIT, rig.parent(request_id=f'request-{number}',
                                               parent_ref=f'parent-{number}'))['ok']
        with pytest.raises(LocalParentRelayError, match='GAP'):
            relay.poll()
        assert relay.checkpoint() == checkpoint and len(remote.attempts) == 1
    finally:
        relay.close()
        runtime.close()


def test_failed_relay_construction_leaves_reader_with_caller(atomic, tmp_path):
    rig, _, _ = atomic
    wire = _wire(rig.target)
    reader = IsolatedLocalParentEventReader(host=rig.host, scope=rig.target)
    runtime, _, _ = _runtime(IngressRemote(tmp_path / 'monitor.sqlite', wire), scope=wire)
    try:
        sink = ServiceMonitorOutboxSink(runtime, scope_ref=_SCOPE_REF,
                                        outbox_scope=LOCAL_PARENT_OUTBOX_SCOPE)
        with pytest.raises(LocalParentRelayError):
            create_local_parent_service_relay(
                reader=reader, sink=sink, binding=LocalParentMonitorBinding(rig.target, wire),
                checkpoint_path=rig.host.store.path)
        with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
            rig.host.close()
    finally:
        reader.close()
        runtime.close()
    assert not rig.host._service_holds


def test_isolated_reader_has_one_relay_owner(atomic, tmp_path):
    rig, _, _ = atomic
    reader, runtime, _, relay, _ = _build(rig, tmp_path)
    try:
        with pytest.raises(LocalParentRelayError, match='CONFIGURATION_INVALID'):
            LocalParentMonitorRelay(reader=reader, scope=rig.target, sink=relay._sink,
                                    checkpoint_path=tmp_path / 'second.sqlite')
        assert rig.call(SUBMIT, rig.parent())['ok']
        assert relay.poll() == (1, 1)
    finally:
        relay.close()
        runtime.close()
    assert not rig.host._service_holds


@pytest.mark.parametrize('trigger', ['sink', 'checkpoint', 'generation'])
def test_reentrant_construction_cannot_share_reader(atomic, tmp_path, monkeypatch, trigger):
    import bt_api_py.service_local_parent_monitor as module
    rig, _, _ = atomic
    reader = IsolatedLocalParentEventReader(host=rig.host, scope=rig.target)
    attempts = []

    class Sink:
        @property
        def consumer_id(self):
            if trigger == 'sink' and not attempts:
                nested()
            return 'synthetic-consumer'
        def __call__(self, _event):
            raise AssertionError('no event delivery expected')

    sink = Sink()
    def nested():
        # Set the marker before recursion so the sink remains well behaved.
        attempts.append(None)
        with pytest.raises(LocalParentRelayError, match='CONFIGURATION_INVALID'):
            LocalParentMonitorRelay(reader=reader, scope=rig.target, sink=sink,
                                    checkpoint_path=tmp_path / 'inner.sqlite')

    if trigger == 'checkpoint':
        original = module._initialize_or_validate_checkpoint
        def checkpoint(path):
            if not attempts:
                nested()
            return original(path)
        monkeypatch.setattr(module, '_initialize_or_validate_checkpoint', checkpoint)
    elif trigger == 'generation':
        original = reader.generation
        def generation():
            if not attempts:
                nested()
            return original()
        monkeypatch.setattr(reader, 'generation', generation)
    try:
        relay = LocalParentMonitorRelay(reader=reader, scope=rig.target, sink=sink,
                                        checkpoint_path=tmp_path / 'outer.sqlite')
        assert len(attempts) == 1
        relay.close()
        assert reader._closed
    finally:
        if not reader._closed:
            reader.close()
    assert not rig.host._service_holds


@pytest.mark.parametrize('interrupted', [False, True])
def test_failed_construction_releases_reservation_but_not_reader(atomic, tmp_path, interrupted):
    rig, _, _ = atomic
    reader = IsolatedLocalParentEventReader(host=rig.host, scope=rig.target)
    class BadSink:
        @property
        def consumer_id(self):
            if interrupted:
                raise KeyboardInterrupt
            raise ValueError('synthetic failure')
        def __call__(self, _event):
            pass
    try:
        with pytest.raises(KeyboardInterrupt if interrupted else LocalParentRelayError):
            LocalParentMonitorRelay(reader=reader, scope=rig.target, sink=BadSink(),
                                    checkpoint_path=tmp_path / 'bad.sqlite')
        assert getattr(reader, '_monitor_relay_owner', None) is None
        assert reader.generation()
        with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
            rig.host.close()
    finally:
        reader.close()
    assert not rig.host._service_holds


def test_only_exact_reader_types_are_accepted(atomic, tmp_path):
    rig, _, _ = atomic
    reader, runtime, _, relay, _ = _build(rig, tmp_path)
    class Impostor(IsolatedLocalParentEventReader):
        pass
    try:
        imposter = object.__new__(Impostor)
        imposter._scope = rig.target
        binding = LocalParentMonitorBinding(rig.target, _wire(rig.target))
        sink = ServiceMonitorOutboxSink(runtime, scope_ref=_SCOPE_REF,
                                        outbox_scope=LOCAL_PARENT_OUTBOX_SCOPE)
        with pytest.raises(LocalParentMonitorSinkError, match='INVALID_CONFIGURATION'):
            LocalParentMonitorServiceSink(sink=sink, binding=binding, reader=imposter)
        with pytest.raises(LocalParentRelayError, match='CONFIGURATION_INVALID'):
            LocalParentMonitorRelay(reader=imposter, scope=rig.target, sink=relay._sink,
                                    checkpoint_path=tmp_path / 'other.sqlite')
        with pytest.raises(LocalParentMonitorSinkError, match='INVALID_CONFIGURATION'):
            LocalParentMonitorServiceSink(sink=sink,
                binding=LocalParentMonitorBinding(replace(rig.target, strategy_id='other'),
                                                   replace(_wire(rig.target), strategy_ref='other')),
                reader=reader)
    finally:
        relay.close()
        runtime.close()
