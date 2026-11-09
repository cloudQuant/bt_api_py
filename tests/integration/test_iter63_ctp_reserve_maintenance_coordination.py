"""Real CTP session, return maintenance, Registry and owner; synthetic native API."""
from dataclasses import replace
import threading

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.integration.test_iter63_ctp_gateway_return_maintenance import real_owner, _pair
from tests.unit.test_iter63_gateway_direct_service import _order
from tests.unit.test_service_registry import _next_request, _register


def test_real_ctp_maintenance_short_window_waits_before_authenticated_reservation(tmp_path, monkeypatch):
    started, release, completed = threading.Event(), threading.Event(), threading.Event()
    holder = {}

    def factory():
        generator = real_owner.__wrapped__(tmp_path, monkeypatch)
        maintenance, session, _native, journal, instrument, _helper = next(generator)
        _pair(maintenance)
        assert maintenance.status()['reservation_open']
        buffer = session.return_buffer
        offer = buffer.offer
        def blocked_offer(*, limit=256):
            started.set()
            assert release.wait(3)
            return offer(limit=limit)
        buffer.offer = blocked_offer
        holder.update(maintenance=maintenance, journal=journal, instrument=instrument)
        dispatcher = GatewayDirectDispatcher(journal, 'ctp', maintenance._ledger._guard,
            lambda *_: None, lambda _: ('anchor',), lambda _: True)
        def close():
            try:
                next(generator)
            except StopIteration:
                pass
        return GatewayDirectOwnerResources(dispatcher, journal, close,
            maintain=maintenance.poll, maintenance_interval_seconds=100)

    owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=2, busy_timeout_seconds=2)
    assert owner.start(3) and started.wait(2)
    registry = None
    thread = None
    try:
        maintenance, journal = holder['maintenance'], holder['journal']
        assert maintenance.status() == {'status': 'MAINTENANCE', 'reservation_open': False}
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
        scope = WireScope('domain', account_ref=journal.account_scope, strategy_ref='strategy', run_id='run')
        ops = frozenset({'direct.reserve'})
        registry = ServiceRegistry(':memory:', service_id='gateway-service',
            capabilities=(ServiceCapability('gateway', 'service.direct_write', ops, ops),),
            grants={'principal': (PrincipalGrant(identity, (ResourceGrant(
                'gateway', 'service.direct_write', 'scope', replace(scope, run_id=None), ops),)),)},
            policy_version='p', requirements_version='r')
        binding = GatewayDirectServiceBinding(journal,
            {'principal': GatewayDirectGrant('strategy', frozenset({holder['instrument'].native_symbol}))},
            owner=owner, reservation_fence=maintenance.reservation_fence, reservation_owner_serialized=True)
        registry.register_handler('gateway', 'direct.reserve',
            lambda context, args: binding.handle('direct.reserve', context=context, arguments=args))
        registered = _register(registry, principal='principal', identity=identity, scope=scope,
            instance_id='gateway', port='service.direct_write', scope_ref='scope', operations=tuple(ops))
        assert registered['ok']
        rec = registered['result']
        args = {'request_key': 'one', 'payload': _order('one', symbol=holder['instrument'].native_symbol,
                 account=journal.account_scope), 'ttl_seconds': 10}
        results = []
        def send():
            try:
                results.append(registry.handle('principal', _next_request('direct.reserve', {
                    'registration_id': rec['registration_id'], 'service_epoch': rec['service_epoch'],
                    'instance_id': 'gateway', 'arguments': args}, identity=identity, scope=scope)))
            finally:
                completed.set()
        thread = threading.Thread(target=send)
        thread.start()
        assert not completed.wait(.1)
        release.set()
        assert completed.wait(2)
        assert results[0]['ok'] and results[0]['result']['status'] == 'OK', results
        assert maintenance.status() == {'status': 'HEALTHY', 'reservation_open': True}
        assert journal.get('strategy', 'one', 'principal').status == 'RESERVED'
    finally:
        release.set()
        if thread is not None:
            thread.join(3)
        if registry is not None:
            registry.close()
        assert owner.close(3)
