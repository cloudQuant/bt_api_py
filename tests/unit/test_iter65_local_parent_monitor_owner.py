"""Owner lifecycle tests with synthetic, in-process resources only."""
import threading

import pytest

from bt_api_base.service_protocol import WireScope
from bt_api_execution.local_parent_events import LocalParentEventReader
from bt_api_py.execution_parent_grant import SUBMIT
from bt_api_py.service_local_parent_monitor import LocalParentMonitorAck, LocalParentMonitorRelay
from bt_api_py.service_local_parent_monitor_sink import (
    LOCAL_PARENT_OUTBOX_SCOPE, LocalParentMonitorBinding, create_local_parent_service_relay,
)
from bt_api_py.service_monitor import ServiceMonitorOutboxSink
from bt_api_py.service_local_parent_monitor_owner import (
    LocalParentMonitorCleanup, LocalParentMonitorOwner, LocalParentMonitorOwnerError,
    LocalParentMonitorRuntimeResource,
)
from tests.unit.test_iter65_local_parent_monitor import atomic
from tests.unit.test_service_monitor_outbox import _RemoteOutbox, _runtime, _SCOPE_REF


class Resource:
    def __init__(self):
        self.owner = threading.get_ident()
        self.calls = []
        self.polled = threading.Event()

    def _record(self, name):
        self.calls.append((name, threading.get_ident()))
        assert threading.get_ident() == self.owner

    def maintain(self):
        self._record('maintain')

    def poll(self):
        self._record('poll')
        self.polled.set()

    def close(self):
        self._record('close')
        return LocalParentMonitorCleanup.CONFIRMED


def owner(tmp_path, factory, *, interval=0.01):
    return LocalParentMonitorOwner(owner_key=str(tmp_path / 'owner'), factory=factory,
                                   poll_interval=interval)


def test_all_resource_calls_run_on_owner_and_stop_is_idempotent(tmp_path):
    made = []
    def factory():
        result = Resource()
        made.append(result)
        return result
    worker = owner(tmp_path, factory)
    assert worker.status().outcome == 'NOT_STARTED'
    assert worker.start(timeout=2).outcome == 'RUNNING'
    assert made[0].polled.wait(2)
    assert worker.stop(timeout=2).outcome == 'STOPPED'
    assert worker.stop(timeout=0).outcome == 'STOPPED'
    assert [name for name, _ in made[0].calls][-1] == 'close'
    assert all(thread == made[0].owner for _, thread in made[0].calls)
    with pytest.raises(LocalParentMonitorOwnerError, match='OWNER_ALREADY_STARTED'):
        worker.start(timeout=0)


def test_blocked_factory_and_stop_race_preserve_owner_reservation(tmp_path):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    class BlockingResource(Resource):
        def close(self):
            result = super().close()
            closed.set()
            return result
    def factory():
        entered.set()
        assert release.wait(3)
        return BlockingResource()
    worker = owner(tmp_path, factory)
    assert worker.start(timeout=0.01).outcome == 'STILL_RUNNING'
    assert entered.wait(2)
    assert worker.stop(timeout=0.01).outcome == 'STILL_RUNNING'
    second = owner(tmp_path, lambda: Resource())
    with pytest.raises(LocalParentMonitorOwnerError, match='OWNER_KEY_BUSY'):
        second.start(timeout=0)
    release.set()
    assert worker.stop(timeout=2).outcome == 'STOPPED'
    assert closed.is_set()
    assert second.start(timeout=2).outcome == 'RUNNING'
    assert second.stop(timeout=2).outcome == 'STOPPED'


def test_stop_while_another_thread_waits_for_start_skips_poll(tmp_path):
    entered, release = threading.Event(), threading.Event()
    resources, results = [], []
    def factory():
        entered.set()
        assert release.wait(3)
        result = Resource()
        resources.append(result)
        return result
    worker = owner(tmp_path, factory)
    starting = threading.Thread(target=lambda: results.append(worker.start(timeout=2)))
    starting.start()
    assert entered.wait(2)
    assert worker.stop(timeout=0.01).outcome == 'STILL_RUNNING'
    release.set()
    starting.join(2)
    assert not starting.is_alive() and results[0].outcome != 'RUNNING'
    assert worker.stop(timeout=2).outcome == 'STOPPED'
    assert [name for name, _ in resources[0].calls] == ['close']


