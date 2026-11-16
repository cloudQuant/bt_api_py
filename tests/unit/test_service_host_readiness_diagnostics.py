"""Readiness observations are bounded and never probe components for summary."""

import threading
from types import SimpleNamespace

import pytest

from bt_api_py.service_components import _ATOMIC_READINESS_RETRY
from tests.unit.test_service_host import _account_worker_setup


@pytest.fixture
def host(tmp_path):
    host, *_ = _account_worker_setup(tmp_path, expose_instrument_metadata=True)
    host._specs = ()
    host._components = []

    class Registry:
        ready = False
        _hard_unready_generation = 0
        _lock = threading.RLock()

        def set_ready(self, value):
            self.ready = value

        def _set_parent_retry_unready(self):
            self.ready = False

    host._registry = Registry()
    return host


def component(callback, *, kind='gateway'):
    return SimpleNamespace(component=kind, instance_id='local-component', readiness=callback)


def test_false_exception_recovery_and_summary_do_not_probe(host):
    calls = []
    value = [False]

    def ready():
        calls.append(None)
        if isinstance(value[0], Exception):
            raise value[0]
        return value[0]

    host._components = [component(ready)]
    assert dict(host.summary['readiness']) == {'state': 'unknown', 'reason': None, 'last_failure': None}
    host._refresh_registry_readiness()
    assert not host._registry.ready
    assert dict(host.summary['readiness']) == {
        'state': 'failed', 'reason': 'component_false', 'last_failure': 'component_false'
    }
    assert dict(host.readiness_diagnostics) == dict(host.summary['readiness'])
    assert len(calls) == 1

    value[0] = RuntimeError('secret account path request')
    host._refresh_registry_readiness()
    diagnostic = dict(host.summary['readiness'])
    assert diagnostic == {'state': 'failed', 'reason': 'component_exception', 'last_failure': 'component_exception'}
    assert 'secret' not in repr(diagnostic)
    assert len(calls) == 2

    value[0] = True
    host._refresh_registry_readiness()
    assert host._registry.ready
    assert dict(host.summary['readiness']) == {
        'state': 'valid', 'reason': None, 'last_failure': 'component_exception'
    }
    assert len(calls) == 3


@pytest.mark.parametrize(('field', 'value', 'reason'), [
    ('_component_maintenance_faults', {'secret-id'}, 'component_maintenance'),
    ('_component_delay_faults', {'secret-id'}, 'component_delay'),
    ('_gateway_lease_faults', {'secret-id'}, 'gateway_lease'),
    ('_host_delay_faulted', True, 'host_delay'),
    ('_paper_monitor_faults', {'secret-id': 'secret-error'}, 'paper_monitor'),
])
def test_fault_categories_are_finite(host, field, value, reason):
    setattr(host, field, value)
    host._refresh_registry_readiness()
    assert dict(host.summary['readiness']) == {'state': 'failed', 'reason': reason, 'last_failure': reason}
    assert 'secret' not in repr(host.summary['readiness'])


def test_upstream_categories_and_invalid_callback(host):
    host._components = [component(lambda: None)]
    host._refresh_registry_readiness()
    assert host.summary['readiness']['reason'] == 'component_invalid'
    host._upstream = SimpleNamespace(healthy=False)
    host._refresh_registry_readiness()
    assert host.summary['readiness']['reason'] == 'upstream_unhealthy'

    class BrokenUpstream:
        @property
        def healthy(self):
            raise ValueError('secret upstream')

    host._upstream = BrokenUpstream()
    host._refresh_registry_readiness()
    assert host.summary['readiness']['reason'] == 'upstream_exception'


def test_retry_lease_and_recovery_preserve_last_failure(host):
    state = ['failed']

    class Intake:
        instance_id = 'local-component'
        def local_intake_status(self):
            return 'valid'

        def _lease_readiness_status(self):
            return state[0]

    host._atomic_intake_services = (Intake(),)
    host._components = [component(lambda: True, kind='execution')]
    host._refresh_registry_readiness()
    assert host.summary['readiness']['reason'] == 'atomic_lease_failed'
    state[0] = 'retry'
    host._refresh_registry_readiness()
    assert dict(host.summary['readiness']) == {
        'state': 'retry', 'reason': 'atomic_lease_retry', 'last_failure': 'atomic_lease_failed'
    }
    assert not host._registry.ready
    state[0] = 'valid'
    host._components = [component(lambda: _ATOMIC_READINESS_RETRY, kind='execution')]
    host._refresh_registry_readiness()
    assert host.summary['readiness']['reason'] == 'component_retry'
    host._components = [component(lambda: True, kind='execution')]
    host._refresh_registry_readiness()
    assert host._registry.ready
    assert dict(host.summary['readiness']) == {
        'state': 'valid', 'reason': None, 'last_failure': 'atomic_lease_failed'
    }


def test_intake_fence_and_execution_parent_categories(host):
    host._atomic_intake_services = (SimpleNamespace(local_intake_status=lambda: 'failed'),)
    host._refresh_registry_readiness()
    assert host.readiness_diagnostics['reason'] == 'intake_fence'

    class BrokenFence:
        def local_intake_status(self):
            raise ValueError('secret fence')

    host._atomic_intake_services = (BrokenFence(),)
    host._refresh_registry_readiness()
    assert host.readiness_diagnostics['reason'] == 'intake_fence_exception'
    host._atomic_intake_services = ()
    host._execution_parent_active = {('opaque',): object()}
    host._execution_parent_gateway_ready = lambda _: False
    host._refresh_registry_readiness()
    assert host.readiness_diagnostics['reason'] == 'execution_parent_gateway'


def test_fault_appearing_during_callback_is_observed(host):
    def ready():
        host._component_delay_faults.add('secret-id')
        return True

    host._components = [component(ready)]
    host._refresh_registry_readiness()
    assert host.readiness_diagnostics['reason'] == 'component_delay'
    assert not host._registry.ready


def test_atomic_recheck_exception_is_redacted(host):
    class Intake:
        def local_intake_status(self):
            return 'valid'

        def _lease_readiness_status(self):
            raise ValueError('secret lease')

    host._atomic_intake_services = (Intake(),)
    host._refresh_registry_readiness()
    assert dict(host.readiness_diagnostics) == {
        'state': 'failed', 'reason': 'atomic_recheck_exception', 'last_failure': 'atomic_recheck_exception'
    }
    assert not host._registry.ready


def test_hard_generation_change_overrides_stale_valid_sample(host):
    host._atomic_intake_services = (SimpleNamespace(local_intake_status=lambda: 'valid'),)

    def ready():
        host._registry._hard_unready_generation += 1
        return True

    host._components = [component(ready)]
    host._refresh_registry_readiness()
    assert host.readiness_diagnostics['reason'] == 'hard_unready_generation'
    assert not host._registry.ready
