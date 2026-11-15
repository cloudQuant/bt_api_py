"""Synthetic journal and sink only; no socket, Monitor or account operations."""
from dataclasses import replace
import json
import sqlite3
import threading
import uuid

import pytest

from bt_api_execution.local_parent_events import LocalParentEventReader
from bt_api_execution.atomic_submission_intake import _key
from bt_api_execution.account_journal_host import AccountJournalHost
from bt_api_execution.contracts import ExecutionScope
from bt_api_execution.isolated_local_parent_events import IsolatedLocalParentEventReader
import bt_api_execution.local_parent_events as source_module
from bt_api_py.execution_parent_grant import PORT, SUBMIT
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
import bt_api_py.service_local_parent_monitor as module
from bt_api_py.service_local_parent_monitor import (
    LocalParentMonitorAck, LocalParentMonitorRelay, LocalParentRelayError,
)
from test_iter65_atomic_submission_intake import AtomicSubmissionIntake, PrototypeHarness, planned
from test_iter65_fresh_submission_intake import Rig, _next_request, _register


@pytest.fixture
def atomic(tmp_path):
    target = ExecutionScope('FAKE-' + uuid.uuid4().hex, 'test', 'account-a', 'strategy-a')
    rig = Rig(tmp_path, target=target)
    original_parent = rig.parent
    rig.parent = lambda **kwargs: planned(original_parent(**kwargs))
    rig.intake.close()
    root = tmp_path / 'account-host'
    root.mkdir(mode=0o700)
    rig.host = AccountJournalHost(root=str(root), scope=rig.target,
                                  storage_id='d9044d18-dc74-4cb4-a574-3f5d8e5ca7a5')
    store = rig.host.store
    lease = rig.host.acquire_intake_lease(rig.target, 'atomic-test', ttl_ns=300_000_000_000)
    rig.intake = PrototypeHarness(AtomicSubmissionIntake(host=rig.host, targets={rig.scope: rig.target},
                                                         lease=lease), rig.registry, rig.scope)
    yield rig, store, lease
    rig.host.close()
    rig.registry.close()


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
    return LocalParentMonitorRelay(reader=reader, scope=scope, wire_scope=rig.scope, sink=sink or Sink(),
                                   checkpoint_path=tmp_path / name)


def submit(rig, i):
    result = rig.call(SUBMIT, rig.parent(request_id=f'request-{i}', parent_ref=f'parent-{i}'))
    assert result['ok']


def submit_other_scope(rig, lease, wire):
    """Submit a second real Atomic request through an independently scoped registry."""
    operations = frozenset({SUBMIT})
    grant = replace(rig.grant, execution_domain_ref=wire.execution_domain_ref,
                    operations=operations)
    resource = ResourceGrant('exec-a', PORT, 'scope-a', replace(wire, run_id=None), operations)
    capability = ServiceCapability('exec-a', PORT, operations, operations)
    registry = ServiceRegistry(':memory:', service_id='synthetic-other', capabilities=(capability,),
        grants={rig.identity.principal_ref: (PrincipalGrant(rig.identity, (resource,)),)},
        policy_version='p', requirements_version='r', lease_seconds=30)
    try:
        registry.configure_execution_parents(grants=(grant,))
        intake = AtomicSubmissionIntake(host=rig.host, targets={wire: rig.target}, lease=lease)
        harness = PrototypeHarness(intake, registry, wire)
        def handler(context, arguments):
            try:
                return harness.submit(context, arguments, grant=grant)
            finally:
                context.dispatch_admission.close()
        registry.register_handler('exec-a', SUBMIT, handler)
        registration = _register(registry, identity=rig.identity, scope=wire,
            principal=rig.identity.principal_ref, instance_id='exec-a', port=PORT,
            scope_ref='scope-a', operations=tuple(operations), key='other-registration')
        assert registration['ok']
        request = rig.parent(request_id='request-2', parent_ref='parent-2')
        request['spec']['execution_domain_ref'] = wire.execution_domain_ref
        result = registry.handle(rig.identity.principal_ref, _next_request(SUBMIT, {
            'registration_id': registration['result']['registration_id'],
            'service_epoch': registration['result']['service_epoch'],
            'instance_id': 'exec-a', 'arguments': request}, identity=rig.identity, scope=wire))
        assert result['ok'], result
    finally:
        registry.close()


@pytest.mark.parametrize('change', ['execution_domain_ref', 'run_id'])
@pytest.mark.parametrize('isolated', [False, True])
def test_real_atomic_mixed_wire_page_is_rejected_without_delivery(atomic, tmp_path, change, isolated):
    rig, _, lease = atomic
    submit(rig, 1)
    other = replace(rig.scope, **{change: 'other-domain' if change == 'execution_domain_ref' else 'other-run'})
    submit_other_scope(rig, lease, other)
    sink = Sink()
    reader = (IsolatedLocalParentEventReader(host=rig.host, scope=rig.target)
              if isolated else LocalParentEventReader(host=rig.host, scope=rig.target))
    relay = LocalParentMonitorRelay(reader=reader, scope=rig.target, wire_scope=rig.scope,
                                    sink=sink, checkpoint_path=tmp_path / 'mixed.sqlite')
    try:
        checkpoint = relay.checkpoint()
        with pytest.raises(LocalParentRelayError, match='SOURCE_SCOPE_MISMATCH'):
            relay.poll()
        assert sink.events == []
        assert relay.checkpoint() == checkpoint
    finally:
        relay.close()