def test_blocked_sink_keeps_resources_and_prevents_second_owner(tmp_path):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    class BlockedSink(Resource):
        def poll(self):
            self._record('poll')
            entered.set()
            assert release.wait(3)
        def close(self):
            result = super().close()
            closed.set()
            return result
    worker = owner(tmp_path, BlockedSink)
    assert worker.start(timeout=2).outcome == 'RUNNING'
    assert entered.wait(2)
    assert worker.stop(timeout=0.01).outcome == 'STILL_RUNNING'
    assert not closed.is_set()
    with pytest.raises(LocalParentMonitorOwnerError, match='OWNER_KEY_BUSY'):
        owner(tmp_path, Resource).start(timeout=0)
    release.set()
    assert worker.stop(timeout=2).outcome == 'STOPPED'
    assert closed.is_set()


def test_blocked_close_is_not_reported_as_stopped(tmp_path):
    closing, release = threading.Event(), threading.Event()
    class BlockedClose(Resource):
        def close(self):
            self._record('close')
            closing.set()
            assert release.wait(3)
            return LocalParentMonitorCleanup.CONFIRMED
    worker = owner(tmp_path, BlockedClose)
    assert worker.start(timeout=2).outcome == 'RUNNING'
    assert worker.stop(timeout=0.01).outcome == 'STILL_RUNNING'
    assert closing.wait(2)
    assert worker.status().phase == 'CLOSING'
    with pytest.raises(LocalParentMonitorOwnerError, match='OWNER_KEY_BUSY'):
        owner(tmp_path, Resource).start(timeout=0)
    release.set()
    assert worker.stop(timeout=2).outcome == 'STOPPED'


@pytest.mark.parametrize('where,expected', [('factory', 'FACTORY_FAILED'),
                                           ('poll', 'OWNER_WORK_FAILED'),
                                           ('close', 'CLOSE_FAILED')])
def test_failures_are_reported_without_restart(tmp_path, where, expected):
    class Failing(Resource):
        def poll(self):
            if where == 'poll':
                raise RuntimeError('private diagnostic')
            super().poll()
        def close(self):
            result = super().close()
            if where == 'close':
                raise RuntimeError('private diagnostic')
            return result
    def factory():
        if where == 'factory':
            raise RuntimeError('private diagnostic')
        return Failing()
    worker = owner(tmp_path, factory)
    worker.start(timeout=2)
    if where == 'poll':
        # Join without issuing stop: the first poll is guaranteed to fail.
        worker._thread.join(2)
    result = worker.stop(timeout=2)
    assert result.outcome == 'FAILED' and result.reason == expected
    assert result.cleanup == ('CONFIRMED' if where == 'poll' else 'UNKNOWN')
    assert 'private' not in repr(result)
    if where in ('factory', 'close'):
        with pytest.raises(LocalParentMonitorOwnerError, match='OWNER_KEY_QUARANTINED'):
            owner(tmp_path, Resource).start(timeout=0)


def test_invalid_factory_resource_stays_quarantined_even_if_close_returns(tmp_path):
    closed = []
    class Invalid:
        def close(self):
            closed.append(threading.get_ident())
    worker = owner(tmp_path, Invalid)
    worker.start(timeout=2)
    result = worker.stop(timeout=2)
    assert closed and closed[0] != threading.get_ident()
    assert result.outcome == 'FAILED' and result.reason == 'INVALID_RESOURCE'
    assert result.cleanup == 'UNKNOWN'
    with pytest.raises(LocalParentMonitorOwnerError, match='OWNER_KEY_QUARANTINED'):
        owner(tmp_path, Resource).start(timeout=0)


@pytest.mark.parametrize('returned', [None, False, 'CONFIRMED', 'UNKNOWN'])
def test_close_needs_explicit_cleanup_confirmation(tmp_path, returned):
    class Unconfirmed(Resource):
        def close(self):
            self._record('close')
            return returned
    worker = owner(tmp_path, Unconfirmed)
    assert worker.start(timeout=2).outcome == 'RUNNING'
    result = worker.stop(timeout=2)
    assert (result.outcome, result.reason, result.cleanup) == ('FAILED', 'CLOSE_UNCONFIRMED', 'UNKNOWN')
    with pytest.raises(LocalParentMonitorOwnerError, match='OWNER_KEY_QUARANTINED'):
        owner(tmp_path, Resource).start(timeout=0)


