"""In-memory cleanup policy checks; no retained test threads or external resources."""

import threading

import pytest

from bt_api_py.service_runtime_thread import (
    OwnerOperationContinuation,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
    _StickyCleanupSignal,
)


class EndFakeWait(BaseException):
    pass


class FakeCondition(threading.Condition):
    def wait(self, timeout=None):
        raise EndFakeWait()


class Raw:
    sticky_cleanup_enabled = True

    def __init__(self, events, error=None, unknown=0):
        self.events, self.error = events, error
        self.bindings = ()
        self.healthy = True
        self._state = 'new'
        self.cleanup_failures = self.unregister_uncertainties = 0
        self.unknown_registration_count = unknown

    def start(self):
        self._state = 'registered'

    def maintain(self):
        pass

    def next_maintenance_delay(self):
        return 1

    def close(self):
        self.events.append('raw')
        if self.error is not None:
            raise self.error
        self._state = 'closed'


class Extension:
    def __init__(self, events, error=None, count=0):
        self.events, self.error, self.count = events, error, count

    @property
    def cleanup_uncertainties(self):
        if isinstance(self.count, BaseException):
            raise self.count
        return self.count

    def start(self, runtime):
        pass

    def close(self):
        self.events.append('extension')
        if self.error is not None:
            raise self.error


def direct_runtime(events, *, unknown=lambda: False, callback=None):
    if callback is None:
        callback = lambda owner: events.append('callback')
    owner = ThreadedServiceClientRuntime(
        lambda: None, cleanup_unknown=unknown, on_cleanup_sticky=callback,
    )
    owner._condition = FakeCondition()
    return owner


def assert_sticky(owner, raw, extension):
    with pytest.raises(EndFakeWait):
        owner._run_sticky_cleanup(raw, extension)
    assert owner.status().state == 'close_failed'
    assert owner._sticky_resources == (raw, extension)
    snapshot = owner.status()
    assert (snapshot.cleanup_failures, snapshot.unregister_uncertainties,
            snapshot.owner_cleanup_uncertainties, snapshot.unknown_registration_count) == (
                None, None, None, None)
    from bt_api_py.service_runtime_cleanup import runtime_cleanup_confirmed
    assert not runtime_cleanup_confirmed(snapshot)
    before = owner._cleanup_retry
    with pytest.raises(ThreadedRuntimeError, match='CLEANUP_FAILED'):
        owner.close()
    assert owner._cleanup_retry == before
    assert owner.status().state == 'close_failed'


@pytest.mark.parametrize('error', [RuntimeError('close'), KeyboardInterrupt()])
def test_extension_exception_halts_before_raw_and_retains_error(error):
    events = []
    owner = direct_runtime(events)
    raw, extension = Raw(events), Extension(events, error=error)
    assert_sticky(owner, raw, extension)
    assert events == ['extension', 'callback']
    assert owner._sticky_errors[0] is error


@pytest.mark.parametrize('count', [1, False, None, RuntimeError('count')])
def test_extension_uncertainty_or_bad_count_halts_raw(count):
    events = []
    owner = direct_runtime(events)
    assert_sticky(owner, Raw(events), Extension(events, count=count))
    assert events == ['callback']


@pytest.mark.parametrize('error,unknown', [(RuntimeError('raw'), 0), (None, None), (None, 1)])
def test_raw_failure_or_unconfirmed_snapshot_is_sticky(error, unknown):
    events = []
    owner = direct_runtime(events)
    class ChangesOnClose(Raw):
        def close(self):
            super().close()
            self.unknown_registration_count = unknown
    raw = ChangesOnClose(events, error=error)
    assert_sticky(owner, raw, Extension(events))
    assert events == ['extension', 'raw', 'callback']


def test_external_unknown_and_callback_failure_keep_resources():
    events = []
    callback_error = KeyboardInterrupt()
    def callback(owner):
        events.append('callback')
        raise callback_error
    owner = direct_runtime(events, unknown=lambda: True, callback=callback)
    raw, extension = Raw(events), Extension(events)
    assert_sticky(owner, raw, extension)
    assert events == ['callback']
    assert owner._sticky_errors[-1] is callback_error


@pytest.mark.parametrize('value', [None, 0, RuntimeError('probe')])
def test_external_probe_requires_exact_false(value):
    events = []
    def probe():
        if isinstance(value, BaseException):
            raise value
        return value
    owner = direct_runtime(events, unknown=probe)
    assert_sticky(owner, Raw(events), Extension(events))
    assert events == ['callback']


def test_callback_is_unlocked_and_owner_reentry_is_rejected():
    events = []
    def callback(owner):
        acquired = []
        def other_thread():
            with owner._condition:
                acquired.append(True)
        worker = threading.Thread(target=other_thread)
        worker.start()
        worker.join(timeout=1)
        assert acquired == [True]
        with pytest.raises(ThreadedRuntimeError, match='OWNER_THREAD_REENTRY'):
            owner.close()
        events.append('callback')
    owner = direct_runtime(events, unknown=lambda: True, callback=callback)
    owner._thread = threading.current_thread()
    with pytest.raises(EndFakeWait):
        owner._run_sticky_cleanup(Raw(events), Extension(events))
    assert events == ['callback']


def test_external_unknown_after_extension_prevents_raw():
    events, unknown = [], [False]
    class ExtensionSetsUnknown(Extension):
        def close(self):
            super().close()
            unknown[0] = True
    owner = direct_runtime(events, unknown=lambda: unknown[0])
    assert_sticky(owner, Raw(events), ExtensionSetsUnknown(events))
    assert events == ['extension', 'callback']


def test_exact_success_and_default_legacy_retry():
    events = []
    owner = direct_runtime(events)
    owner._run_sticky_cleanup(Raw(events), Extension(events))
    assert events == ['extension', 'raw']
    assert not owner._cleanup_sticky

    events = []
    runtime = ThreadedServiceClientRuntime(
        lambda: Raw(events), owner_extension_factory=lambda: Extension(events),
        cleanup_unknown=lambda: False,
        on_cleanup_sticky=lambda _: events.append('callback'),
    )
    runtime.start()
    assert runtime.close().state == 'closed'
    assert events == ['extension', 'raw']

    events = []
    extension = Extension(events, error=RuntimeError('first'))
    runtime = ThreadedServiceClientRuntime(lambda: Raw(events), owner_extension_factory=lambda: extension)
    runtime.start()
    with pytest.raises(ThreadedRuntimeError, match='CLEANUP_FAILED'):
        runtime.close()
    extension.error = None
    assert runtime.close().state == 'closed'
    assert events == ['extension', 'raw', 'extension']


def test_observation_during_run_rejects_uncertainty_only_when_opted_in():
    owner = direct_runtime([])
    for count in (1, None, RuntimeError('count')):
        with pytest.raises(_StickyCleanupSignal):
            owner._capture_owner_uncertainties(Extension([], count=count))
    legacy = ThreadedServiceClientRuntime(lambda: None)
    legacy._capture_owner_uncertainties(Extension([], count=1))
    assert legacy.status().owner_cleanup_uncertainties == 1


