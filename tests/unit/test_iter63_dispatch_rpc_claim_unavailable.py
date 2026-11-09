"""Gateway RPC does not queue PLACE while complete claim proof is absent."""
from dataclasses import replace

import pytest
from bt_api_base.direct_write_protocol import NativeOrderIdentity
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_dispatch import _identity
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.test_iter63_gateway_direct_owner import Harness, create
from tests.unit.test_iter63_gateway_direct_service import _order
from tests.unit.test_service_registry import _next_request, _register


@pytest.mark.parametrize('state', ['RESERVED', 'DISPATCHING', 'SENT', 'UNKNOWN', 'RELEASED', 'EXPIRED'])
def test_dispatch_rpc_keeps_original_record_and_never_queues_without_claim(tmp_path, state, monkeypatch):
    harness = Harness(tmp_path)
    owner = create(harness)
    assert owner.start(2)
    journal = harness.journal
    clock = [100.0]
    journal._clock = lambda: clock[0]
    row = journal.reserve('strategy', 'one', 'principal', _order('one', symbol='X', account='account'), ttl_seconds=1)
    native = NativeOrderIdentity('SYN', 'native', {'trading_day':'20300101','front_id':1}, 1)
    identity_mapping = _identity(native, 'SYN')
    if state in {'DISPATCHING', 'SENT', 'UNKNOWN'}:
        row = journal.begin_dispatch('strategy', 'one', row.reservation_id, row.digest, 'principal',
            row.version, identity_mapping, ('key',)).record
        if state != 'DISPATCHING':
            row = journal.finish('strategy', 'one', row.reservation_id, row.digest, 'principal',
                row.version, identity_mapping, ('key',), state)
    elif state == 'RELEASED':
        row = journal.release('strategy', 'one', row.reservation_id, row.digest, 'principal', 'release', row.version)
    elif state == 'EXPIRED':
        clock[0] = 102.0
        row = journal.expire('strategy', 'one', 'principal')
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
    operations = frozenset({'direct.dispatch'})
    registry = ServiceRegistry(':memory:', service_id='gateway',
        capabilities=(ServiceCapability('gateway', 'service.direct_write', operations, operations),),
        grants={'principal': (PrincipalGrant(identity, (ResourceGrant('gateway', 'service.direct_write',
            'scope', replace(scope, run_id=None), operations),)),)}, policy_version='p', requirements_version='r')
    binding = GatewayDirectServiceBinding(journal,
        {'principal': GatewayDirectGrant('strategy', frozenset({'X'}))}, owner=owner)
    registry.register_handler('gateway', 'direct.dispatch',
        lambda ctx, args: binding.handle('direct.dispatch', context=ctx, arguments=args))
    calls = []
    monkeypatch.setattr(owner, 'enqueue', lambda *args, **kwargs: calls.append((args, kwargs)))
    try:
        rec = _register(registry, principal='principal', identity=identity, scope=scope,
            instance_id='gateway', port='service.direct_write', scope_ref='scope', operations=tuple(operations))['result']
        response = registry.handle('principal', _next_request('direct.dispatch', {
            'registration_id':rec['registration_id'], 'service_epoch':rec['service_epoch'], 'instance_id':'gateway',
            'arguments':{'request_key':'one', 'reservation_id':row.reservation_id, 'digest':row.digest,
                         'expected_version':row.version}}, identity=identity, scope=scope))
        assert response['ok'], response
        if state == 'RESERVED':
            assert response['result'] == {'status':'REQUEST_REJECTED', 'code':'DISPATCH_CLAIM_UNAVAILABLE'}
        else:
            assert response['result']['status'] == 'OK'
            assert response['result']['dispatch_queue_state'] == 'OBSERVED'
            assert response['result']['record']['state'] == state
            assert response['result']['record']['reservation_id'] == row.reservation_id
            assert response['result']['record']['digest'] == row.digest
        assert calls == []
    finally:
        registry.close()
        assert owner.close(2)