@pytest.mark.parametrize('consumer_id', [Sink.consumer_id, 'another-consumer'])
def test_legacy_checkpoint_row_is_not_migrated_or_reinitialized(atomic, tmp_path, consumer_id):
    rig, _, _ = atomic
    path = tmp_path / 'legacy.sqlite'
    module._initialize_or_validate_checkpoint(path)
    scope_key = LocalParentEventReader(host=rig.host, scope=rig.target)._scope_key
    binding = module._json({'schema': 'sdk.local-parent-monitor.v1',
                            'consumer_id': consumer_id, 'scope_key': scope_key})
    row = (module._sha(binding), binding, 1, 'old-event', 'a' * 64)
    with sqlite3.connect(path) as connection:
        connection.execute('INSERT INTO relay_checkpoints VALUES (?,?,?,?,?)', row)
    with pytest.raises(LocalParentRelayError, match='LEGACY_CHECKPOINT_UNSUPPORTED'):
        create(rig, tmp_path, name='legacy.sqlite')
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT * FROM relay_checkpoints').fetchall() == [row]


def test_legacy_row_added_after_construction_stops_poll(atomic, tmp_path):
    rig, _, _ = atomic
    submit(rig, 1)
    sink = Sink()
    relay = create(rig, tmp_path, sink)
    binding = module._json({'schema': 'sdk.local-parent-monitor.v1',
                            'consumer_id': Sink.consumer_id, 'scope_key': relay._scope_key})
    with sqlite3.connect(tmp_path / 'checkpoint.sqlite') as connection:
        connection.execute('INSERT INTO relay_checkpoints VALUES (?,?,?,?,?)',
                           (module._sha(binding), binding, 1, 'old-event', 'a' * 64))
    with pytest.raises(LocalParentRelayError, match='LEGACY_CHECKPOINT_UNSUPPORTED'):
        relay.poll()
    assert sink.events == []
    with sqlite3.connect(tmp_path / 'checkpoint.sqlite') as connection:
        assert connection.execute('SELECT count(*) FROM relay_checkpoints').fetchone()[0] == 1


def test_wire_scope_is_required_and_v2_namespaces_include_all_wire_fields(atomic, tmp_path):
    rig, _, _ = atomic
    source = LocalParentEventReader(host=rig.host, scope=rig.target)
    with pytest.raises(TypeError):
        LocalParentMonitorRelay(reader=source, scope=rig.target, sink=Sink(),
                                checkpoint_path=tmp_path / 'checkpoint.sqlite')
    for field in ('execution_domain_ref', 'account_ref', 'strategy_ref', 'run_id'):
        changed = replace(rig.scope, **{field: 'different'})
        first = LocalParentMonitorRelay(reader=source, scope=rig.target, wire_scope=rig.scope,
                                        sink=Sink(), checkpoint_path=tmp_path / 'checkpoint.sqlite')
        second = LocalParentMonitorRelay(reader=source, scope=rig.target, wire_scope=changed,
                                         sink=Sink(), checkpoint_path=tmp_path / 'checkpoint.sqlite')
        assert first._namespace != second._namespace
        first.close()
        second.close()


@pytest.mark.parametrize('field', ['execution_domain_ref', 'account_ref', 'strategy_ref', 'run_id'])
def test_single_source_scope_mismatch_is_not_delivered(atomic, tmp_path, field):
    rig, _, _ = atomic
    submit(rig, 1)
    sink = Sink()
    changed = replace(rig.scope, **{field: 'different'})
    relay = LocalParentMonitorRelay(reader=LocalParentEventReader(host=rig.host, scope=rig.target),
        scope=rig.target, wire_scope=changed, sink=sink, checkpoint_path=tmp_path / 'mismatch.sqlite')
    try:
        before = relay.checkpoint()
        with pytest.raises(LocalParentRelayError, match='SOURCE_SCOPE_MISMATCH'):
            relay.poll()
        assert sink.events == [] and relay.checkpoint() == before
    finally:
        relay.close()


def test_missing_run_is_rejected_before_relay_construction(atomic, tmp_path):
    rig, _, _ = atomic
    with pytest.raises(LocalParentRelayError, match='CONFIGURATION_INVALID'):
        LocalParentMonitorRelay(reader=LocalParentEventReader(host=rig.host, scope=rig.target),
            scope=rig.target, wire_scope=replace(rig.scope, run_id=None), sink=Sink(),
            checkpoint_path=tmp_path / 'checkpoint.sqlite')


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


def test_damaged_source_intake_does_not_forward_or_advance(atomic, tmp_path):
    rig, store, _ = atomic
    sink = Sink()
    relay = create(rig, tmp_path, sink)
    try:
        submit(rig, 1)
        assert relay.poll() == (1, 1)
        checkpoint = relay.checkpoint()
        sink.events.clear()
        submit(rig, 2)
        with store._transaction() as cursor:
            origin_key = _key('local_parent', relay._scope_key, 'parent-2')
            origin = json.loads(cursor.execute(
                'SELECT value FROM execution_meta WHERE key=?', (origin_key,)).fetchone()[0])
            cursor.execute('UPDATE execution_meta SET value=? WHERE key=?', ('{}', origin['index_keys'][0]))
        with pytest.raises(LocalParentRelayError, match='SOURCE_UNAVAILABLE'):
            relay.poll()
        assert sink.events == []
        assert relay.checkpoint() == checkpoint
    finally:
        relay.close()


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
        LocalParentMonitorRelay(reader=reader, scope=rig.target, wire_scope=rig.scope, sink=sink,
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
        LocalParentMonitorRelay(reader=reader, scope=rig.target, wire_scope=rig.scope, sink=Sink(), checkpoint_path=rig.host.store.path)
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