class OwnerWaitCondition(threading.Condition):
    """Terminate only the test owner's retention wait, never a real resource."""
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def wait(self, timeout=None):
        if self.owner._cleanup_sticky and threading.current_thread() is self.owner._thread:
            raise EndFakeWait()
        return super().wait(timeout)


def test_running_owner_uncertainty_immediately_stops_without_cleanup():
    events = []
    callback_called = threading.Event()
    class ChangingExtension(Extension):
        def invoke(self, operation, arguments):
            self.count = 1
            return {'ok': True}
    extension = ChangingExtension(events)
    runtime = ThreadedServiceClientRuntime(
        lambda: Raw(events), owner_extension_factory=lambda: extension,
        cleanup_unknown=lambda: False,
        on_cleanup_sticky=lambda _: callback_called.set(),
    )
    runtime._condition = OwnerWaitCondition(runtime)
    runtime.start()
    handle = runtime.submit_owner('change', {})
    with pytest.raises(ThreadedRuntimeError, match='OWNER_OUTCOME_UNKNOWN'):
        handle.result()
    assert callback_called.wait(1)
    runtime._thread.join(1)
    assert not runtime._thread.is_alive()
    assert runtime.status().state == 'close_failed'
    assert events == []
    with pytest.raises(ThreadedRuntimeError, match='CLEANUP_FAILED'):
        runtime.close()


def test_failed_start_with_owner_uncertainty_stops_without_cleanup():
    events = []
    callback_called = threading.Event()
    extension = Extension(events, count=1)
    runtime = ThreadedServiceClientRuntime(
        lambda: Raw(events), owner_extension_factory=lambda: extension,
        cleanup_unknown=lambda: False,
        on_cleanup_sticky=lambda _: callback_called.set(),
    )
    runtime._condition = OwnerWaitCondition(runtime)
    with pytest.raises(ThreadedRuntimeError):
        runtime.start()
    assert callback_called.wait(1)
    runtime._thread.join(1)
    assert not runtime._thread.is_alive()
    assert runtime.status().state == 'close_failed'
    assert events == []


class FakeRetainedRuntime(ThreadedServiceClientRuntime):
    def _run(self):
        try:
            super()._run()
        except EndFakeWait:
            pass


def test_extension_start_failure_with_uncertainty_never_cleans():
    events = []
    notified = threading.Event()
    class FailedStart(Extension):
        def start(self, raw):
            self.count = 1
            raise RuntimeError('partial start')
    ext = FailedStart(events)
    owner = FakeRetainedRuntime(
        lambda: Raw(events), owner_extension_factory=lambda: ext,
        cleanup_unknown=lambda: False, on_cleanup_sticky=lambda _: notified.set(),
    )
    owner._condition = OwnerWaitCondition(owner)
    with pytest.raises(ThreadedRuntimeError):
        owner.start()
    assert notified.wait(1)
    owner._thread.join(1)
    assert not owner._thread.is_alive()
    assert owner.status().state == 'close_failed'
    assert owner.status().owner_cleanup_uncertainties is None
    assert owner._sticky_resources[1] is ext
    assert events == []


@pytest.mark.parametrize('field,value', [
    ('cleanup_failures', 1), ('cleanup_failures', None),
    ('unregister_uncertainties', False), ('unknown_registration_count', None),
    ('unknown_registration_count', 1),
])
def test_existing_raw_uncertainty_prevents_any_cleanup(field, value):
    events = []
    owner = direct_runtime(events)
    raw = Raw(events)
    setattr(raw, field, value)
    assert_sticky(owner, raw, Extension(events))
    assert events == ['callback']


def test_raising_raw_property_prevents_any_cleanup():
    events = []
    class BrokenRaw(Raw):
        @property
        def unknown_registration_count(self):
            raise KeyboardInterrupt()
        @unknown_registration_count.setter
        def unknown_registration_count(self, value):
            pass
    owner = direct_runtime(events)
    raw = BrokenRaw(events)
    assert_sticky(owner, raw, Extension(events))
    assert events == ['callback']
    assert isinstance(owner._sticky_errors[0], KeyboardInterrupt)


def test_raw_uncertainty_during_invoke_rejects_queued_work():
    events = []
    entered, release, notified = threading.Event(), threading.Event(), threading.Event()
    raw = Raw(events)
    class ChangesRaw(Extension):
        def invoke(self, operation, arguments):
            events.append(operation)
            if operation == 'first':
                entered.set()
                assert release.wait(2)
                raw.unregister_uncertainties = 1
            return {'done': True}
    ext = ChangesRaw(events)
    owner = FakeRetainedRuntime(
        lambda: raw, owner_extension_factory=lambda: ext,
        cleanup_unknown=lambda: False, on_cleanup_sticky=lambda _: notified.set(),
    )
    owner._condition = OwnerWaitCondition(owner)
    owner.start()
    first = owner.submit_owner('first', {})
    assert entered.wait(1)
    queued = owner.submit_owner('queued', {})
    release.set()
    with pytest.raises(ThreadedRuntimeError, match='OWNER_OUTCOME_UNKNOWN'):
        first.result()
    with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
        queued.result()
    assert notified.wait(1)
    owner._thread.join(1)
    assert not owner._thread.is_alive()
    assert events == ['first']
    assert owner._sticky_resources == (raw, ext)
    assert owner.status().unregister_uncertainties is None


def test_legacy_raw_without_sticky_capability_is_not_started_or_closed():
    events = []
    notified = threading.Event()
    raw = Raw(events)
    raw.sticky_cleanup_enabled = False
    owner = FakeRetainedRuntime(
        lambda: raw, cleanup_unknown=lambda: False,
        on_cleanup_sticky=lambda _: notified.set(),
    )
    owner._condition = OwnerWaitCondition(owner)
    with pytest.raises(ThreadedRuntimeError):
        owner.start()
    assert notified.wait(1)
    owner._thread.join(1)
    assert not owner._thread.is_alive()
    assert raw._state == 'new'
    assert events == []
    assert owner._sticky_resources[0] is raw


def test_extension_close_uncertainty_prevents_raw_cleanup():
    events = []
    class ChangesOnClose(Extension):
        def close(self):
            self.events.append('extension')
            self.count = 1
    owner = direct_runtime(events)
    assert_sticky(owner, Raw(events), ChangesOnClose(events))
    assert events == ['extension', 'callback']


def test_extension_close_raw_uncertainty_prevents_raw_cleanup():
    events = []
    raw = Raw(events)
    class ChangesOnClose(Extension):
        def close(self):
            self.events.append('extension')
            raw.cleanup_failures = 1
    owner = direct_runtime(events)
    assert_sticky(owner, raw, ChangesOnClose(events))
    assert events == ['extension', 'callback']


def test_strict_continuation_completes_and_cleanly_closes():
    events = []
    class ContinuationExtension(Extension):
        def invoke(self, operation, arguments):
            return OwnerOperationContinuation(lambda: {'done': True}, 0.01)
    runtime = ThreadedServiceClientRuntime(
        lambda: Raw(events), owner_extension_factory=lambda: ContinuationExtension(events),
        cleanup_unknown=lambda: False,
        on_cleanup_sticky=lambda _: events.append('callback'),
    )
    runtime.start()
    assert runtime.submit_owner('resume', {}).result() == {'done': True}
    assert runtime.close().state == 'closed'
    assert events == ['extension', 'raw']


