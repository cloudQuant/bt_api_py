"""Owner queue passes the exact installed permit to its native dispatcher."""
from types import SimpleNamespace

from bt_api_gateway.direct_delegation import GatewayDelegationPermit
from bt_api_py.gateway_direct_owner import GatewayDispatchCommand
from tests.test_iter63_gateway_direct_owner import Harness, create, fence, wait_for


def test_owner_preserves_delegation_permit_through_queue(tmp_path):
    harness = Harness(tmp_path)
    seen = []
    def dispatch(*args, request_fence, delegation_permit):
        seen.append((args, request_fence, delegation_permit))
        harness.finished.set()
    harness.dispatch = dispatch
    owner = create(harness)
    assert owner.start(2)
    permit = object.__new__(GatewayDelegationPermit)
    permit._operation = 'direct.dispatch'
    permit._command = SimpleNamespace(strategy_scope='strategy', request_key='key',
        actor_principal='principal', payload_digest='a' * 64)
    command = GatewayDispatchCommand('strategy', 'key', 'principal', 'reservation', 'a' * 64, 1)
    assert owner.enqueue(command, request_fence=fence, finalize=harness.finalize('done'),
                         delegation_permit=permit) == 'QUEUED'
    assert harness.finished.wait(2)
    wait_for(lambda: harness.finalized == ['done'])
    assert len(seen) == 1 and seen[0][2] is permit
    assert owner.close(2)
