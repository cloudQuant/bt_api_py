"""Registry mutation invalidation; no native provider or account access."""
import threading
import time

import pytest

from bt_api_py.gateway_dispatch_claim import DispatchClaimError, DispatchCommandBinding, GatewayDispatchClaimCoordinator
from tests.unit.test_service_registry import _identity, _make_registry, _next_request, _register, _strategy_scope


def _epoch(coordinator):
    return coordinator._domains['registry'][0]


def test_registration_renew_expiry_ready_and_close_invalidate_without_enabling_claim(tmp_path):
    now = [100.0]
    registry = _make_registry(tmp_path / 'registry.sqlite', clock=lambda: now[0])
    coordinator = GatewayDispatchClaimCoordinator('account-a', 'owner')
    registry.attach_dispatch_claim_coordinator(coordinator, account_ref='account-a', owner_generation='owner')
    try:
        baseline = _epoch(coordinator)
        registration = _register(registry)['result']
        assert _epoch(coordinator) > baseline
        payload = {key: registration[key] for key in ('registration_id', 'service_epoch')}
        before = _epoch(coordinator)
        now[0] = 101.0
        assert registry.handle('curve-a', _next_request('service.renew', payload,
            identity=_identity(), scope=_strategy_scope()))['ok']
        assert _epoch(coordinator) > before
        before = _epoch(coordinator)
        registry.set_ready(False)
        assert _epoch(coordinator) > before
        registry.set_ready(True)
        before = _epoch(coordinator)
        now[0] = 112.0
        assert registry.active_registration_ids(instance_id='account-instance') == frozenset()
        assert _epoch(coordinator) > before
        assert coordinator._domains['registry'][1] == 'UNAVAILABLE'
        with pytest.raises(DispatchClaimError, match='COVERAGE_UNAVAILABLE'):
            coordinator.capture()
    finally:
        before = _epoch(coordinator)
        registry.close()
        assert _epoch(coordinator) > before


def test_registry_rejects_fake_or_mismatched_coordinator_and_poison_is_fail_closed(tmp_path):
    registry = _make_registry(tmp_path / 'registry.sqlite')
    coordinator = GatewayDispatchClaimCoordinator('account-a', 'owner')
    try:
        with pytest.raises(ValueError):
            registry.attach_dispatch_claim_coordinator(GatewayDispatchClaimCoordinator._for_test('account-a', 'owner'),
                account_ref='account-a', owner_generation='owner')
        with pytest.raises(ValueError):
            registry.attach_dispatch_claim_coordinator(coordinator, account_ref='wrong', owner_generation='owner')
        registry.attach_dispatch_claim_coordinator(coordinator, account_ref='account-a', owner_generation='owner')
        with pytest.raises(ValueError):
            registry.attach_dispatch_claim_coordinator(coordinator, account_ref='account-a', owner_generation='owner')
        with pytest.raises(RuntimeError, match='synthetic'):
            with registry._transaction():
                raise RuntimeError('synthetic')
        # A rolled-back Registry operation ends UNAVAILABLE (not poisoned):
        # claims stay refused until the next successful Registry change.
        assert coordinator._domains['registry'][4] is False
        assert coordinator._domains['registry'][1] == 'UNAVAILABLE'
        # An unavailable claim domain cannot stop an operator from making the
        # Registry unready; it remains unavailable to any claim.
        registry.set_ready(False)
        assert registry.ready is False
        with pytest.raises(DispatchClaimError):
            coordinator.capture()
    finally:
        registry.close()


def test_trusted_principal_revocation_invalidates_then_denies_existing_registration(tmp_path):
    registry = _make_registry(tmp_path / 'registry.sqlite')
    coordinator = GatewayDispatchClaimCoordinator('account-a', 'owner')
    registry.attach_dispatch_claim_coordinator(coordinator, account_ref='account-a', owner_generation='owner')
    try:
        registration = _register(registry)['result']
        before = _epoch(coordinator)
        assert registry.revoke_principal('curve-a') is True
        assert _epoch(coordinator) > before
        payload = {key: registration[key] for key in ('registration_id', 'service_epoch')}
        reply = registry.handle('curve-a', _next_request('service.renew', payload,
            identity=_identity(), scope=_strategy_scope()))
        assert reply['ok'] is False
        assert coordinator._domains['registry'][1] == 'UNAVAILABLE'
    finally:
        registry.close()


def test_unready_does_not_wait_for_unrelated_hung_native_thread(tmp_path):
    registry = _make_registry(tmp_path / 'registry.sqlite')
    coordinator = GatewayDispatchClaimCoordinator('account-a', 'owner')
    registry.attach_dispatch_claim_coordinator(coordinator, account_ref='account-a', owner_generation='owner')
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def native():
        entered.set()
        release.wait(3)
    def unready():
        registry.set_ready(False)
        finished.set()
    worker = threading.Thread(target=native)
    control = threading.Thread(target=unready)
    try:
        worker.start()
        assert entered.wait(1)
        control.start()
        assert finished.wait(1)
        assert registry.ready is False
    finally:
        release.set()
        worker.join(2)
        control.join(2)
        registry.close()


def test_real_registry_revoke_after_synthetic_claim_does_not_wait_for_native(tmp_path):
    registry = _make_registry(tmp_path / 'registry.sqlite')
    coordinator = GatewayDispatchClaimCoordinator._for_test('account-a', 'owner')
    # Test-only injection tests B's linearization mechanics. Production attach
    # explicitly rejects this coordinator and cannot enable claims.
    for domain in ('registry', 'market', 'parent_facts', 'control', 'session_source', 'risk'):
        token = coordinator.begin_change(domain)
        coordinator.finish_change(token, 'NOT_SELECTED' if domain == 'risk' else 'READY')
    with registry._lock:
        registry._dispatch_claim_coordinators = (coordinator,)
    def prepare(key):
        binding = DispatchCommandBinding('account-a', 'owner', 'journal', 'strategy', 'curve-a',
            'run', key, 'reservation-' + key, 'a' * 64, 'b' * 64, 'marker-' + key)
        before, after = coordinator.capture(), coordinator.capture()
        return coordinator.prepare(binding, before, after, deadline_ns=time.monotonic_ns() + 1_000_000_000)
    accepted, stale = prepare('accepted'), prepare('stale')
    entered, release, revoked = threading.Event(), threading.Event(), threading.Event()
    def native():
        coordinator.try_claim(accepted)
        entered.set()
        assert release.wait(3)
    def revoke():
        assert registry.revoke_principal('curve-a')
        revoked.set()
    worker, control = threading.Thread(target=native), threading.Thread(target=revoke)
    try:
        worker.start()
        assert entered.wait(1)
        control.start()
        assert revoked.wait(1)
        assert worker.is_alive()  # The synthetic native call is still blocked.
        with pytest.raises(DispatchClaimError, match='DOMAIN_UNAVAILABLE'):
            coordinator.try_claim(stale)
    finally:
        release.set()
        worker.join(2)
        control.join(2)
        registry.close()