@pytest.mark.parametrize('kind', ['owner', 'external'])
def test_raw_call_uncertainty_rejects_queued_owner(kind):
    events, failures = [], []
    entered, release, notified = threading.Event(), threading.Event(), threading.Event()
    unknown = [False]
    ext = Extension(events)
    class ChangingRaw(Raw):
        def call(self, *args, **kwargs):
            events.append('call')
            entered.set()
            assert release.wait(2)
            if kind == 'owner':
                ext.count = 1
            else:
                unknown[0] = True
            return {'ok': True}
    raw = ChangingRaw(events)
    owner = FakeRetainedRuntime(
        lambda: raw, owner_extension_factory=lambda: ext,
        cleanup_unknown=lambda: unknown[0], on_cleanup_sticky=lambda _: notified.set(),
    )
    owner._condition = OwnerWaitCondition(owner)
    owner.start()
    def call():
        try:
            owner.call('mount', 'operation', {})
        except ThreadedRuntimeError as error:
            failures.append(error.code)
    caller = threading.Thread(target=call)
    caller.start()
    assert entered.wait(1)
    queued = owner.submit_owner('queued', {})
    release.set()
    caller.join(1)
    assert not caller.is_alive()
    assert failures == ['CALL_OUTCOME_UNKNOWN']
    with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
        queued.result()
    assert notified.wait(1)
    owner._thread.join(1)
    assert not owner._thread.is_alive()
    assert events == ['call']
    assert owner._sticky_resources == (raw, ext)


@pytest.mark.parametrize('kind', ['owner', 'external'])
def test_maintenance_uncertainty_rejects_queued_owner(kind):
    events = []
    entered, release, notified = threading.Event(), threading.Event(), threading.Event()
    unknown = [False]
    ext = Extension(events)
    class ChangingRaw(Raw):
        def maintain(self):
            entered.set()
            assert release.wait(2)
            if kind == 'owner':
                ext.count = 1
            else:
                unknown[0] = True
    raw = ChangingRaw(events)
    owner = FakeRetainedRuntime(
        lambda: raw, owner_extension_factory=lambda: ext,
        cleanup_unknown=lambda: unknown[0], on_cleanup_sticky=lambda _: notified.set(),
    )
    owner._condition = OwnerWaitCondition(owner)
    owner.start()
    assert entered.wait(1)
    queued = owner.submit_owner('queued', {})
    release.set()
    with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
        queued.result()
    assert notified.wait(1)
    owner._thread.join(1)
    assert not owner._thread.is_alive()
    assert events == []
    assert owner._sticky_resources == (raw, ext)


def test_raw_close_owner_uncertainty_prevents_closed_snapshot():
    events = []
    extension = Extension(events)
    class ChangesOwnerOnClose(Raw):
        def close(self):
            super().close()
            extension.count = 1
    owner = direct_runtime(events)
    raw = ChangesOwnerOnClose(events)
    assert_sticky(owner, raw, extension)
    assert events == ['extension', 'raw', 'callback']


def test_delay_callback_unlocks_condition_for_enqueue():
    events = []
    entered, release = threading.Event(), threading.Event()
    class BlockingDelay(Raw):
        def next_maintenance_delay(self):
            entered.set()
            assert release.wait(2)
            return 1
    class Invokable(Extension):
        def invoke(self, operation, arguments):
            return {'done': True}
    owner = ThreadedServiceClientRuntime(
        lambda: BlockingDelay(events), owner_extension_factory=lambda: Invokable(events),
        cleanup_unknown=lambda: False, on_cleanup_sticky=lambda _: events.append('sticky'),
    )
    owner.start()
    try:
        assert entered.wait(1)
        handle = owner.submit_owner('queued', {})
        release.set()
        assert handle.result(timeout_seconds=1) == {'done': True}
    finally:
        release.set()
        assert owner.close().state == 'closed'


def test_delay_callback_unlocks_condition_for_close():
    events = []
    entered, release = threading.Event(), threading.Event()
    class BlockingDelay(Raw):
        def next_maintenance_delay(self):
            entered.set()
            assert release.wait(2)
            return 1
    owner = ThreadedServiceClientRuntime(
        lambda: BlockingDelay(events), cleanup_unknown=lambda: False,
        on_cleanup_sticky=lambda _: events.append('sticky'),
    )
    owner.start()
    assert entered.wait(1)
    try:
        with pytest.raises(ThreadedRuntimeError, match='CLOSE_TIMEOUT_WORKER_ALIVE'):
            owner.close(timeout_seconds=0.01)
        assert owner._stop
    finally:
        release.set()
        assert owner.close().state == 'closed'


def test_evidence_guard_never_holds_condition():
    events = []
    owner = direct_runtime(events)
    def guard():
        acquired = []
        def contender():
            with owner._condition:
                acquired.append(True)
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(1)
        assert not thread.is_alive()
        assert acquired == [True]
        return False
    owner._cleanup_unknown = guard
    owner._check_running_evidence(Raw(events), Extension(events))


def test_continuation_due_while_delay_callback_blocked_resumes():
    events = []
    entered, release = threading.Event(), threading.Event()
    raw = None
    class BlockingDelay(Raw):
        block = False
        def next_maintenance_delay(self):
            if self.block:
                self.block = False
                entered.set()
                assert release.wait(2)
            return 1
    class ContinuationExtension(Extension):
        def invoke(self, operation, arguments):
            raw.block = True
            return OwnerOperationContinuation(lambda: {'resumed': True}, 0.01)
    raw = BlockingDelay(events)
    owner = ThreadedServiceClientRuntime(
        lambda: raw, owner_extension_factory=lambda: ContinuationExtension(events),
        cleanup_unknown=lambda: False, on_cleanup_sticky=lambda _: events.append('sticky'),
    )
    owner.start()
    try:
        handle = owner.submit_owner('yield', {})
        assert entered.wait(1)
        assert not threading.Event().wait(0.03)
        release.set()
        assert handle.result(timeout_seconds=1) == {'resumed': True}
    finally:
        release.set()
        assert owner.close().state == 'closed'


def test_strict_snapshot_stops_at_binding_attribute_that_sets_unknown():
    events, unknown = [], [False]
    class Binding:
        def __init__(self, label):
            self.label = label
        def __getattribute__(self, name):
            if name in ('registration_id', 'service_epoch'):
                label = object.__getattribute__(self, 'label')
                events.append((label, name))
                if label == 'first' and name == 'registration_id':
                    unknown[0] = True
                return 'value'
            return object.__getattribute__(self, name)
    raw = Raw(events)
    raw.bindings = (Binding('first'), Binding('second'))
    owner = direct_runtime(events, unknown=lambda: unknown[0])
    with pytest.raises(_StickyCleanupSignal):
        owner._capture(raw)
    assert events == [('first', 'registration_id')]


