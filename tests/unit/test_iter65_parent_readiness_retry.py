"""Synthetic Host/Registry lease contention; assumed writer fence is test-only."""
import threading
import time

import pytest
from tests.unit.test_iter65_atomic_intake_host import make_host, IDENTITY, SCOPE, PORT, SUBMIT
from tests.unit.test_service_registry import _register, _next_request


def setup_host(tmp_path, monkeypatch):
    host, created, _, _ = make_host(tmp_path, monkeypatch)
    host.start()
    registry = host._registry
    result = _register(registry, identity=IDENTITY, scope=SCOPE, principal=IDENTITY.principal_ref,
        instance_id='exec-a', port=PORT, scope_ref='scope-a', operations=(SUBMIT,), key='retry-race')
    assert result['ok'], result
    return host, created, registry, result['result']


def race_refresh(host, created, monkeypatch, during=None):
    entered, release = threading.Event(), threading.Event()
    failures = []
    original = created['journal'].intake_now_ns
    def blocked_sample():
        value = original()
        if threading.current_thread().name == 'synthetic-readiness-refresh':
            entered.set()
            assert release.wait(4)
        return value
    monkeypatch.setattr(created['journal'], 'intake_now_ns', blocked_sample)
    def refresh():
        try:
            host._refresh_registry_readiness()
        except BaseException as error:
            failures.append(error)
    thread = threading.Thread(target=refresh, name='synthetic-readiness-refresh')
    thread.start()
    try:
        assert entered.wait(2)
        service = created['service']
        with service._lease_lock:
            prior = service._lease_revision
        until = time.monotonic() + 3
        while time.monotonic() < until:
            with service._lease_lock:
                if service._lease_revision != prior:
                    break
            time.sleep(0.01)
        else:
            raise AssertionError('synthetic maintenance did not publish renewal')
        if during is not None:
            during()
    finally:
        release.set()
        thread.join(3)
        monkeypatch.setattr(created['journal'], 'intake_now_ns', original)
    assert not thread.is_alive() and not failures


def submit(registry, registration):
    return registry.handle(IDENTITY.principal_ref, _next_request(SUBMIT, {
        'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
        'instance_id': 'exec-a', 'arguments': {}}, identity=IDENTITY, scope=SCOPE))


def test_real_host_retry_pauses_and_resumes_same_original_lease(tmp_path, monkeypatch):
    host, created, registry, reg = setup_host(tmp_path, monkeypatch)
    try:
        token = registry._execution_parent_monotonic_leases[reg['registration_id']]
        race_refresh(host, created, monkeypatch)
        assert not registry.ready and registry._parent_retry_unready
        assert registry._execution_parent_monotonic_leases[reg['registration_id']] is token
        assert not submit(registry, reg)['ok']
        host._refresh_registry_readiness()
        assert registry.ready and not registry._parent_retry_unready
        with registry._lock:
            row = registry._db.execute('SELECT * FROM sessions WHERE registration_id=?',
                                       (reg['registration_id'],)).fetchone()
            assert registry._execution_parent_live_lease(row) is token
    finally:
        host.close()


@pytest.mark.parametrize('fault', ['other_component', 'writer_fence', 'atomic_deadline'])
def test_retry_with_other_hard_failure_revokes_token(tmp_path, monkeypatch, fault):
    host, created, registry, reg = setup_host(tmp_path, monkeypatch)
    original_components = host._components
    gate_held = False
    try:
        if fault == 'other_component':
            from dataclasses import replace
            failed = threading.Event()
            # A second synthetic component is evaluated in the same Host
            # observation, after the actual Atomic component has retried.
            other = replace(original_components[0], readiness=lambda: not failed.is_set())
            host._components = [*original_components, other]
            def inject():
                failed.set()
        elif fault == 'writer_fence':
            def inject():
                monkeypatch.setattr(type(created['service']), 'local_intake_available', property(lambda _: False))
        else:
            def inject():
                nonlocal gate_held
                gate_held = created['service']._intake_gate.acquire(timeout=2)
                assert gate_held
                with created['service']._lease_lock:
                    created['service']._lease_valid_until = 0
        race_refresh(host, created, monkeypatch, during=inject)
        assert not registry.ready and not registry._parent_retry_unready
        assert not registry._execution_parent_monotonic_leases
        assert not submit(registry, reg)['ok']
    finally:
        if gate_held:
            created['service']._intake_gate.release()
        host._components = original_components
        host.close()


def test_retry_then_real_fault_is_hard_even_when_already_unready(tmp_path, monkeypatch):
    host, created, registry, reg = setup_host(tmp_path, monkeypatch)
    try:
        race_refresh(host, created, monkeypatch)
        assert registry._parent_retry_unready
        created['service']._fail_lease()
        assert not registry.ready and not registry._parent_retry_unready
        assert not registry._execution_parent_monotonic_leases
        assert not submit(registry, reg)['ok']
    finally:
        host.close()