def test_unconfirmed_sink_result_replays_from_persisted_checkpoint(atomic, tmp_path):
    rig, _, _ = atomic
    result = rig.call(SUBMIT, rig.parent(request_id='req-owner', parent_ref='parent-owner'))
    assert result['ok']
    observations = []
    delivered = threading.Event()
    class Sink:
        consumer_id = 'synthetic-owner-sink'
        def __init__(self, fail):
            self.fail = fail
        def __call__(self, event):
            observations.append(event)
            if self.fail:
                raise TimeoutError('synthetic lost receipt')
            delivered.set()
            return LocalParentMonitorAck(self.consumer_id, event.event_id, event.fingerprint)
    def factory(fail):
        reader = LocalParentEventReader(host=rig.host, scope=rig.target)
        relay = LocalParentMonitorRelay(reader=reader, scope=rig.target, wire_scope=rig.scope, sink=Sink(fail),
                                         checkpoint_path=tmp_path / 'checkpoint.sqlite')
        class Owned:
            def maintain(self):
                pass
            def poll(self):
                return relay.poll()
            def close(self):
                relay.close()
                return LocalParentMonitorCleanup.CONFIRMED
        return Owned()
    first = owner(tmp_path, lambda: factory(True))
    first.start(timeout=2)
    first._thread.join(2)
    assert first.status().outcome == 'FAILED'
    assert first.status().reason == 'OWNER_WORK_FAILED'
    second = owner(tmp_path, lambda: factory(False))
    assert second.start(timeout=2).outcome == 'RUNNING'
    assert delivered.wait(2)
    assert second.stop(timeout=2).outcome == 'STOPPED'
    assert observations[0] == observations[1]


@pytest.mark.parametrize('unregister_unknown', [False, True])
def test_real_runtime_close_requires_confirmed_unregister(atomic, tmp_path, unregister_unknown):
    rig, _, _ = atomic
    runtimes = []
    def factory():
        wire = rig.scope
        runtime, transports, _ = _runtime(_RemoteOutbox(), scope=wire)
        runtimes.append(runtime)
        if unregister_unknown:
            original = transports[0].request
            def lost_unregister(request, *, timeout):
                if request.header.operation == 'service.unregister':
                    raise TimeoutError('synthetic unknown unregister')
                return original(request, timeout=timeout)
            transports[0].request = lost_unregister
        reader = LocalParentEventReader(host=rig.host, scope=rig.target)
        sink = ServiceMonitorOutboxSink(runtime, scope_ref=_SCOPE_REF,
                                        outbox_scope=LOCAL_PARENT_OUTBOX_SCOPE)
        relay = create_local_parent_service_relay(reader=reader, sink=sink,
                binding=LocalParentMonitorBinding(rig.target, wire),
                checkpoint_path=tmp_path / 'runtime-checkpoint.sqlite')
        return LocalParentMonitorRuntimeResource(runtime=runtime, relay=relay)
    worker = owner(tmp_path, factory)
    assert worker.start(timeout=2).outcome == 'RUNNING'
    status = worker.stop(timeout=2)
    assert runtimes[0].state == 'closed'
    if unregister_unknown:
        assert runtimes[0].unregister_uncertainties > 0
        assert (status.outcome, status.cleanup) == ('FAILED', 'UNKNOWN')
        with pytest.raises(LocalParentMonitorOwnerError, match='OWNER_KEY_QUARANTINED'):
            owner(tmp_path, Resource).start(timeout=0)
    else:
        assert runtimes[0].unregister_uncertainties == 0
        assert (status.outcome, status.cleanup) == ('STOPPED', 'CONFIRMED')


def test_runtime_wrapper_rejects_relay_bound_to_another_runtime(atomic, tmp_path):
    rig, _, _ = atomic
    wire = rig.scope
    first, _, _ = _runtime(_RemoteOutbox(), scope=wire)
    second, _, _ = _runtime(_RemoteOutbox(), scope=wire)
    try:
        reader = LocalParentEventReader(host=rig.host, scope=rig.target)
        sink = ServiceMonitorOutboxSink(first, scope_ref=_SCOPE_REF,
                                        outbox_scope=LOCAL_PARENT_OUTBOX_SCOPE)
        relay = create_local_parent_service_relay(reader=reader, sink=sink,
                binding=LocalParentMonitorBinding(rig.target, wire),
                checkpoint_path=tmp_path / 'wrong-runtime.sqlite')
        with pytest.raises(LocalParentMonitorOwnerError, match='INVALID_RESOURCE'):
            LocalParentMonitorRuntimeResource(runtime=second, relay=relay)
        relay.close()
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize('bad', [True, -1, float('inf'), float('nan'), '1', 86401, 10 ** 1000])
def test_wait_bounds_reject_invalid_values(tmp_path, bad):
    worker = owner(tmp_path, Resource)
    with pytest.raises(LocalParentMonitorOwnerError, match='INVALID_DURATION'):
        worker.start(timeout=bad)
    with pytest.raises(LocalParentMonitorOwnerError, match='INVALID_DURATION'):
        worker.stop(timeout=bad)