def test_strict_peer_failure_checks_unknown_before_reading_binding_fields(monkeypatch):
    import bt_api_py.service_runtime as service_runtime
    events, unknown = [], [False]
    class Binding:
        def __getattribute__(self, name):
            if name == 'registration_id':
                events.append('registration_id')
            return object.__getattribute__(self, name)
    first, second = Binding(), Binding()
    class PeerRaw(Raw):
        def authenticated_peer_for_binding(self, binding):
            events.append('peer')
            unknown[0] = True
            raise RuntimeError('peer callback')
    monkeypatch.setattr(service_runtime, 'ServiceClientRuntime', PeerRaw)
    raw = PeerRaw(events)
    raw.bindings = (first, second)
    owner = direct_runtime(events, unknown=lambda: unknown[0])
    with pytest.raises(_StickyCleanupSignal):
        owner._capture(raw)
    assert events == ['peer']


def test_strict_raw_count_read_stops_after_it_sets_unknown():
    events, unknown = [], [False]
    class CountRaw(Raw):
        def __getattribute__(self, name):
            if name == 'cleanup_failures':
                events.append(name)
                unknown[0] = True
            elif name == 'unregister_uncertainties':
                events.append(name)
            return object.__getattribute__(self, name)
    raw = CountRaw(events)
    owner = direct_runtime(events, unknown=lambda: unknown[0])
    with pytest.raises(_StickyCleanupSignal):
        owner._capture_raw_uncertainties(raw)
    assert events == ['cleanup_failures']


def test_strict_peer_failure_without_unknown_preserves_existing_fallback(monkeypatch):
    import bt_api_py.service_runtime as service_runtime
    events = []
    class Binding:
        registration_id = 'id'
        service_epoch = 'epoch'
        active = False
        required = False
    class PeerRaw(Raw):
        _require_unregister_ack = False
        _monotonic_clock = staticmethod(__import__('time').monotonic)
        def authenticated_peer_for_binding(self, binding):
            events.append('peer')
            raise RuntimeError('missing proof')
    monkeypatch.setattr(service_runtime, 'ServiceClientRuntime', PeerRaw)
    raw = PeerRaw(events)
    raw.bindings = (Binding(), Binding())
    owner = direct_runtime(events)
    owner._capture(raw)
    assert events == ['peer', 'peer']
    assert len(owner.status().bindings) == 2
    assert all(binding.authenticated_peer is None for binding in owner.status().bindings)


@pytest.mark.parametrize('value', [1, None, False, -1, '0'])
def test_bad_first_raw_count_stops_before_later_fields(value):
    events = []
    class CountRaw(Raw):
        def __getattribute__(self, name):
            if name == 'cleanup_failures':
                events.append(name)
                return value
            if name in ('unregister_uncertainties', 'unknown_registration_count'):
                events.append(name)
            return object.__getattribute__(self, name)
    raw = CountRaw(events)
    owner = direct_runtime(events)
    with pytest.raises(_StickyCleanupSignal):
        owner._capture_raw_uncertainties(raw)
    assert events == ['cleanup_failures']


def test_bad_second_raw_count_stops_before_final_field():
    events = []
    class CountRaw(Raw):
        def __getattribute__(self, name):
            if name in ('cleanup_failures', 'unregister_uncertainties',
                        'unknown_registration_count'):
                events.append(name)
            if name == 'unregister_uncertainties':
                return 1
            return object.__getattribute__(self, name)
    raw = CountRaw(events)
    owner = direct_runtime(events)
    with pytest.raises(_StickyCleanupSignal):
        owner._capture_raw_uncertainties(raw)
    assert events == ['cleanup_failures', 'unregister_uncertainties']


@pytest.mark.parametrize('timeout', [0, -1, False, float('inf'), float('nan'), 10**10000, 5e-324],
                         ids=['zero', 'negative', 'bool', 'infinity', 'nan', 'huge', 'tiny'])
def test_invalid_deadlines_have_no_runtime_side_effect(timeout):
    from bt_api_py.service_runtime_thread import OwnerCallHandle, _OwnerCall
    owner = ThreadedServiceClientRuntime(lambda: pytest.fail('factory called'))
    with pytest.raises(ValueError):
        owner.start(timeout_seconds=timeout)
    assert owner._state == 'new' and owner._thread is None and not owner._stop
    with pytest.raises(ValueError):
        owner.close(timeout_seconds=timeout)
    assert owner._state == 'new' and owner._cleanup_retry == 0 and not owner._stop
    with pytest.raises(ValueError):
        owner.call('mount', 'op', {}, timeout_seconds=timeout)
    assert not owner._queue
    call = _OwnerCall('op', {})
    handle = OwnerCallHandle(owner, call)
    with pytest.raises(ValueError):
        handle.result(timeout_seconds=timeout)
    assert call.state == 'queued' and call.error is None


def test_call_lock_contention_exhausts_original_deadline_before_enqueue(monkeypatch):
    import bt_api_py.service_runtime_thread as module
    deadline_sampled, completed = threading.Event(), threading.Event()
    now = [100.0]
    errors = []
    monkeypatch.setattr(module, '_DEADLINE_CLOCK', lambda: deadline_sampled.set() or now[0])
    owner = ThreadedServiceClientRuntime(lambda: None,
        monotonic_clock=lambda: 100.0)
    owner._state, owner._healthy = 'running', True
    def invoke():
        try:
            owner.call('mount', 'op', {}, timeout_seconds=1)
        except ThreadedRuntimeError as error:
            errors.append(error.code)
        finally:
            completed.set()
    with owner._condition:
        thread = threading.Thread(target=invoke)
        thread.start()
        assert deadline_sampled.wait(1)
        now[0] = 102.0
    assert completed.wait(1)
    thread.join(1)
    assert errors == ['TIMEOUT_BEFORE_START']
    assert not owner._queue


def test_reentrant_lease_clock_close_prevents_enqueue():
    owner = None
    calls = []
    def clock():
        calls.append('clock')
        owner.close()
        return 100.0
    owner = ThreadedServiceClientRuntime(lambda: None, monotonic_clock=clock,
                                         cleanup_unknown=lambda: False,
                                         on_cleanup_sticky=lambda _: None)
    owner._state, owner._healthy = 'running', True
    with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
        owner.call('mount', 'op', {})
    assert owner._stop and not owner._queue and calls == ['clock']


@pytest.mark.parametrize('mode', ['unrepresentable', 'raises'])
def test_continuation_unrepresentable_deadline_is_unknown(monkeypatch, mode):
    import bt_api_py.service_runtime_thread as module
    events = []
    runtime = None
    owner_clock = [False]
    original_clock = module._DEADLINE_CLOCK
    def clock():
        if owner_clock[0] and threading.current_thread() is runtime._thread:
            if mode == 'raises':
                raise RuntimeError('clock failure')
            return 1e300
        return original_clock()
    monkeypatch.setattr(module, '_DEADLINE_CLOCK', clock)
    class ContinuationExtension(Extension):
        def invoke(self, operation, arguments):
            owner_clock[0] = True
            return OwnerOperationContinuation(lambda: events.append('resumed'), 0.01)
    runtime = ThreadedServiceClientRuntime(
        lambda: Raw(events), owner_extension_factory=lambda: ContinuationExtension(events),
    )
    runtime.start()
    try:
        handle = runtime.submit_owner('invoke', {})
        with pytest.raises(ThreadedRuntimeError, match='OWNER_OUTCOME_UNKNOWN'):
            handle.result(timeout_seconds=1)
        assert not runtime._continuations
        assert events == []
    finally:
        runtime.close()


