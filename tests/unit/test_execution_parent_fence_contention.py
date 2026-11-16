"""Actual temporary journal locks and synthetic local registry; no account calls."""
import threading
import time

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.execution_parent_grant import PORT, SUBMIT
from bt_api_py.execution_parent_service_host import ParentServiceHost, parse_config
from tests.unit.test_iter65_execution_parent_service_host import setup_config
from tests.unit.test_service_registry import _register


@pytest.fixture
def running_parent(tmp_path):
    raw, _, _ = setup_config(tmp_path)
    original = ParentServiceHost(parse_config(raw))
    original.start()
    assert not original.host.healthy
    original.close()
    raw['storage'].update(writer_mode='single_host_cooperative', require_existing=True)
    parent = ParentServiceHost(parse_config(raw))
    parent.start()
    try:
        host, registry = parent.host, parent.host._registry
        assert host.healthy and registry.ready
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-principal', 'strategy-a')
        scope = WireScope('paper', account_ref='account-a', strategy_ref='strategy-a', run_id='run-a')
        registered = _register(registry, identity=identity, scope=scope, principal=identity.principal_ref,
            instance_id='exec-a', port=PORT, scope_ref='scope-a', operations=(SUBMIT,), key='contention')
        assert registered['ok'], registered
        yield parent, registry, registered['result']
    finally:
        parent.close()


def hold_lock(lock):
    entered, release = threading.Event(), threading.Event()
    def hold():
        with lock:
            entered.set()
            release.wait(5)
    thread = threading.Thread(target=hold)
    thread.start()
    assert entered.wait(2)
    return thread, release


def refresh_bounded(host):
    errors = []
    def refresh():
        try:
            host._refresh_registry_readiness()
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=refresh)
    thread.start()
    thread.join(1)
    return thread, errors


@pytest.mark.parametrize('lock_name', ['host', 'store'])
def test_real_fence_lock_contention_pauses_then_recovers(running_parent, lock_name):
    parent, registry, registration = running_parent
    journal = parent.journal
    lock = journal._lock if lock_name == 'host' else journal._store._lock
    token = registry._execution_parent_monotonic_leases[registration['registration_id']]
    holder, release = hold_lock(lock)
    refresh = None
    try:
        refresh, errors = refresh_bounded(parent.host)
        assert not refresh.is_alive() and not errors
        assert not registry.ready and registry._parent_retry_unready
        assert registry._execution_parent_monotonic_leases[registration['registration_id']] is token
    finally:
        release.set()
        holder.join(2)
        if refresh is not None:
            refresh.join(2)
    assert not holder.is_alive() and not refresh.is_alive()
    parent.host._refresh_registry_readiness()
    assert registry.ready and not registry._parent_retry_unready
    assert registry._execution_parent_monotonic_leases[registration['registration_id']] is token


def test_failed_lease_capability_wins_over_lock_retry(running_parent):
    parent, registry, _ = running_parent
    journal = parent.journal
    original_lease = journal._issued_lease
    holder, release = hold_lock(journal._lock)
    refresh = None
    try:
        object.__setattr__(journal, '_issued_lease', object())
        refresh, errors = refresh_bounded(parent.host)
        assert not refresh.is_alive() and not errors
        assert not registry.ready and not registry._parent_retry_unready
        assert not registry._execution_parent_monotonic_leases
    finally:
        object.__setattr__(journal, '_issued_lease', original_lease)
        release.set()
        holder.join(2)
        if refresh is not None:
            refresh.join(2)
    assert not holder.is_alive() and not refresh.is_alive()


def test_real_lease_maintenance_host_lock_contention_recovers(running_parent, monkeypatch):
    parent, registry, registration = running_parent
    journal = parent.journal
    entered, release = threading.Event(), threading.Event()
    original_verify = journal._verify_paths
    def pause_maintenance(*args, **kwargs):
        if threading.current_thread().name == 'atomic-intake-lease':
            entered.set()
            assert release.wait(3)
        return original_verify(*args, **kwargs)
    monkeypatch.setattr(journal, '_verify_paths', pause_maintenance)
    token = registry._execution_parent_monotonic_leases[registration['registration_id']]
    refresh = None
    try:
        assert entered.wait(2)
        refresh, errors = refresh_bounded(parent.host)
        assert not refresh.is_alive() and not errors
        assert not registry.ready and registry._parent_retry_unready
        assert registry._execution_parent_monotonic_leases[registration['registration_id']] is token
    finally:
        release.set()
        if refresh is not None:
            refresh.join(2)
        monkeypatch.setattr(journal, '_verify_paths', original_verify)
    deadline = time.monotonic() + 1
    while not registry.ready and time.monotonic() < deadline:
        parent.host._refresh_registry_readiness()
        if not registry.ready:
            time.sleep(0.01)
    assert registry.ready and not registry._parent_retry_unready
    assert registry._execution_parent_monotonic_leases[registration['registration_id']] is token


def test_changed_journal_path_hard_revokes_registration(running_parent):
    parent, registry, _ = running_parent
    marker, = parent.config.journal_root.glob('*.owner')
    moved = marker.with_name(marker.name + '.moved')
    marker.rename(moved)
    try:
        parent.host._refresh_registry_readiness()
        assert not registry.ready and not registry._parent_retry_unready
        assert not registry._execution_parent_monotonic_leases
    finally:
        moved.rename(marker)


def test_real_atomic_service_cannot_use_legacy_boolean_fallback(running_parent, monkeypatch):
    parent, registry, _ = running_parent
    service, = parent.host._atomic_intake_services
    monkeypatch.setattr(service, 'local_intake_status', None)
    monkeypatch.setattr(type(service), 'local_intake_available', property(lambda _: True))
    parent.host._refresh_registry_readiness()
    assert not registry.ready and not registry._parent_retry_unready
    assert not registry._execution_parent_monotonic_leases
