"""Runtime bridge configuration, source checking and lifecycle tests."""
from types import SimpleNamespace
import threading

import pytest

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_run_contract import EventKind, RuntimeEvent
from bt_api_runtime.events import EventStore
from bt_api_runtime.telemetry_relay import TelemetryRelay
from bt_api_py.runtime_monitor_bridge import RuntimeMonitorBridgeError
from tests.integration.test_runtime_monitor_ingress import Clock, _bridge, _control, _deployment


class _Runtime:
    def __init__(self, healthy=True):
        self.healthy = healthy
        self.close_attempts = 0
    def start(self, **kwargs):
        return SimpleNamespace(state='running', healthy=self.healthy)
    def close(self, **kwargs):
        self.close_attempts += 1
        return SimpleNamespace(state='closed', thread_alive=False, cleanup_failures=0,
                               unregister_uncertainties=0, unknown_registration_count=0,
                               owner_cleanup_uncertainties=0)
    def bounded_status(self, **kwargs):
        raise AssertionError('unverified event reached transport')


def test_refuses_wrong_scope_unstored_sequence_and_forged_code(tmp_path):
    _, doc, keys = _deployment(tmp_path)
    store = EventStore(tmp_path / 'events', Clock())
    bridge = _bridge(doc, keys, store)
    bridge._runtime = _Runtime()
    bridge.start()
    try:
        zero = RuntimeEvent(0, EventKind.COMMAND_ACCEPTED, None, None,
                            '2026-01-01T00:00:00+00:00', None, {})
        with pytest.raises(RuntimeMonitorBridgeError, match='DELIVERY_UNAVAILABLE'):
            bridge.append(zero)
        event = store.append(EventKind.COMMAND_ACCEPTED)
        object.__setattr__(event, 'code', 'private-secret')
        with pytest.raises(RuntimeMonitorBridgeError, match='DELIVERY_UNAVAILABLE'):
            bridge.append(event)
        errors = []
        def other_thread():
            try:
                bridge.append(event)
            except RuntimeMonitorBridgeError as exc:
                errors.append(str(exc))
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()
        assert errors == ['WRONG_OWNER']
        with pytest.raises(RuntimeMonitorBridgeError, match='INVALID_CONFIGURATION'):
            _bridge(doc, keys, store, scope=WireScope('synthetic-domain'))
        peer = WireIdentity(WireRole.SERVICE, 'some-principal', 'wrong-service')
        with pytest.raises(RuntimeMonitorBridgeError, match='INVALID_CONFIGURATION'):
            _bridge(doc, keys, store, peer=peer)
        keys['producer'] = (keys['producer'][0], keys['gateway'][1])
        with pytest.raises(RuntimeMonitorBridgeError, match='INVALID_CONFIGURATION'):
            _bridge(doc, keys, store)
        keys['producer'] = (b'short', b'short')
        with pytest.raises(RuntimeMonitorBridgeError, match='INVALID_CONFIGURATION'):
            _bridge(doc, keys, store)
    finally:
        bridge.close()
        store.close()


def test_unhealthy_start_is_not_ready_and_close_is_repeatable(tmp_path):
    _, doc, keys = _deployment(tmp_path)
    store = EventStore(tmp_path / 'events', Clock())
    bridge = _bridge(doc, keys, store)
    fake = _Runtime(healthy=False)
    bridge._runtime = fake
    try:
        with pytest.raises(RuntimeMonitorBridgeError, match='START_FAILED'):
            bridge.start()
        assert bridge.state == 'close_uncertain'
        with pytest.raises(RuntimeMonitorBridgeError, match='UNAVAILABLE'):
            bridge.start()
        bridge.close()
        assert bridge.state == 'closed'
        bridge.close()
        assert fake.close_attempts == 1
        with pytest.raises(RuntimeMonitorBridgeError, match='UNAVAILABLE'):
            bridge.start()
    finally:
        store.close()


def test_close_uncertainty_can_be_retried(tmp_path):
    _, doc, keys = _deployment(tmp_path)
    store = EventStore(tmp_path / 'events', Clock())
    bridge = _bridge(doc, keys, store)
    class RetryRuntime(_Runtime):
        def close(self, **kwargs):
            if self.close_attempts == 0:
                self.close_attempts += 1
                raise TimeoutError('sensitive detail')
            return super().close(**kwargs)
    bridge._runtime = RetryRuntime()
    try:
        bridge.start()
        with pytest.raises(RuntimeMonitorBridgeError, match='^CLEANUP_UNCERTAIN$'):
            bridge.close()
        assert bridge.state == 'close_uncertain'
        bridge.close()
        assert bridge.state == 'closed'
    finally:
        store.close()


def test_unavailable_monitor_does_not_block_runtime_control(tmp_path):
    _, doc, keys = _deployment(tmp_path)
    store = EventStore(tmp_path / 'events', Clock())
    bridge = _bridge(doc, keys, store)  # not started; explicit unavailable path
    relay = TelemetryRelay(bridge, enabled=True, cooldown_s=0)
    service = _control(tmp_path / 'control', store, relay)
    try:
        assert service.unit_action('start', 'alpha', reason='test').status == 'APPLIED'
        assert relay.stats()['queued'] > 0
        assert relay.poll() == 0
        assert relay.stats()['drop_reasons']['monitor_error'] == 1
        assert service.unit_action('stop', 'alpha', reason='test').status == 'APPLIED'
    finally:
        service.close()
        relay.close()
        bridge.close()
        store.close()