@pytest.mark.parametrize('state,expected', [
    ('queued', 'OWNER_TIMEOUT_QUEUED'), ('running', 'OWNER_TIMEOUT_RUNNING'),
])
def test_handle_wait_timeout_does_not_change_submitted_call(monkeypatch, state, expected):
    import bt_api_py.service_runtime_thread as module
    from bt_api_py.service_runtime_thread import OwnerCallHandle, _OwnerCall
    now = iter([100.0, 102.0])
    monkeypatch.setattr(module, '_DEADLINE_CLOCK', lambda: next(now))
    owner = ThreadedServiceClientRuntime(lambda: None)
    call = _OwnerCall('op', {})
    call.state = state
    with pytest.raises(ThreadedRuntimeError, match=expected):
        OwnerCallHandle(owner, call).result(timeout_seconds=1)
    assert call.state == state and call.error is None


def test_started_call_timeout_remains_unknown_until_owner_finishes():
    events, errors = [], []
    entered, release = threading.Event(), threading.Event()
    class BlockingRaw(Raw):
        def call(self, *args, **kwargs):
            entered.set()
            assert release.wait(2)
            return {'ok': True}
    runtime = ThreadedServiceClientRuntime(lambda: BlockingRaw(events))
    runtime.start()
    def caller():
        try:
            runtime.call('mount', 'op', {}, timeout_seconds=0.05)
        except ThreadedRuntimeError as error:
            errors.append(error.code)
    thread = threading.Thread(target=caller)
    try:
        thread.start()
        assert entered.wait(1)
        thread.join(1)
        assert not thread.is_alive()
        assert errors == ['TIMEOUT_AFTER_START']
    finally:
        release.set()
        runtime.close()


def test_continuation_due_failure_does_not_override_close_result(monkeypatch):
    import bt_api_py.service_runtime_thread as module
    events = []
    entered, release = threading.Event(), threading.Event()
    runtime = None
    fail_owner_clock = [False]
    original_clock = module._DEADLINE_CLOCK
    def clock():
        if fail_owner_clock[0] and threading.current_thread() is runtime._thread:
            raise RuntimeError('clock error')
        return original_clock()
    monkeypatch.setattr(module, '_DEADLINE_CLOCK', clock)
    class ContinuationExtension(Extension):
        def invoke(self, operation, arguments):
            def resume():
                entered.set()
                assert release.wait(2)
                fail_owner_clock[0] = True
                return OwnerOperationContinuation(lambda: events.append('late'), 0.01)
            return OwnerOperationContinuation(resume, 0.01)
    runtime = ThreadedServiceClientRuntime(
        lambda: Raw(events), owner_extension_factory=lambda: ContinuationExtension(events),
    )
    runtime.start()
    handle = runtime.submit_owner('start', {})
    try:
        assert entered.wait(1)
        with pytest.raises(ThreadedRuntimeError, match='CLOSE_TIMEOUT_WORKER_ALIVE'):
            runtime.close(timeout_seconds=0.01)
        with pytest.raises(ThreadedRuntimeError, match='OWNER_OUTCOME_UNKNOWN'):
            handle.result()
        error = handle._call.error
        release.set()
        runtime.close()
        assert handle._call.error is error
        assert 'late' not in events
    finally:
        release.set()
        runtime.close()


def test_clock_failure_before_actions_preserves_state_and_queued_call(monkeypatch):
    import bt_api_py.service_runtime_thread as module
    from bt_api_py.service_runtime_thread import OwnerCallHandle, _OwnerCall
    def broken():
        raise RuntimeError('clock failed')
    monkeypatch.setattr(module, '_DEADLINE_CLOCK', broken)
    owner = ThreadedServiceClientRuntime(lambda: pytest.fail('factory called'))
    queued = _OwnerCall('queued', {})
    owner._state, owner._healthy = 'running', True
    owner._queue.append(queued)
    for action in (
        lambda: owner.start(), lambda: owner.close(),
        lambda: owner.call('mount', 'op', {}), lambda: OwnerCallHandle(owner, queued).result(),
    ):
        with pytest.raises(ValueError):
            action()
    assert owner._state == 'running' and not owner._stop and owner._cleanup_retry == 0
    assert list(owner._queue) == [queued] and queued.state == 'queued' and queued.error is None


def test_owner_submit_reentrant_lease_clock_close_prevents_enqueue():
    owner = None
    def clock():
        owner.close()
        return 100.0
    owner = ThreadedServiceClientRuntime(lambda: None, monotonic_clock=clock,
                                         owner_extension_factory=lambda: Extension([]),
                                         cleanup_unknown=lambda: False,
                                         on_cleanup_sticky=lambda _: None)
    owner._state, owner._healthy = 'running', True
    with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
        owner.submit_owner('op', {})
    assert owner._stop and not owner._queue


def test_call_expired_during_unlocked_lease_sample_is_not_enqueued(monkeypatch):
    import bt_api_py.service_runtime_thread as module
    now = [100.0]
    monkeypatch.setattr(module, '_DEADLINE_CLOCK', lambda: now[0])
    def lease_clock():
        now[0] = 102.0
        return 100.0
    owner = ThreadedServiceClientRuntime(lambda: None, monotonic_clock=lease_clock,
                                         cleanup_unknown=lambda: False,
                                         on_cleanup_sticky=lambda _: None)
    owner._state, owner._healthy = 'running', True
    with pytest.raises(ThreadedRuntimeError, match='TIMEOUT_BEFORE_START'):
        owner.call('mount', 'op', {}, timeout_seconds=1)
    assert not owner._queue


@pytest.mark.parametrize('operation', ['status', 'call', 'submit'])
def test_native_lease_expiring_while_waiting_for_lock_fails_closed(operation):
    import bt_api_py.service_runtime_thread as module
    sampled, continue_sample = threading.Event(), threading.Event()
    result, errors = [], []
    owner = ThreadedServiceClientRuntime(lambda: None,
                                         owner_extension_factory=lambda: Extension([]),
                                         cleanup_unknown=lambda: False,
                                         on_cleanup_sticky=lambda _: None)
    owner._state, owner._healthy = 'running', True
    owner._lease_clock_native_proven = True
    owner._lease_deadline = module._NATIVE_MONOTONIC() + 0.04
    original_sample = owner._sample_lease_clock
    def sample():
        value = original_sample()
        sampled.set()
        assert continue_sample.wait(2)
        return value
    owner._sample_lease_clock = sample
    def run():
        try:
            if operation == 'status':
                result.append(owner.status().healthy)
            elif operation == 'call':
                owner.call('mount', 'op', {}, timeout_seconds=1)
            else:
                owner.submit_owner('op', {})
        except ThreadedRuntimeError as error:
            errors.append(error.code)
    thread = threading.Thread(target=run)
    thread.start()
    assert sampled.wait(1)
    with owner._condition:
        continue_sample.set()
        assert not threading.Event().wait(0.06)
    thread.join(1)
    assert not thread.is_alive()
    if operation == 'status':
        assert result == [False]
    else:
        assert errors == ['NOT_READY']
        assert not owner._queue


