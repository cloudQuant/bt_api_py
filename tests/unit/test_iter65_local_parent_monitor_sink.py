"""Real local Monitor ingress, synthetic runtime transport; never uses a socket."""
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireScope
from bt_api_execution.local_parent_events import LocalParentEventReader
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.service_ingress import MonitorIngressService, MonitorIngressTarget

from bt_api_py.execution_parent_grant import SUBMIT
from bt_api_py.service_local_parent_monitor import LocalParentRelayError
from bt_api_py.service_local_parent_monitor_sink import (
    LOCAL_PARENT_EVENT_TYPE, LOCAL_PARENT_OUTBOX_SCOPE,
    LocalParentMonitorBinding, LocalParentMonitorServiceSink,
    LocalParentMonitorSinkError, create_local_parent_service_relay,
)
from bt_api_py.service_monitor import ServiceMonitorOutboxSink
from test_iter65_atomic_submission_intake import atomic
from tests.unit.test_service_monitor_outbox import _PRODUCER, _SCOPE_REF, _runtime


class IngressRemote:
    def __init__(self, path, scope, *, allowed=(LOCAL_PARENT_EVENT_TYPE,)):
        self.outbox = DurableOutbox(path)
        self.scope = scope
        self.ingress = MonitorIngressService(
            self.outbox, {scope: MonitorIngressTarget('monitor:local-parent', allowed)})
        self.attempts = []

    def append(self, arguments):
        self.attempts.append(arguments)
        return dict(self.ingress.handle('events.append', principal_ref=_PRODUCER.principal_ref,
                                        identity=_PRODUCER, scope=self.scope, arguments=arguments))


def components(rig, tmp_path, remote, wire, *, checkpoint='checkpoint.sqlite'):
    runtime, transports, _ = _runtime(remote, scope=wire)
    reader = LocalParentEventReader(host=rig.host, scope=rig.target)
    sink = ServiceMonitorOutboxSink(runtime, scope_ref=_SCOPE_REF,
                                    outbox_scope=LOCAL_PARENT_OUTBOX_SCOPE)
    binding = LocalParentMonitorBinding(rig.target, wire)
    relay = create_local_parent_service_relay(sink=sink, binding=binding, reader=reader,
                                             checkpoint_path=tmp_path / checkpoint)
    return runtime, transports[0], reader, sink, binding, relay


def wire_for(rig):
    # The domain is explicitly supplied by trusted composition, not inferred
    # from provider or environment names.
    return WireScope('configured-domain', account_ref=rig.target.account_ref,
                     strategy_ref=rig.target.strategy_id, run_id='synthetic-run')


def submit(rig):
    result = rig.call(SUBMIT, rig.parent(request_id='req-1', parent_ref='parent-1'))
    assert result['ok']


def test_real_ingress_receipt_and_idempotent_replay(atomic, tmp_path, monkeypatch):
    rig, _, _ = atomic
    wire = wire_for(rig)
    remote = IngressRemote(tmp_path / 'monitor.sqlite', wire)
    runtime, _, _, _, _, relay = components(rig, tmp_path, remote, wire)
    submit(rig)
    import bt_api_py.service_local_parent_monitor as module
    original = module._advance_checkpoint
    monkeypatch.setattr(module, '_advance_checkpoint',
                        lambda *a, **k: (_ for _ in ()).throw(module._CheckpointFailureError('TEST_FAILURE')))
    with pytest.raises(LocalParentRelayError, match='TEST_FAILURE'):
        relay.poll()
    assert relay.checkpoint().sequence == 0
    monkeypatch.setattr(module, '_advance_checkpoint', original)
    assert relay.poll() == (1, 1)
    assert remote.attempts[0] == remote.attempts[1]
    rows, more = remote.outbox.read_page_types('monitor:local-parent', (LOCAL_PARENT_EVENT_TYPE,), 0, 10)
    assert not more and len(rows) == 1
    payload = rows[0].event.data['payload']
    assert payload['observation'] == 'LOCAL_RECEIVED'
    assert payload['coverage'] == 'FROM_ACTIVATION'
    assert payload['sequence'] == 1
    assert set(payload) == {'schema', 'observation', 'coverage', 'sequence', 'fingerprint'}
    assert 'parent-1' not in repr(payload) and rig.target.account_ref not in repr(payload)
    runtime.close()


def test_ambiguous_ack_replays_against_real_ingress(atomic, tmp_path):
    rig, _, _ = atomic
    wire = wire_for(rig)
    remote = IngressRemote(tmp_path / 'monitor.sqlite', wire)
    runtime, transport, _, _, _, relay = components(rig, tmp_path, remote, wire)
    submit(rig)
    transport.fail_after_append = True
    with pytest.raises(LocalParentRelayError, match='REMOTE_DELIVERY_UNKNOWN'):
        relay.poll()
    assert relay.checkpoint().sequence == 0 and len(remote.attempts) == 1
    runtime.close()
    replacement, _, _, _, _, replay = components(rig, tmp_path, remote, wire)
    assert replay.poll() == (1, 1)
    assert remote.attempts[0] == remote.attempts[1]
    rows, _ = remote.outbox.read_page_types('monitor:local-parent', (LOCAL_PARENT_EVENT_TYPE,), 0, 10)
    assert len(rows) == 1
    replacement.close()


