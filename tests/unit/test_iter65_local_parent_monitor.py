"""Synthetic journal and sink only; no socket, Monitor or account operations."""
from dataclasses import replace
import threading

import pytest

from bt_api_execution.local_parent_events import LocalParentEventReader
import bt_api_execution.local_parent_events as source_module
from bt_api_py.execution_parent_grant import SUBMIT
import bt_api_py.service_local_parent_monitor as module
from bt_api_py.service_local_parent_monitor import (
    LocalParentMonitorAck, LocalParentMonitorRelay, LocalParentRelayError,
)
from test_iter65_atomic_submission_intake import atomic


class Sink:
    consumer_id = 'synthetic-monitor'

    def __init__(self):
        self.events = []
        self.mode = 'ack'

    def __call__(self, event):
        self.events.append(event)
        if self.mode == 'unknown':
            raise TimeoutError('lost reply')
        if self.mode == 'invalid':
            return None
        return LocalParentMonitorAck(self.consumer_id, event.event_id, event.fingerprint)


def create(rig, tmp_path, sink=None, *, scope=None, name='checkpoint.sqlite'):
    scope = scope or rig.target
    reader = LocalParentEventReader(host=rig.host, scope=scope)
    return LocalParentMonitorRelay(reader=reader, scope=scope, sink=sink or Sink(),
                                   checkpoint_path=tmp_path / name)


def submit(rig, i):
    result = rig.call(SUBMIT, rig.parent(request_id=f'request-{i}', parent_ref=f'parent-{i}'))
    assert result['ok']


def test_delivery_scope_minimization_paging_and_restart(atomic, tmp_path):
    rig, _, _ = atomic
    sink = Sink()
    relay = create(rig, tmp_path, sink)
    assert relay.poll() == (0, 0)
    submit(rig, 1)
    submit(rig, 2)
    assert relay.poll(limit=1) == (1, 1)
    event = sink.events[0]
    assert event.event_type == 'LOCAL_RECEIVED' and event.coverage == 'FROM_ACTIVATION'
    assert set(event.__slots__) == {'event_id', 'fingerprint', 'sequence', 'occurred_at_ns', 'event_type', 'coverage'}
    assert 'parent-' not in repr(event) and rig.target.account_ref not in repr(event)
    relay.close()
    resumed = create(rig, tmp_path, sink)
    assert resumed.poll() == (1, 1)
    assert [event.sequence for event in sink.events] == [1, 2]
    assert resumed.poll() == (0, 0)


@pytest.mark.parametrize('mode', ['unknown', 'invalid'])
def test_unconfirmed_receipt_replays_same_event(atomic, tmp_path, mode):
    rig, _, _ = atomic
    submit(rig, 1)
    sink = Sink()
    sink.mode = mode
    relay = create(rig, tmp_path, sink)
    with pytest.raises(LocalParentRelayError, match='REMOTE_DELIVERY_UNKNOWN'):
        relay.poll()
    assert relay.checkpoint().sequence == 0
    old = sink.events[0]
    sink.mode = 'ack'
    assert relay.poll() == (1, 1)
    assert sink.events[1] == old


def test_checkpoint_failure_replays_same_confirmed_event(atomic, tmp_path, monkeypatch):
    rig, _, _ = atomic
    submit(rig, 1)
    sink = Sink()
    relay = create(rig, tmp_path, sink)
    original = module._advance_checkpoint
    def fail(*args, **kwargs):
        raise module._CheckpointFailureError('LOCAL_CHECKPOINT_FAILED')
    monkeypatch.setattr(module, '_advance_checkpoint', fail)
    with pytest.raises(LocalParentRelayError, match='LOCAL_CHECKPOINT_FAILED') as error:
        relay.poll()
    assert (error.value.delivered, error.value.checkpointed) == (1, 0)
    monkeypatch.setattr(module, '_advance_checkpoint', original)
    assert relay.poll() == (1, 1)
    assert sink.events[0] == sink.events[1]


def test_gap_does_not_skip_or_forward(atomic, tmp_path, monkeypatch):
    rig, _, _ = atomic
    monkeypatch.setattr(source_module, '_RETENTION', 2)
    sink = Sink()
    relay = create(rig, tmp_path, sink)
    for i in range(1, 4):
        submit(rig, i)
    with pytest.raises(LocalParentRelayError, match='GAP'):
        relay.poll()
    assert relay.checkpoint().sequence == 0 and sink.events == []


def test_generation_and_stream_change_do_not_advance(atomic, tmp_path, monkeypatch):
    rig, store, _ = atomic
    submit(rig, 1)
    sink = Sink()
    relay = create(rig, tmp_path, sink)
    assert relay.poll() == (1, 1)
    original = LocalParentEventReader.generation
    monkeypatch.setattr(LocalParentEventReader, 'generation', lambda self: 'f' * 32)
    with pytest.raises(LocalParentRelayError, match='GENERATION_CHANGED'):
        relay.poll()
    monkeypatch.setattr(LocalParentEventReader, 'generation', original)
    scope_key = relay._scope_key
    meta, prefix = source_module._stream(scope_key)
    with store._transaction() as cursor:
        cursor.execute('DELETE FROM execution_meta WHERE key IN (?, ?)', (meta, source_module._key(prefix, 1)))
    submit(rig, 2)
    with pytest.raises(LocalParentRelayError, match='STREAM_CHANGED'):
        relay.poll()
    assert relay.checkpoint().sequence == 1
    assert len(sink.events) == 1