def test_custom_clock_with_finite_lease_fails_closed_but_no_lease_works():
    owner = ThreadedServiceClientRuntime(lambda: None, monotonic_clock=lambda: 100.0,
                                         owner_extension_factory=lambda: Extension([]),
                                         cleanup_unknown=lambda: False,
                                         on_cleanup_sticky=lambda _: None)
    owner._state, owner._healthy = 'running', True
    owner._lease_deadline = 101.0
    assert not owner.status().healthy
    with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
        owner.call('mount', 'op', {})
    with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
        owner.submit_owner('op', {})
    assert not owner._queue
    owner._lease_deadline = None
    assert owner.status().healthy
    handle = owner.submit_owner('op', {})
    assert handle.cancel()


def test_finite_lease_requires_exact_raw_and_matching_native_clock(monkeypatch):
    import bt_api_py.service_runtime as service_runtime
    import bt_api_py.service_runtime_thread as module
    class Binding:
        registration_id = 'id'
        service_epoch = 'epoch'
        active = True
        required = True
        lease_deadline = module._NATIVE_MONOTONIC() + 5
    class ExactRaw(Raw):
        _require_unregister_ack = False
        def __init__(self, clock):
            super().__init__([])
            self._monotonic_clock = clock
            self.bindings = (Binding(),)
        def authenticated_peer_for_binding(self, binding):
            return None
    class OtherRaw(ExactRaw):
        pass
    monkeypatch.setattr(service_runtime, 'ServiceClientRuntime', ExactRaw)
    owner = ThreadedServiceClientRuntime(lambda: None, cleanup_unknown=lambda: False,
                                         on_cleanup_sticky=lambda _: None)
    owner._state, owner._healthy = 'running', True
    owner._capture(ExactRaw(module._NATIVE_MONOTONIC))
    assert owner.status().healthy
    owner._capture(ExactRaw(lambda: module._NATIVE_MONOTONIC()))
    assert not owner.status().healthy
    owner._capture(OtherRaw(module._NATIVE_MONOTONIC))
    assert not owner.status().healthy
    custom = ThreadedServiceClientRuntime(lambda: None, monotonic_clock=lambda: 0.0,
                                          cleanup_unknown=lambda: False,
                                          on_cleanup_sticky=lambda _: None)
    custom._state, custom._healthy = 'running', True
    custom._capture(ExactRaw(module._NATIVE_MONOTONIC))
    assert not custom.status().healthy


def test_raw_and_wrapper_default_clock_objects_match():
    import bt_api_py.service_runtime as service_runtime
    import bt_api_py.service_runtime_thread as module
    assert service_runtime.ServiceClientRuntime.__init__.__kwdefaults__['monotonic_clock'] is module._NATIVE_MONOTONIC
    assert ThreadedServiceClientRuntime.__init__.__kwdefaults__['monotonic_clock'] is module._NATIVE_MONOTONIC


def test_close_lock_timeout_does_not_request_close_or_reject_queue(monkeypatch):
    import bt_api_py.service_runtime_thread as module
    from bt_api_py.service_runtime_thread import _OwnerCall
    sampled = threading.Event()
    now, errors = [100.0], []
    monkeypatch.setattr(module, '_DEADLINE_CLOCK', lambda: sampled.set() or now[0])
    owner = ThreadedServiceClientRuntime(lambda: None)
    owner._state, owner._healthy = 'running', True
    call = _OwnerCall('queued', {})
    owner._queue.append(call)
    def close():
        try:
            owner.close(timeout_seconds=1)
        except ThreadedRuntimeError as error:
            errors.append(error.code)
    with owner._condition:
        thread = threading.Thread(target=close)
        thread.start()
        assert sampled.wait(1)
        now[0] = 102.0
    thread.join(1)
    assert not thread.is_alive()
    assert errors == ['CLOSE_TIMEOUT_BEFORE_START']
    assert not owner._stop and owner._healthy and owner._state == 'running'
    assert owner._cleanup_retry == 0 and list(owner._queue) == [call]
    assert call.state == 'queued' and call.error is None


def test_malformed_continuation_after_invoke_is_unknown():
    events = []
    malformed = OwnerOperationContinuation(lambda: events.append('late'), 0.01)
    object.__setattr__(malformed, 'delay_seconds', 0)
    class BadExtension(Extension):
        def invoke(self, operation, arguments):
            events.append('invoked')
            return malformed
    owner = ThreadedServiceClientRuntime(
        lambda: Raw(events), owner_extension_factory=lambda: BadExtension(events))
    owner.start()
    try:
        with pytest.raises(ThreadedRuntimeError, match='OWNER_OUTCOME_UNKNOWN'):
            owner.submit_owner('op', {}).result()
        assert events == ['invoked']
    finally:
        owner.close()


@pytest.mark.parametrize('deadline', [float('inf'), float('nan'), 10**10000, True],
                         ids=['inf', 'nan', 'huge', 'bool'])
def test_invalid_or_unrepresentable_native_lease_is_not_healthy(deadline):
    owner = ThreadedServiceClientRuntime(lambda: None,
                                         owner_extension_factory=lambda: Extension([]),
                                         cleanup_unknown=lambda: False,
                                         on_cleanup_sticky=lambda _: None)
    owner._state, owner._healthy = 'running', True
    owner._lease_clock_native_proven = True
    owner._lease_deadline = deadline
    assert not owner.status().healthy
    with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
        owner.submit_owner('op', {})
    assert not owner._queue


def test_default_custom_raw_with_finite_lease_retains_existing_behavior():
    events = []
    class Binding:
        registration_id = 'id'
        service_epoch = 'epoch'
        active = True
        required = True
        lease_deadline = 101.0
    class CustomRaw(Raw):
        def __init__(self):
            super().__init__(events)
            self.bindings = (Binding(),)
        def call(self, *args, **kwargs):
            events.append('call')
            return {'done': True}
    owner = ThreadedServiceClientRuntime(CustomRaw, monotonic_clock=lambda: 100.0)
    owner.start()
    try:
        assert owner.status().healthy
        assert owner.call('mount', 'op', {}, timeout_seconds=1) == {'done': True}
    finally:
        assert owner.close().state == 'closed'
    assert events == ['call', 'raw']


@pytest.mark.parametrize('position', ['first', 'last', 'alone'])
@pytest.mark.parametrize('invalid', ['missing', None, float('nan'), float('inf'), False, 0, -1, 'bad'],
                         ids=['missing', 'none', 'nan', 'inf', 'bool', 'zero', 'negative', 'text'])