def test_retry_recovery_never_revives_expired_parent_lease(tmp_path, monkeypatch):
    host, created, registry, reg = setup_host(tmp_path, monkeypatch)
    try:
        token = registry._execution_parent_monotonic_leases[reg['registration_id']]
        race_refresh(host, created, monkeypatch)
        monkeypatch.setattr(registry, '_execution_parent_mono_now', lambda: token[5] + 1)
        host._refresh_registry_readiness()
        assert registry.ready
        assert not submit(registry, reg)['ok']
        with registry._lock:
            row = registry._db.execute('SELECT * FROM sessions WHERE registration_id=?',
                                       (reg['registration_id'],)).fetchone()
            assert registry._execution_parent_live_lease(row) is None
    finally:
        host.close()


def test_retry_then_grant_reinstall_cannot_revive_old_lease(tmp_path, monkeypatch):
    host, created, registry, reg = setup_host(tmp_path, monkeypatch)
    try:
        race_refresh(host, created, monkeypatch)
        grant = created['service'].grants[0]
        assert registry.revoke_execution_parent(grant)
        registry.configure_execution_parents(grants=(grant,))
        host._refresh_registry_readiness()
        assert registry.ready and not submit(registry, reg)['ok']
        with registry._lock:
            row = registry._db.execute('SELECT * FROM sessions WHERE registration_id=?',
                                       (reg['registration_id'],)).fetchone()
            assert registry._execution_parent_live_lease(row) is None
    finally:
        host.close()


def test_valid_observation_cannot_overwrite_concurrent_lease_failure(tmp_path, monkeypatch):
    host, created, registry, reg = setup_host(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    errors = []
    original = host._components_readiness_state
    def paused():
        state = original()
        assert state == 'valid'
        entered.set()
        assert release.wait(3)
        return state
    monkeypatch.setattr(host, '_components_readiness_state', paused)
    def refresh():
        try:
            host._refresh_registry_readiness()
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=refresh)
    try:
        thread.start()
        assert entered.wait(2)
        created['service']._fail_lease()
        release.set()
        thread.join(3)
        assert not thread.is_alive() and not errors
        assert not registry.ready and not registry._parent_retry_unready
        assert not registry._execution_parent_monotonic_leases
        assert not submit(registry, reg)['ok']
    finally:
        release.set()
        thread.join(3)
        host.close()


def test_retry_pause_blocks_even_restricted_gateway_write():
    from tests.unit.test_iter63_gateway_cancel_admission import _fixture
    registry, _, _, invoke, captured = _fixture()
    try:
        assert invoke('direct.cancel')[0]['ok']
        captured[-1][0].dispatch_admission.close()
        registry._set_parent_retry_unready()
        assert not invoke('direct.cancel')[0]['ok']
    finally:
        registry.close()


def test_concurrent_close_cannot_revive_valid_observation(tmp_path, monkeypatch):
    host, created, registry, _ = setup_host(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    errors = []
    original = host._components_readiness_state
    def paused():
        state = original()
        assert state == 'valid'
        entered.set()
        assert release.wait(3)
        return state
    monkeypatch.setattr(host, '_components_readiness_state', paused)
    def refresh():
        try:
            host._refresh_registry_readiness()
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=refresh)
    try:
        thread.start()
        assert entered.wait(2)
        host.close()
        assert registry._closed and not registry._execution_parent_monotonic_leases
        release.set()
        thread.join(3)
        assert not thread.is_alive()
        assert registry._closed and not registry._execution_parent_monotonic_leases
    finally:
        release.set()
        thread.join(3)
        host.close()


def test_hard_unready_generation_defeats_stale_valid_component_sample(tmp_path, monkeypatch):
    from dataclasses import replace
    host, _, registry, _ = setup_host(tmp_path, monkeypatch)
    old_components = host._components
    healthy = threading.Event(); healthy.set()
    added = replace(old_components[0], readiness=lambda: healthy.is_set())
    host._components = [*old_components, added]
    entered, release = threading.Event(), threading.Event()
    errors = []
    original = host._components_readiness_state
    def paused():
        state = original()
        assert state == 'valid'
        entered.set()
        assert release.wait(3)
        return state
    monkeypatch.setattr(host, '_components_readiness_state', paused)
    def refresh():
        try:
            host._refresh_registry_readiness()
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=refresh)
    try:
        thread.start()
        assert entered.wait(2)
        healthy.clear()
        registry.set_ready(False)
        release.set()
        thread.join(3)
        assert not thread.is_alive() and not errors
        assert not registry.ready and not registry._parent_retry_unready
        assert not registry._execution_parent_monotonic_leases
    finally:
        release.set()
        thread.join(3)
        host._components = old_components
        host.close()
