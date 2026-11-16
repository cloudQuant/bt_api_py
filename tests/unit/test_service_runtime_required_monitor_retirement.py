"""Heartbeat-only phased unregister retains the original uncertain transport."""
import threading

import pytest

from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_py.service_runtime import RegisteredBinding, ServiceClientRuntime, ServiceRuntimeError
from test_gateway_heartbeat_host import _ACCOUNT_SCOPE, _producer_config


class Transport:
    def __init__(self):
        self.close_calls = 0
        self.fail_close = False

    def close(self):
        self.close_calls += 1
        if self.fail_close:
            raise RuntimeError('synthetic close failure')


def _runtime(*, require_ack=False, sticky=False):
    identity = WireIdentity(WireRole.SERVICE, 'synthetic-producer-a', 'producer-a-service')
    kwargs = ({'cleanup_unknown': lambda: False, 'on_cleanup_sticky': lambda: None} if sticky else {})
    runtime = ServiceClientRuntime(_producer_config('tcp://127.0.0.1:9876'), identity=identity,
        scope_resolver=lambda _: _ACCOUNT_SCOPE, peer_resolver=lambda _: identity,
        transport_factory=lambda *_: None, require_unregister_ack=require_ack,
        requested_operations={'upstream.telemetry': frozenset({'monitor.heartbeat'})}, **kwargs)
    binding = RegisteredBinding('upstream.telemetry', 'synthetic-monitor-ingress', 'monitor-ingress',
        'monitor-ingress-account-v1', _ACCOUNT_SCOPE, 'registration', 'epoch',
        frozenset({'monitor.heartbeat'}), 1.0, 'policy', 'requirements', 'monitor-connect', 0.01,
        lease_deadline=1.0)
    transport = Transport()
    runtime._bindings = [binding]
    runtime._transports = {(binding.service_id, binding.transport_ref): transport}
    runtime._state = 'registered'
    return runtime, binding, transport


def test_bad_ack_retains_exact_binding_transport_then_confirmed_ack_closes(monkeypatch):
    runtime, binding, transport = _runtime()
    requests = []

    def rpc(service, profile, scope, op, payload, **kwargs):
        requests.append((op, payload))
        if len(requests) == 1:
            return {'unregistered': True, 'registration_id': 'wrong'}
        return {'unregistered': True, 'registration_id': binding.registration_id}

    monkeypatch.setattr(runtime, '_rpc', rpc)
    assert runtime.retire_required_monitor_session() is False
    assert runtime.state == 'monitor_cleanup_pending'
    assert runtime._bindings == [binding] and runtime.open_transport_count == 1
    assert runtime.unregister_uncertainties == 1 and not transport.close_calls
    with pytest.raises(ServiceRuntimeError, match='SESSION_NOT_REGISTERED'):
        runtime.call('upstream.telemetry', 'monitor.heartbeat', {})
    with pytest.raises(ServiceRuntimeError, match='SESSION_NOT_RECOVERABLE'):
        runtime.recover()
    assert runtime.retire_required_monitor_session() is True
    assert runtime.state == 'closed' and runtime.unregister_uncertainties == 0
    assert runtime.open_transport_count == 0 and transport.close_calls == 1
    assert requests == [('service.unregister', {'registration_id': 'registration', 'service_epoch': 'epoch'})] * 2


@pytest.mark.parametrize('require_ack,expected', [(False, True), (True, False)])
def test_authenticated_stale_epoch_obeys_ack_policy(monkeypatch, require_ack, expected):
    runtime, binding, transport = _runtime(require_ack=require_ack)
    def stale(*args, **kwargs):
        kwargs['timing']['server_rejected'] = True
        raise ServiceRuntimeError('STALE_EPOCH')
    monkeypatch.setattr(runtime, '_rpc', stale)
    assert runtime.retire_required_monitor_session() is expected
    assert runtime.open_transport_count == (0 if expected else 1)
    assert runtime.unregister_uncertainties == (0 if expected else 1)
    if not expected:
        assert runtime._bindings == [binding] and transport.close_calls == 0


def test_unknown_registration_sticky_and_wrong_thread_fail_closed():
    runtime, binding, transport = _runtime()
    runtime._unknown_registration_count = 1
    with pytest.raises(ServiceRuntimeError, match='MONITOR_CLEANUP_REGISTRATION_UNKNOWN'):
        runtime.retire_required_monitor_session()
    assert runtime.state == 'monitor_cleanup_pending'
    assert runtime._bindings == [binding] and runtime.open_transport_count == 1 and transport.close_calls == 0
    sticky, _, _ = _runtime(sticky=True)
    with pytest.raises(ServiceRuntimeError, match='MONITOR_CLEANUP_UNAVAILABLE'):
        sticky.retire_required_monitor_session()
    other, _, _ = _runtime()
    errors = []
    def call():
        try:
            other.retire_required_monitor_session()
        except ServiceRuntimeError as error:
            errors.append(error.code)
    thread = threading.Thread(target=call)
    thread.start()
    thread.join(2)
    assert errors == ['WRONG_TRANSPORT_THREAD'] and other.state == 'registered'


def test_folded_uncertainty_and_nonheartbeat_session_cannot_be_retired():
    runtime, binding, transport = _runtime()
    runtime._unresolved_unregister_count = 1
    with pytest.raises(ServiceRuntimeError, match='MONITOR_CLEANUP_REGISTRATION_UNKNOWN'):
        runtime.retire_required_monitor_session()
    assert runtime.state == 'monitor_cleanup_pending' and runtime._bindings == [binding]
    assert runtime._unresolved_unregister_count == 1 and transport.close_calls == 0
    unrelated, _, other_transport = _runtime()
    unrelated._requested_operations = {'upstream.telemetry': frozenset({'events.append'})}
    with pytest.raises(ServiceRuntimeError, match='MONITOR_CLEANUP_UNAVAILABLE'):
        unrelated.retire_required_monitor_session()
    assert unrelated.state == 'registered' and other_transport.close_calls == 0


def test_unverified_stale_epoch_is_not_remote_inactive_proof(monkeypatch):
    runtime, binding, transport = _runtime()
    def stale(*args, **kwargs):
        raise ServiceRuntimeError('STALE_EPOCH')
    monkeypatch.setattr(runtime, '_rpc', stale)
    assert runtime.retire_required_monitor_session() is False
    assert runtime._bindings == [binding] and runtime.unregister_uncertainties == 1
    assert runtime.open_transport_count == 1 and transport.close_calls == 0


def test_transport_close_failure_retains_same_object_for_retry(monkeypatch):
    runtime, binding, transport = _runtime()
    transport.fail_close = True
    calls = []
    def rpc(*args, **kwargs):
        calls.append(args[3])
        return {'unregistered': True, 'registration_id': binding.registration_id}
    monkeypatch.setattr(runtime, '_rpc', rpc)
    with pytest.raises(ServiceRuntimeError, match='CLOSE_FAILED'):
        runtime.retire_required_monitor_session()
    assert runtime.state == 'close_failed' and runtime.open_transport_count == 1
    assert runtime.unregister_uncertainties == 0 and transport.close_calls == 1
    transport.fail_close = False
    assert runtime.retire_required_monitor_session() is True
    assert transport.close_calls == 2 and calls == ['service.unregister']