def test_remote_event_conflict_does_not_advance(atomic, tmp_path):
    rig, _, _ = atomic
    wire = wire_for(rig)
    remote = IngressRemote(tmp_path / 'monitor.sqlite', wire)
    runtime, _, reader, _, _, relay = components(rig, tmp_path, remote, wire)
    submit(rig)
    page = reader.page(generation=reader.generation())
    event = relay._project(page.events[0], page.stream_id)
    remote.append({'schema_version': 1, 'event_id': event.event_id,
                   'event_type': LOCAL_PARENT_EVENT_TYPE,
                   'occurred_at': event.occurred_at_ns / 1_000_000_000,
                   'data': {'schema': 'sdk.local_parent_monitor.v1', 'observation': 'LOCAL_RECEIVED',
                            'coverage': 'FROM_ACTIVATION', 'sequence': event.sequence,
                            'fingerprint': 'f' * 64}})
    with pytest.raises(LocalParentRelayError, match='REMOTE_DELIVERY_UNKNOWN'):
        relay.poll()
    assert relay.checkpoint().sequence == 0
    assert len(remote.attempts) == 2
    runtime.close()


@pytest.mark.parametrize('change', ['account', 'strategy', 'domain', 'run', 'outbox'])
def test_binding_mismatch_rejected_before_rpc(atomic, tmp_path, change):
    rig, _, _ = atomic
    wire = wire_for(rig)
    remote = IngressRemote(tmp_path / 'monitor.sqlite', wire)
    runtime, transport, reader, sink, binding, _ = components(rig, tmp_path, remote, wire)
    if change == 'account':
        with pytest.raises(LocalParentMonitorSinkError):
            LocalParentMonitorBinding(rig.target, replace(wire, account_ref='other'))
    elif change == 'strategy':
        with pytest.raises(LocalParentMonitorSinkError):
            LocalParentMonitorBinding(rig.target, replace(wire, strategy_ref='other'))
    elif change == 'outbox':
        wrong = ServiceMonitorOutboxSink(runtime, scope_ref=_SCOPE_REF, outbox_scope='other')
        with pytest.raises(LocalParentMonitorSinkError):
            LocalParentMonitorServiceSink(sink=wrong, binding=binding, reader=reader)
    else:
        altered = replace(wire, **({'execution_domain_ref': 'other'} if change == 'domain' else {'run_id': 'other'}))
        with pytest.raises(LocalParentMonitorSinkError):
            LocalParentMonitorServiceSink(sink=sink, binding=LocalParentMonitorBinding(rig.target, altered), reader=reader)
    assert not remote.attempts
    assert not any(req.header.operation == 'events.append' for req in transport.requests)
    runtime.close()


def test_ungranted_type_and_invalid_receipt_do_not_checkpoint(atomic, tmp_path):
    rig, _, _ = atomic
    wire = wire_for(rig)
    remote = IngressRemote(tmp_path / 'monitor.sqlite', wire, allowed=('other.event',))
    runtime, _, _, _, _, relay = components(rig, tmp_path, remote, wire)
    submit(rig)
    with pytest.raises(LocalParentRelayError, match='REMOTE_DELIVERY_UNKNOWN'):
        relay.poll()
    assert relay.checkpoint().sequence == 0
    runtime.close()


def test_malformed_remote_receipt_after_append_does_not_checkpoint(atomic, tmp_path):
    rig, _, _ = atomic
    wire = wire_for(rig)
    remote = IngressRemote(tmp_path / 'monitor.sqlite', wire)
    runtime, transport, _, _, _, relay = components(rig, tmp_path, remote, wire)
    submit(rig)
    transport.receipt_transform = lambda receipt: {**receipt, 'source_event_id': 'wrong'}
    with pytest.raises(LocalParentRelayError, match='REMOTE_DELIVERY_UNKNOWN'):
        relay.poll()
    assert relay.checkpoint().sequence == 0
    transport.receipt_transform = None
    assert relay.poll() == (1, 1)
    runtime.close()


def test_binding_revoked_after_remote_append_does_not_checkpoint(atomic, tmp_path):
    rig, _, _ = atomic
    wire = wire_for(rig)
    remote = IngressRemote(tmp_path / 'monitor.sqlite', wire)
    runtime, transport, _, _, _, relay = components(rig, tmp_path, remote, wire)
    submit(rig)
    def revoke(receipt):
        runtime._bindings[0] = replace(runtime._bindings[0], active=False)
        return receipt
    transport.receipt_transform = revoke
    with pytest.raises(LocalParentRelayError, match='REMOTE_DELIVERY_UNKNOWN'):
        relay.poll()
    assert relay.checkpoint().sequence == 0
    rows, _ = remote.outbox.read_page_types('monitor:local-parent', (LOCAL_PARENT_EVENT_TYPE,), 0, 10)
    assert len(rows) == 1
    runtime.close()


def test_source_reader_must_match_configured_execution_scope(atomic, tmp_path):
    rig, _, _ = atomic
    wire = wire_for(rig)
    remote = IngressRemote(tmp_path / 'monitor.sqlite', wire)
    runtime, _, reader, sink, _, _ = components(rig, tmp_path, remote, wire)
    other = replace(rig.target, provider='other-provider')
    with pytest.raises(LocalParentMonitorSinkError, match='INVALID_CONFIGURATION'):
        LocalParentMonitorServiceSink(sink=sink, binding=LocalParentMonitorBinding(other, wire), reader=reader)
    assert not remote.attempts
    runtime.close()
