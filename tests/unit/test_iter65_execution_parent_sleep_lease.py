"""Synthetic Registry clocks/journals; no real account, network or OS sleep."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from bt_api_py.execution_parent_grant import PORT, QUERY, SUBMIT
from bt_api_py.service_registry import ServiceDispatchAdmissionError, ServiceRegistry
from tests.unit.test_iter65_execution_parent_registry_grant import fixture as registry_fixture, call
from tests.unit.test_service_registry import _register, _next_request


def fixture(**kwargs):
    return registry_fixture(fresh_read=True, **kwargs)


@pytest.fixture
def fake_time(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(ServiceRegistry, '_execution_parent_mono_now', lambda self: now[0])
    return now


def renew(registry, identity, scope, reg):
    return registry.handle(identity.principal_ref, _next_request('service.renew', {
        'registration_id': reg['registration_id'], 'service_epoch': reg['service_epoch']},
        identity=identity, scope=scope))


def unregister(registry, identity, scope, reg):
    return registry.handle(identity.principal_ref, _next_request('service.unregister', {
        'registration_id': reg['registration_id'], 'service_epoch': reg['service_epoch']},
        identity=identity, scope=scope))


def register(registry, identity, scope, key):
    return _register(registry, identity=identity, scope=scope, principal=identity.principal_ref,
        instance_id='exec-a', port=PORT, scope_ref='scope-a', operations=(SUBMIT,), key=key)


def test_sleep_and_wall_rollback_cannot_revive_submit_or_renew(fake_time):
    registry, wall, identity, scope, _, _, response = fixture()
    try:
        reg = response['result']
        assert call(registry, identity, scope, reg)[0]['ok']
        fake_time[0] += 11
        wall[0] -= 50
        assert not call(registry, identity, scope, reg)[0]['ok']
        assert not renew(registry, identity, scope, reg)['ok']
        assert not register(registry, identity, scope, 'registration-a')['ok']
    finally:
        registry.close()


def test_slow_registration_never_issues_late_token(fake_time, monkeypatch):
    original = ServiceRegistry._register_impl
    def delayed(self, principal, request):
        result = original(self, principal, request)
        fake_time[0] += 11
        return result
    monkeypatch.setattr(ServiceRegistry, '_register_impl', delayed)
    registry, _, identity, scope, _, _, response = fixture()
    try:
        assert not response['ok']
        assert not register(registry, identity, scope, 'registration-a')['ok']
        assert not registry._execution_parent_monotonic_leases
    finally:
        registry.close()


def test_commit_then_exception_cannot_reconstruct_registration_token(fake_time, monkeypatch):
    original = ServiceRegistry._register_impl
    def uncertain(self, principal, request):
        original(self, principal, request)
        raise OSError('synthetic lost commit outcome')
    monkeypatch.setattr(ServiceRegistry, '_register_impl', uncertain)
    registry, _, identity, scope, _, _, response = fixture()
    try:
        assert not response['ok']
        monkeypatch.setattr(ServiceRegistry, '_register_impl', original)
        assert not register(registry, identity, scope, 'registration-a')['ok']
        assert not registry._execution_parent_monotonic_leases
    finally:
        registry.close()


def test_reply_loss_replay_never_extends_existing_deadline(fake_time):
    registry, _, identity, scope, _, _, response = fixture()
    try:
        reg = response['result']
        old = registry._execution_parent_monotonic_leases[reg['registration_id']]
        fake_time[0] += 2
        replay = register(registry, identity, scope, 'registration-a')
        assert replay['ok'] and replay['result']['registration_id'] == reg['registration_id']
        assert registry._execution_parent_monotonic_leases[reg['registration_id']] is old
        fake_time[0] = old[5]
        assert not register(registry, identity, scope, 'registration-a')['ok']
    finally:
        registry.close()


def test_renewal_commit_lost_and_slow_cannot_restore_old_token(fake_time, monkeypatch):
    registry, _, identity, scope, _, _, response = fixture()
    try:
        reg = response['result']
        original = registry._renew_or_unregister
        def uncertain(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('synthetic lost renewal outcome')
        monkeypatch.setattr(registry, '_renew_or_unregister', uncertain)
        assert not renew(registry, identity, scope, reg)['ok']
        monkeypatch.setattr(registry, '_renew_or_unregister', original)
        assert not renew(registry, identity, scope, reg)['ok']
        assert not call(registry, identity, scope, reg)[0]['ok']
    finally:
        registry.close()


def test_slow_renewal_does_not_revive_expired_token(fake_time, monkeypatch):
    registry, _, identity, scope, _, _, response = fixture()
    try:
        reg = response['result']
        original = registry._renew_or_unregister
        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            fake_time[0] += 11
            return result
        monkeypatch.setattr(registry, '_renew_or_unregister', delayed)
        assert not renew(registry, identity, scope, reg)['ok']
        assert not call(registry, identity, scope, reg)[0]['ok']
    finally:
        registry.close()


def test_two_concurrent_renewals_remain_bound(fake_time):
    registry, _, identity, scope, _, _, response = fixture()
    try:
        reg = response['result']
        fake_time[0] += 1
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: renew(registry, identity, scope, reg), range(2)))
        assert all(r['ok'] for r in results)
        assert call(registry, identity, scope, reg)[0]['ok']
    finally:
        registry.close()


def test_locked_request_queue_consumes_deadline(fake_time):
    registry, _, identity, scope, _, _, response = fixture()
    try:
        reg = response['result']
        entered = threading.Event()
        original = registry._execution_parent_mono_now
        def sample():
            value = original()
            entered.set()
            return value
        registry._execution_parent_mono_now = sample
        with ThreadPoolExecutor(max_workers=1) as pool:
            with registry._lock:
                pending = pool.submit(renew, registry, identity, scope, reg)
                assert entered.wait(2)
                fake_time[0] += 11
            assert not pending.result(timeout=2)['ok']
    finally:
        registry.close()


def test_lease_revoke_and_identical_grant_reinstall_do_not_revive(fake_time):
    registry, _, identity, scope, grant, received, response = fixture()
    try:
        reg = response['result']
        assert call(registry, identity, scope, reg)[0]['ok']
        permit = received[-1][0].dispatch_admission
        assert registry.revoke_execution_parent(grant)
        registry.configure_execution_parents(grants=(grant,))
        assert not call(registry, identity, scope, reg)[0]['ok']
        assert not renew(registry, identity, scope, reg)['ok']
        with pytest.raises(ServiceDispatchAdmissionError), permit.scope():
            pytest.fail('reinstalled grant revived old permit')
        permit.close()
    finally:
        registry.close()


def test_clock_fault_latches_and_query_only_cannot_bypass_it(monkeypatch):
    import bt_api_py.service_registry as module
    registry, _, identity, scope, _, _, response = fixture()
    try:
        old_id = module._PARENT_SLEEP_CLOCK_ID
        monkeypatch.setattr(module, '_PARENT_SLEEP_CLOCK_ID', -1)
        assert not call(registry, identity, scope, response['result'])[0]['ok']
        monkeypatch.setattr(module, '_PARENT_SLEEP_CLOCK_ID', old_id)
        assert not call(registry, identity, scope, response['result'])[0]['ok']
        assert not renew(registry, identity, scope, response['result'])['ok']
    finally:
        registry.close()
    monkeypatch.setattr(module, '_PARENT_SLEEP_CLOCK_ID', None)
    reader, _, ident, read_scope, _, _, result = fixture(operation=QUERY)
    try:
        assert not result['ok']
    finally:
        reader.close()


def test_nonfresh_query_registration_does_not_sample_parent_clock(monkeypatch):
    import bt_api_py.service_registry as module
    original = module._PARENT_SLEEP_CLOCK_ID
    monkeypatch.setattr(module, '_PARENT_SLEEP_CLOCK_ID', None)
    registry, _, identity, scope, _, _, response = registry_fixture(operation=QUERY)
    try:
        assert response['ok']
        assert not registry._execution_parent_clock_failed
        assert call(registry, identity, scope, response['result'], QUERY)[0]['ok']
        monkeypatch.setattr(module, '_PARENT_SLEEP_CLOCK_ID', original)
        assert register(registry, identity, scope, 'fresh-write-key')['ok']
    finally:
        registry.close()


def test_unrelated_service_renewal_does_not_sample_fresh_read_wall(tmp_path, monkeypatch):
    from tests.unit.test_service_runtime import LocalTransport, _config, _registry, _runtime
    registry = _registry(tmp_path / 'unrelated.sqlite', [])
    monkeypatch.setattr(registry, '_execution_parent_read_wall_now',
                        lambda: pytest.fail('unrelated renewal sampled fresh read wall clock'))
    runtime = _runtime(_config(), lambda *_: LocalTransport(registry))
    try:
        runtime.start()
        assert runtime.renew().bindings[0].active
    finally:
        runtime.close()
        registry.close()


def test_query_only_read_lease_cannot_revive_after_sleep_or_wall_rollback(fake_time):
    registry, wall, identity, scope, _, _, response = fixture(operation=QUERY)
    try:
        assert response['ok']
        reg = response['result']
        assert call(registry, identity, scope, reg, QUERY)[0]['ok']
        fake_time[0] += 11
        wall[0] -= 50
        assert not call(registry, identity, scope, reg, QUERY)[0]['ok']
        assert not renew(registry, identity, scope, reg)['ok']
    finally:
        registry.close()


def test_query_only_read_lease_detects_wall_rollback_before_expiry(fake_time):
    registry, wall, identity, scope, _, _, response = fixture(operation=QUERY)
    try:
        reg = response['result']
        assert call(registry, identity, scope, reg, QUERY)[0]['ok']
        wall[0] -= 1
        assert not call(registry, identity, scope, reg, QUERY)[0]['ok']
        wall[0] += 1
        assert not call(registry, identity, scope, reg, QUERY)[0]['ok']
        assert not renew(registry, identity, scope, reg)['ok']
    finally:
        registry.close()


def test_query_only_renewal_requires_original_live_read_token(fake_time):
    registry, wall, identity, scope, _, _, response = fixture(operation=QUERY)
    try:
        reg = response['result']
        original = registry._execution_parent_read_leases[reg['registration_id']]
        fake_time[0] += 2
        wall[0] += 2
        assert renew(registry, identity, scope, reg)['ok']
        updated = registry._execution_parent_read_leases[reg['registration_id']]
        assert updated is not original and updated[5] > original[5]
        assert call(registry, identity, scope, reg, QUERY)[0]['ok']
        fake_time[0] = updated[5]
        assert not renew(registry, identity, scope, reg)['ok']
        assert not call(registry, identity, scope, reg, QUERY)[0]['ok']
    finally:
        registry.close()


def test_query_read_token_is_not_restored_after_hard_registry_failure(fake_time):
    registry, _, identity, scope, _, _, response = fixture(operation=QUERY)
    try:
        reg = response['result']
        assert call(registry, identity, scope, reg, QUERY)[0]['ok']
        registry.set_ready(False)
        registry.set_ready(True)
        assert not call(registry, identity, scope, reg, QUERY)[0]['ok']
        assert not renew(registry, identity, scope, reg)['ok']
    finally:
        registry.close()


def test_query_read_renewal_cannot_span_local_resource_revocation(fake_time):
    from types import MappingProxyType
    registry, _, identity, scope, _, _, response = fixture(operation=QUERY)
    try:
        reg = response['result']
        original_grants = dict(registry._grants)
        assert registry.revoke_principal(identity.principal_ref)
        assert not renew(registry, identity, scope, reg)['ok']
        restored = {principal: tuple(list(grants)) for principal, grants in original_grants.items()}
        registry._grants = MappingProxyType(restored)
        assert not renew(registry, identity, scope, reg)['ok']
        assert not call(registry, identity, scope, reg, QUERY)[0]['ok']
    finally:
        registry.close()


def test_other_principal_revocation_does_not_revoke_current_query_lease(fake_time):
    registry, _, identity, scope, _, _, response = fixture(operation=QUERY, two=True)
    try:
        reg = response['result']
        assert registry.revoke_principal('principal-b')
        assert call(registry, identity, scope, reg, QUERY)[0]['ok']
        assert renew(registry, identity, scope, reg)['ok']
    finally:
        registry.close()


def test_unregister_expiry_and_close_remove_tokens(fake_time):
    registry, wall, identity, scope, _, received, response = fixture()
    try:
        reg = response['result']
        assert call(registry, identity, scope, reg)[0]['ok']
        permit = received[-1][0].dispatch_admission
        assert unregister(registry, identity, scope, reg)['ok']
        assert not registry._execution_parent_monotonic_leases
        with pytest.raises(ServiceDispatchAdmissionError), permit.scope():
            pytest.fail('unregistered permit survived')
        permit.close()
        for i in range(30):
            result = register(registry, identity, scope, 'cycle-' + str(i))
            assert result['ok'], result
            assert unregister(registry, identity, scope, result['result'])['ok']
            assert not registry._execution_parent_monotonic_leases
        result = register(registry, identity, scope, 'expiry')
        assert result['ok'] and registry._execution_parent_monotonic_leases
        wall[0] += 11
        with registry._transaction():
            registry._expire_sessions(wall[0])
        assert not registry._execution_parent_monotonic_leases
    finally:
        registry.close()
    assert not registry._execution_parent_monotonic_leases


@pytest.mark.skipif(not hasattr(__import__('os'), 'fork'), reason='requires POSIX fork')
def test_fork_child_rejects_without_touching_inherited_clock_lock():
    import os
    import signal
    registry, _, _, _, _, _, response = fixture()
    assert response['ok']
    locked, release = threading.Event(), threading.Event()
    def holder():
        with registry._lock, registry._execution_parent_clock_lock:
            locked.set()
            assert release.wait(5)
    thread = threading.Thread(target=holder)
    thread.start()
    assert locked.wait(2)
    try:
        pid = os.fork()
        if pid == 0:
            signal.alarm(2)
            try:
                registry._execution_parent_mono_now()
            except ServiceDispatchAdmissionError:
                try:
                    registry.close()
                except ServiceDispatchAdmissionError:
                    os._exit(0)
            os._exit(1)
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    finally:
        release.set()
        thread.join(2)
        registry.close()


def test_direct_registration_ignores_unavailable_parent_clock(monkeypatch, tmp_path):
    import bt_api_py.service_registry as module
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability
    from tests.unit.test_service_registry import _identity, _strategy_scope
    monkeypatch.setattr(module, '_PARENT_SLEEP_CLOCK_ID', None)
    identity = _identity(principal_ref='principal-a', actor_ref='strategy-a')
    scope = _strategy_scope(strategy='strategy-a')
    ops = frozenset({'direct.reserve'})
    resource = ResourceGrant('gateway', 'service.direct_write', 'scope',
                             _strategy_scope(strategy='strategy-a', run_id=None), ops)
    registry = ServiceRegistry(tmp_path / 'ordinary.sqlite', service_id='gateway-service',
        capabilities=(ServiceCapability('gateway', 'service.direct_write', ops, ops),),
        grants={'principal-a': (PrincipalGrant(identity, (resource,)),)},
        policy_version='p', requirements_version='r', lease_seconds=10, clock=lambda: 100.0)
    registry.register_handler('gateway', 'direct.reserve', lambda _ctx, _args: {'accepted': True})
    try:
        result = _register(registry, identity=identity, scope=scope, principal='principal-a',
            instance_id='gateway', port='service.direct_write', scope_ref='scope', operations=('direct.reserve',))
        assert result['ok'], result
        assert renew(registry, identity, scope, result['result'])['ok']
        reply = registry.handle('principal-a', _next_request('direct.reserve', {
            'registration_id': result['result']['registration_id'], 'service_epoch': result['result']['service_epoch'],
            'instance_id': 'gateway', 'arguments': {}}, identity=identity, scope=scope))
        assert reply['ok'], reply
    finally:
        registry.close()


def test_real_clock_backward_detection_latches():
    registry, _, _, _, _, _, response = fixture()
    try:
        assert response['ok']
        registry._execution_parent_clock_last = registry._execution_parent_mono_now() + 100
        with pytest.raises(ServiceDispatchAdmissionError, match='UNAVAILABLE'):
            registry._execution_parent_mono_now()
        registry._execution_parent_clock_last = None
        with pytest.raises(ServiceDispatchAdmissionError, match='UNAVAILABLE'):
            registry._execution_parent_mono_now()
    finally:
        registry.close()


def test_native_sleep_clock_selection():
    import sys
    import time
    from types import BuiltinFunctionType
    import bt_api_py.service_registry as module
    assert type(module._RELEASE_SLEEP_CLOCK) is BuiltinFunctionType
    if sys.platform == 'darwin':
        assert module._PARENT_SLEEP_CLOCK_ID == time.CLOCK_MONOTONIC
    elif sys.platform.startswith('linux'):
        assert module._PARENT_SLEEP_CLOCK_ID == getattr(time, 'CLOCK_BOOTTIME', None)
    else:
        assert module._PARENT_SLEEP_CLOCK_ID is None


def test_reopen_does_not_recreate_prior_session(fake_time, tmp_path):
    path = tmp_path / 'parent-registry.sqlite'
    first, wall, identity, scope, _, _, initial = fixture(path=path)
    assert initial['ok'], initial
    old = initial['result']
    first.close()
    second, _, _, _, _, _, replay = fixture(path=path)
    try:
        assert not replay['ok']
        assert not renew(second, identity, scope, old)['ok']
        assert not second._execution_parent_monotonic_leases
    finally:
        second.close()


def test_intake_check_rechecks_sleep_lease_inside_local_scope(fake_time):
    registry, _, identity, scope, _, received, response = fixture(system_clock=True)
    try:
        assert call(registry, identity, scope, response['result'])[0]['ok']
        permit = received[-1][0].dispatch_admission
        with permit.intake_scope() as check:
            check()
            fake_time[0] += 11
            with pytest.raises(ServiceDispatchAdmissionError, match='NOT_REGISTERED'):
                check()
        permit.close()
    finally:
        registry.close()


def test_changed_expiry_cannot_reconstitute_lease(fake_time):
    registry, _, identity, scope, _, _, response = fixture()
    try:
        reg = response['result']
        with registry._transaction():
            registry._db.execute('UPDATE sessions SET expires_at=expires_at+100 WHERE registration_id=?',
                                 (reg['registration_id'],))
        assert not call(registry, identity, scope, reg)[0]['ok']
        assert not renew(registry, identity, scope, reg)['ok']
    finally:
        registry.close()


def test_registration_queue_consumes_request_deadline(fake_time):
    registry, _, identity, scope, _, _, response = fixture()
    try:
        assert unregister(registry, identity, scope, response['result'])['ok']
        entered = threading.Event()
        original = registry._execution_parent_mono_now
        def sample():
            value = original()
            entered.set()
            return value
        registry._execution_parent_mono_now = sample
        with ThreadPoolExecutor(max_workers=1) as pool:
            with registry._lock:
                pending = pool.submit(register, registry, identity, scope, 'queued')
                assert entered.wait(2)
                fake_time[0] += 11
            assert not pending.result(timeout=2)['ok']
        assert not register(registry, identity, scope, 'queued')['ok']
        assert not registry._execution_parent_monotonic_leases
    finally:
        registry.close()


def test_unready_transition_permanently_revokes_existing_parent_lease(fake_time):
    registry, _, identity, scope, _, _, response = fixture()
    try:
        reg = response['result']
        registry.set_ready(False)
        registry.set_ready(True)
        assert not registry._execution_parent_monotonic_leases
        assert not call(registry, identity, scope, reg)[0]['ok']
        assert not renew(registry, identity, scope, reg)['ok']
        assert not register(registry, identity, scope, 'registration-a')['ok']
    finally:
        registry.close()