def test_other_strategy_and_wrong_scope_not_forwarded(atomic, tmp_path):
    rig, _, _ = atomic
    submit(rig, 1)
    other = replace(rig.target, strategy_id='strategy-b')
    sink = Sink()
    relay = create(rig, tmp_path, sink, scope=other)
    assert relay.poll() == (0, 0) and not sink.events
    reader = LocalParentEventReader(host=rig.host, scope=other)
    with pytest.raises(LocalParentRelayError, match='CONFIGURATION_INVALID'):
        LocalParentMonitorRelay(reader=reader, scope=rig.target, sink=sink,
                                checkpoint_path=tmp_path / 'other.sqlite')


def test_source_change_after_ack_does_not_checkpoint(atomic, tmp_path):
    rig, store, _ = atomic
    submit(rig, 1)
    sink = Sink()
    relay = create(rig, tmp_path, sink)
    original = sink.__class__.__call__
    class DestroyingSink(Sink):
        def __call__(self, event):
            result = original(self, event)
            _, prefix = source_module._stream(relay._scope_key)
            with store._transaction() as cursor:
                cursor.execute('DELETE FROM execution_meta WHERE key=?', (source_module._key(prefix, 1),))
            return result
    relay._sink = DestroyingSink()
    with pytest.raises(LocalParentRelayError) as error:
        relay.poll()
    assert (error.value.delivered, error.value.checkpointed) == (1, 0)
    assert relay.checkpoint().sequence == 0


def test_checkpoint_must_not_be_journal_and_thread_is_owned(atomic, tmp_path):
    rig, _, _ = atomic
    reader = LocalParentEventReader(host=rig.host, scope=rig.target)
    with pytest.raises(LocalParentRelayError, match='CONFIGURATION_INVALID'):
        LocalParentMonitorRelay(reader=reader, scope=rig.target, sink=Sink(), checkpoint_path=rig.host.store.path)
    relay = create(rig, tmp_path)
    errors = []
    def run():
        try:
            relay.poll()
        except LocalParentRelayError as error:
            errors.append(error.code)
    worker = threading.Thread(target=run)
    worker.start()
    worker.join()
    assert errors == ['RELAY_UNAVAILABLE']


def test_competing_relay_exact_ack_is_idempotent(atomic, tmp_path):
    rig, _, _ = atomic
    submit(rig, 1)
    other_sink = Sink()
    other = create(rig, tmp_path, other_sink)
    class RacingSink(Sink):
        def __call__(self, event):
            result = super().__call__(event)
            assert other.poll() == (1, 1)
            return result
    sink = RacingSink()
    relay = create(rig, tmp_path, sink)
    assert relay.poll() == (1, 1)
    assert sink.events[0] == other_sink.events[0]
    assert relay.checkpoint().sequence == 1


def test_overwritten_source_during_ack_is_gap_without_checkpoint(atomic, tmp_path, monkeypatch):
    rig, _, _ = atomic
    monkeypatch.setattr(source_module, '_RETENTION', 1)
    submit(rig, 1)
    class EvictingSink(Sink):
        def __call__(self, event):
            result = super().__call__(event)
            submit(rig, 2)
            return result
    relay = create(rig, tmp_path, EvictingSink())
    with pytest.raises(LocalParentRelayError, match='GAP') as error:
        relay.poll()
    assert (error.value.delivered, error.value.checkpointed) == (1, 0)
    assert relay.checkpoint().sequence == 0


def test_confirmed_anchor_retired_but_next_event_is_contiguous(atomic, tmp_path, monkeypatch):
    rig, _, _ = atomic
    monkeypatch.setattr(source_module, '_RETENTION', 2)
    sink = Sink()
    relay = create(rig, tmp_path, sink)
    submit(rig, 1)
    assert relay.poll() == (1, 1)
    submit(rig, 2)
    submit(rig, 3)
    assert relay.poll() == (2, 2)
    assert relay.checkpoint().sequence == 3
    assert [event.sequence for event in sink.events] == [1, 2, 3]


def test_confirmed_anchor_and_unconfirmed_event_lost_is_gap(atomic, tmp_path, monkeypatch):
    rig, _, _ = atomic
    monkeypatch.setattr(source_module, '_RETENTION', 2)
    sink = Sink()
    relay = create(rig, tmp_path, sink)
    submit(rig, 1)
    assert relay.poll() == (1, 1)
    for i in (2, 3, 4):
        submit(rig, i)
    with pytest.raises(LocalParentRelayError, match='GAP'):
        relay.poll()
    assert relay.checkpoint().sequence == 1
    assert [event.sequence for event in sink.events] == [1]


def test_rebuilt_stream_with_same_contiguous_boundary_is_rejected(atomic, tmp_path, monkeypatch):
    rig, store, _ = atomic
    monkeypatch.setattr(source_module, '_RETENTION', 2)
    sink = Sink()
    relay = create(rig, tmp_path, sink)
    submit(rig, 1)
    assert relay.poll() == (1, 1)
    meta, prefix = source_module._stream(relay._scope_key)
    with store._transaction() as cursor:
        cursor.execute('DELETE FROM execution_meta WHERE key IN (?, ?)',
                       (meta, source_module._key(prefix, 1)))
    for i in (2, 3, 4):
        submit(rig, i)
    with pytest.raises(LocalParentRelayError, match='STREAM_CHANGED'):
        relay.poll()
    assert relay.checkpoint().sequence == 1
    assert len(sink.events) == 1
