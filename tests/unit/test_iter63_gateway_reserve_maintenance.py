"""Synthetic Registry and owner scheduling; no external account calls."""
from contextlib import contextmanager
from dataclasses import replace
from types import MappingProxyType
import threading

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import DirectCommandJournalError
from bt_api_py.gateway_direct_owner import GatewayDirectOwner
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding, GatewayReservationAdmissionDenied
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.test_iter63_gateway_direct_owner import Harness
from tests.unit.test_iter63_gateway_direct_service import _order
from tests.unit.test_service_registry import _next_request, _register


@pytest.mark.parametrize('scenario', ['normal', 'queued_timeout', 'running_timeout', 'postcheck_timeout', 'queued_revoke', 'running_revoke', 'non_ctp'])
def test_authenticated_reserve_coordinates_maintenance_and_authority(tmp_path, scenario):
    harness = Harness(tmp_path)
    maintenance_entered, maintenance_release = threading.Event(), threading.Event()
    fence_entered, fence_release, complete = threading.Event(), threading.Event(), threading.Event()
    final_check_passed, final_check_release = threading.Event(), threading.Event()
    opened = [False]
    fence_threads = []

    def factory():
        bundle = harness.factory()
        def maintain():
            maintenance_entered.set()
            assert maintenance_release.wait(4)
            opened[0] = True
        return replace(bundle, maintain=maintain, maintenance_interval_seconds=100)

    @contextmanager
    def reservation_fence(_context, _order_request):
        fence_threads.append(threading.get_ident())
        if not opened[0]:
            raise GatewayReservationAdmissionDenied()
        if scenario in {'running_timeout', 'running_revoke'}:
            fence_entered.set()
            assert fence_release.wait(4)
        yield

    owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=2, busy_timeout_seconds=2)
    assert owner.start(2) and maintenance_entered.wait(2)
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
    operations = frozenset({'direct.reserve'})
    registry = ServiceRegistry(':memory:', service_id='gateway-service',
        capabilities=(ServiceCapability('gateway', 'service.direct_write', operations, operations),),
        grants={'principal': (PrincipalGrant(identity, (ResourceGrant(
            'gateway', 'service.direct_write', 'scope', replace(scope, run_id=None), operations),)),)},
        policy_version='p', requirements_version='r')
    binding = GatewayDirectServiceBinding(harness.journal,
        {'principal': GatewayDirectGrant('strategy', frozenset({'X'}))}, owner=owner,
        reservation_fence=reservation_fence, reservation_owner_serialized=scenario != 'non_ctp')
    if scenario == 'postcheck_timeout':
        original_reserve = harness.journal.reserve
        def paused_commit(*args, **kwargs):
            original_check = kwargs['before_reservation_commit']
            def after_check():
                original_check()
                final_check_passed.set()
                assert final_check_release.wait(4)
            kwargs['before_reservation_commit'] = after_check
            return original_reserve(*args, **kwargs)
        harness.journal.reserve = paused_commit
    registry.register_handler('gateway', 'direct.reserve',
        lambda ctx, args: binding.handle('direct.reserve', context=ctx, arguments=args))
    registered = _register(registry, principal='principal', identity=identity, scope=scope,
        instance_id='gateway', port='service.direct_write', scope_ref='scope', operations=tuple(operations))
    assert registered['ok']
    rec = registered['result']
    def call(key):
        args = {'request_key': key, 'payload': _order(key, symbol='X', account='account'), 'ttl_seconds': 10}
        return registry.handle('principal', _next_request('direct.reserve', {
            'registration_id': rec['registration_id'], 'service_epoch': rec['service_epoch'],
            'instance_id': 'gateway', 'arguments': args}, identity=identity, scope=scope))
    results = []
    def reserve():
        try:
            results.append(call('one'))
        finally:
            complete.set()
    thread = threading.Thread(target=reserve)
    revoked = threading.Event()
    revoke_thread = None
    def revoke():
        with registry._lock:
            registry._grants = MappingProxyType({'principal': ()})
        revoked.set()
    try:
        thread.start()
        if scenario == 'non_ctp':
            assert complete.wait(2)
            assert results[0]['ok'] and results[0]['result']['code'] == 'ADMISSION_DENIED'
            assert fence_threads == [thread.ident]
            return
        assert not complete.wait(.1)
        if scenario == 'queued_revoke':
            revoke()
        if scenario != 'queued_timeout':
            maintenance_release.set()
        if scenario in {'running_timeout', 'running_revoke'}:
            assert fence_entered.wait(2)
        if scenario == 'postcheck_timeout':
            assert final_check_passed.wait(2)
            assert complete.wait(2)
            assert results[0]['ok'] and results[0]['result'] == {'status': 'UNKNOWN'}, results
            final_check_release.set()
            owner.submit_service_call(lambda: None).result(timeout_seconds=2)
            assert harness.journal.get('strategy', 'one', 'principal').status == 'RESERVED'
            return
        if scenario == 'running_revoke':
            revoke_thread = threading.Thread(target=revoke)
            revoke_thread.start()
            assert not revoked.wait(.1)  # Registry scope stays held through commit.
            fence_release.set()
        if scenario in {'queued_timeout', 'running_timeout'}:
            assert complete.wait(2)
            assert results[0]['ok'] and results[0]['result'] == {'status': 'UNKNOWN'}, results
            maintenance_release.set()
            fence_release.set()
            owner.submit_service_call(lambda: None).result(timeout_seconds=2)
            with pytest.raises(DirectCommandJournalError, match='NOT_FOUND'):
                harness.journal.get('strategy', 'one', 'principal')
        elif scenario == 'queued_revoke':
            assert complete.wait(2)
            assert results[0]['ok'] and results[0]['result'] == {'status': 'UNKNOWN'}, results
            with pytest.raises(DirectCommandJournalError, match='NOT_FOUND'):
                harness.journal.get('strategy', 'one', 'principal')
        else:
            assert complete.wait(2)
            assert results[0]['ok'] and results[0]['result']['status'] == 'OK', results
            assert harness.journal.get('strategy', 'one', 'principal').status == 'RESERVED'
            if scenario == 'normal':
                opened[0] = False  # Existing key is re-observed without a new reservation.
                replay = call('one')
                assert replay['ok'] and replay['result']['record']['reservation_id'] == results[0]['result']['record']['reservation_id']
                denied = call('two')
                assert denied['ok'] and denied['result']['code'] == 'ADMISSION_DENIED'
    finally:
        maintenance_release.set()
        fence_release.set()
        final_check_release.set()
        thread.join(3)
        if revoke_thread is not None:
            revoke_thread.join(3)
        registry.close()
        assert owner.close(2)