def test_strict_capture_invalid_required_lease_is_not_no_lease(monkeypatch, position, invalid):
    import bt_api_py.service_runtime as service_runtime
    import bt_api_py.service_runtime_thread as module
    class Binding:
        registration_id = 'id'
        service_epoch = 'epoch'
        active = True
        required = True
        def __init__(self, value):
            if value != 'missing':
                self.lease_deadline = value
    class ExactRaw(Raw):
        _require_unregister_ack = False
        def __init__(self, bindings):
            super().__init__([])
            self._monotonic_clock = module._NATIVE_MONOTONIC
            self.bindings = bindings
        def authenticated_peer_for_binding(self, binding):
            return None
    valid = Binding(module._NATIVE_MONOTONIC() + 10)
    bad = Binding(invalid)
    bindings = {'first': (bad, valid), 'last': (valid, bad), 'alone': (bad,)}[position]
    monkeypatch.setattr(service_runtime, 'ServiceClientRuntime', ExactRaw)
    owner = ThreadedServiceClientRuntime(lambda: None, owner_extension_factory=lambda: Extension([]),
                                         cleanup_unknown=lambda: False, on_cleanup_sticky=lambda _: None)
    owner._state, owner._healthy = 'running', True
    owner._capture(ExactRaw(bindings))
    assert not owner.status().healthy
    with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
        owner.submit_owner('op', {})
    with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
        owner.call('mount', 'op', {})
    assert not owner._queue


def test_strict_capture_no_active_required_binding_allows_no_lease(monkeypatch):
    import bt_api_py.service_runtime as service_runtime
    import bt_api_py.service_runtime_thread as module
    class OptionalBinding:
        registration_id = 'id'
        service_epoch = 'epoch'
        active = True
        required = False
    class ExactRaw(Raw):
        _require_unregister_ack = False
        _monotonic_clock = staticmethod(module._NATIVE_MONOTONIC)
        def authenticated_peer_for_binding(self, binding):
            return None
    monkeypatch.setattr(service_runtime, 'ServiceClientRuntime', ExactRaw)
    raw = ExactRaw([])
    raw.bindings = (OptionalBinding(),)
    owner = ThreadedServiceClientRuntime(lambda: None, cleanup_unknown=lambda: False,
                                         on_cleanup_sticky=lambda _: None)
    owner._state, owner._healthy = 'running', True
    owner._capture(raw)
    assert owner.status().healthy
    assert owner._lease_deadline is None and not owner._lease_evidence_invalid


@pytest.mark.parametrize('operation', ['call', 'submit'])
def test_default_reentrant_lease_clock_close_cannot_enqueue(operation):
    owner = None
    def clock():
        owner.close()
        return 100.0
    owner = ThreadedServiceClientRuntime(
        lambda: None, monotonic_clock=clock, owner_extension_factory=lambda: Extension([]))
    owner._state, owner._healthy = 'running', True
    owner._lease_deadline = 101.0
    with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
        if operation == 'call':
            owner.call('mount', 'op', {})
        else:
            owner.submit_owner('op', {})
    assert owner._state == 'stopping' and owner._stop and not owner._queue


@pytest.mark.parametrize('field', ['active', 'required'])
@pytest.mark.parametrize('invalid', ['missing', None, 0, 'yes'],
                         ids=['missing', 'none', 'zero', 'text'])
def test_strict_capture_unknown_binding_flags_are_not_no_lease(monkeypatch, field, invalid):
    import bt_api_py.service_runtime as service_runtime
    import bt_api_py.service_runtime_thread as module
    class Binding:
        registration_id = 'id'
        service_epoch = 'epoch'
        lease_deadline = 101.0
        def __init__(self):
            if field != 'active' or invalid != 'missing':
                self.active = invalid if field == 'active' else True
            if field != 'required' or invalid != 'missing':
                self.required = invalid if field == 'required' else False
    class ExactRaw(Raw):
        _require_unregister_ack = False
        _monotonic_clock = staticmethod(module._NATIVE_MONOTONIC)
        def authenticated_peer_for_binding(self, binding):
            return None
    monkeypatch.setattr(service_runtime, 'ServiceClientRuntime', ExactRaw)
    raw = ExactRaw([])
    raw.bindings = (Binding(),)
    owner = ThreadedServiceClientRuntime(lambda: None, owner_extension_factory=lambda: Extension([]),
                                         cleanup_unknown=lambda: False, on_cleanup_sticky=lambda _: None)
    owner._state, owner._healthy = 'running', True
    owner._capture(raw)
    assert owner._lease_evidence_invalid and not owner.status().healthy
    with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
        owner.call('mount', 'op', {})
    with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
        owner.submit_owner('op', {})
    assert not owner._queue


def test_invalid_binding_flag_stops_following_binding_field_reads(monkeypatch):
    import bt_api_py.service_runtime as service_runtime
    import bt_api_py.service_runtime_thread as module
    events = []
    class Binding:
        registration_id = 'id'
        service_epoch = 'epoch'
        active = True
        required = None
        @property
        def mount(self):
            events.append('mount')
            return None
    class ExactRaw(Raw):
        _require_unregister_ack = False
        _monotonic_clock = staticmethod(module._NATIVE_MONOTONIC)
        def authenticated_peer_for_binding(self, binding):
            return None
    monkeypatch.setattr(service_runtime, 'ServiceClientRuntime', ExactRaw)
    raw = ExactRaw(events)
    raw.bindings = (Binding(),)
    owner = ThreadedServiceClientRuntime(lambda: None, cleanup_unknown=lambda: False,
                                         on_cleanup_sticky=lambda _: None)
    owner._state, owner._healthy = 'running', True
    owner._capture(raw)
    assert owner._lease_evidence_invalid and not owner.status().healthy
    assert events == []


def test_invalid_binding_after_maintenance_rejects_already_queued_call(monkeypatch):
    import bt_api_py.service_runtime as service_runtime
    import bt_api_py.service_runtime_thread as module
    events = []
    entered, release, notified = threading.Event(), threading.Event(), threading.Event()
    class Binding:
        registration_id = 'id'
        service_epoch = 'epoch'
        active = True
        required = True
        lease_deadline = module._NATIVE_MONOTONIC() + 10
    binding = Binding()
    class ExactRaw(Raw):
        _require_unregister_ack = False
        _monotonic_clock = staticmethod(module._NATIVE_MONOTONIC)
        def __init__(self):
            super().__init__(events)
            self.bindings = (binding,)
        def authenticated_peer_for_binding(self, item):
            return None
        def maintain(self):
            entered.set()
            assert release.wait(2)
            binding.required = None
    class ExtensionNeverInvoked(Extension):
        def invoke(self, operation, arguments):
            events.append('unexpected invoke')
    monkeypatch.setattr(service_runtime, 'ServiceClientRuntime', ExactRaw)
    owner = FakeRetainedRuntime(
        ExactRaw, owner_extension_factory=lambda: ExtensionNeverInvoked(events),
        cleanup_unknown=lambda: False, on_cleanup_sticky=lambda _: notified.set(),
    )
    owner._condition = OwnerWaitCondition(owner)
    owner.start()
    assert entered.wait(1)
    queued = owner.submit_owner('queued', {})
    release.set()
    with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
        queued.result()
    assert notified.wait(1)
    owner._thread.join(1)
    assert not owner._thread.is_alive()
    assert 'unexpected invoke' not in events


@pytest.mark.parametrize('problem', ['required_none', 'required_missing',
                                     'deadline_none', 'deadline_missing', 'deadline_nan'])
def test_post_close_invalid_lease_cannot_reuse_previous_empty_snapshot(problem):
    events = []
    class InvalidBinding:
        registration_id = 'id'
        service_epoch = 'epoch'
        active = True
        def __init__(self):
            if problem != 'required_missing':
                self.required = None if problem == 'required_none' else True
            if problem != 'deadline_missing':
                self.lease_deadline = float('nan') if problem == 'deadline_nan' else None
    class ChangesOnClose(Raw):
        def close(self):
            super().close()
            self.bindings = (InvalidBinding(),)
    owner = direct_runtime(events)
    raw, extension = ChangesOnClose(events), Extension(events)
    assert owner._bindings == () and owner._unknown_registration_count == 0
    assert_sticky(owner, raw, extension)
    assert events == ['extension', 'raw', 'callback']
    assert owner._lease_evidence_invalid


@pytest.mark.parametrize('stage', ['start', 'running'])
@pytest.mark.parametrize('problem', ['required_unknown', 'deadline_unknown'])
def test_observed_invalid_lease_prevents_all_later_cleanup(monkeypatch, stage, problem):
    import bt_api_py.service_runtime as service_runtime
    import bt_api_py.service_runtime_thread as module
    from bt_api_py.service_runtime_cleanup import runtime_cleanup_confirmed
    events = []
    entered, release, notified = threading.Event(), threading.Event(), threading.Event()
    class Binding:
        registration_id = 'id'
        service_epoch = 'epoch'
        active = True
        required = True
        lease_deadline = module._NATIVE_MONOTONIC() + 10
    binding = Binding()
    def invalidate():
        if problem == 'required_unknown':
            binding.required = None
        else:
            binding.lease_deadline = None
    if stage == 'start':
        invalidate()
    class ExactRaw(Raw):
        _require_unregister_ack = False
        _monotonic_clock = staticmethod(module._NATIVE_MONOTONIC)
        def __init__(self):
            super().__init__(events)
            self.bindings = (binding,)
        def authenticated_peer_for_binding(self, item):
            return None
        def maintain(self):
            entered.set()
            assert release.wait(2)
            invalidate()
    class NeverInvoked(Extension):
        def invoke(self, operation, arguments):
            events.append('unexpected invoke')
    raw, extension = ExactRaw(), NeverInvoked(events)
    monkeypatch.setattr(service_runtime, 'ServiceClientRuntime', ExactRaw)
    owner = FakeRetainedRuntime(
        lambda: raw, owner_extension_factory=lambda: extension,
        cleanup_unknown=lambda: False, on_cleanup_sticky=lambda _: notified.set(),
    )
    owner._condition = OwnerWaitCondition(owner)
    if stage == 'start':
        with pytest.raises(ThreadedRuntimeError):
            owner.start()
    else:
        owner.start()
        assert entered.wait(1)
        queued = owner.submit_owner('queued', {})
        release.set()
        with pytest.raises(ThreadedRuntimeError, match='NOT_READY'):
            queued.result()
    assert notified.wait(1)
    owner._thread.join(1)
    assert not owner._thread.is_alive()
    snapshot = owner.status()
    assert snapshot.state == 'close_failed' and not runtime_cleanup_confirmed(snapshot)
    assert snapshot.owner_cleanup_uncertainties is None
    assert owner._sticky_resources == (raw, extension)
    assert events == []  # Neither extension.close nor raw.close ran.
    raw.bindings = ()
    assert owner._capture(raw) is False
    assert owner._lease_evidence_invalid
    with pytest.raises(ThreadedRuntimeError, match='CLEANUP_FAILED'):
        owner.close()
    assert events == []


def test_expired_finite_lease_is_not_invalid_cleanup_evidence(monkeypatch):
    import bt_api_py.service_runtime as service_runtime
    import bt_api_py.service_runtime_thread as module
    events = []
    class Binding:
        registration_id = 'id'
        service_epoch = 'epoch'
        active = True
        required = True
        lease_deadline = module._NATIVE_MONOTONIC() - 1
    class ExactRaw(Raw):
        _require_unregister_ack = False
        _monotonic_clock = staticmethod(module._NATIVE_MONOTONIC)
        def authenticated_peer_for_binding(self, item):
            return None
        def close(self):
            super().close()
            self.bindings = ()
    monkeypatch.setattr(service_runtime, 'ServiceClientRuntime', ExactRaw)
    raw = ExactRaw(events)
    raw.bindings = (Binding(),)
    owner = direct_runtime(events)
    owner._state, owner._healthy = 'running', True
    assert owner._capture(raw) is True
    assert not owner._lease_evidence_invalid and not owner.status().healthy
    owner._run_sticky_cleanup(raw, Extension(events))
    assert events == ['extension', 'raw']
    assert not owner._cleanup_sticky


def test_local_invalid_evidence_stops_all_running_checks_without_foreign_reads():
    events = []
    owner = direct_runtime(events, unknown=lambda: events.append('guard') or False)
    owner._lease_evidence_invalid = True
    class CountingExtension(Extension):
        @property
        def cleanup_uncertainties(self):
            events.append('extension')
            return 0
    class CountingRaw(Raw):
        def __getattribute__(self, name):
            if name == 'sticky_cleanup_enabled':
                events.append('raw')
            return object.__getattribute__(self, name)
    with pytest.raises(_StickyCleanupSignal):
        owner._check_running_evidence(CountingRaw(events), CountingExtension(events))
    assert events == []


def test_guard_publishing_local_invalid_stops_before_resource_reads():
    events = []
    owner = None
    def guard():
        events.append('guard')
        owner._lease_evidence_invalid = True
        return False
    owner = direct_runtime(events, unknown=guard)
    class CountingExtension(Extension):
        @property
        def cleanup_uncertainties(self):
            events.append('extension')
            return 0
    with pytest.raises(_StickyCleanupSignal):
        owner._check_running_evidence(Raw(events), CountingExtension(events))
    assert events == ['guard']


def test_extension_getter_publishing_invalid_stops_before_next_guard_and_raw():
    events = []
    owner = direct_runtime(events, unknown=lambda: events.append('guard') or False)
    class InvalidatingExtension(Extension):
        @property
        def cleanup_uncertainties(self):
            events.append('extension')
            owner._lease_evidence_invalid = True
            return 0
    class CountingRaw(Raw):
        def __getattribute__(self, name):
            if name == 'sticky_cleanup_enabled':
                events.append('raw')
            return object.__getattribute__(self, name)
    with pytest.raises(_StickyCleanupSignal):
        owner._check_running_evidence(CountingRaw(events), InvalidatingExtension(events))
    assert events == ['guard', 'guard', 'extension']


def test_checked_action_finally_does_not_read_after_local_invalid():
    events = []
    owner = direct_runtime(events, unknown=lambda: events.append('guard') or False)
    class CountingExtension(Extension):
        @property
        def cleanup_uncertainties(self):
            events.append('extension')
            return 0
    class CountingRaw(Raw):
        def __getattribute__(self, name):
            if name == 'sticky_cleanup_enabled':
                events.append('raw')
            return object.__getattribute__(self, name)
    def action():
        events.clear()
        owner._lease_evidence_invalid = True
        events.append('invalid')
    with pytest.raises(_StickyCleanupSignal):
        owner._checked_action(CountingRaw(events), CountingExtension(events), action)
    assert events == ['invalid']
