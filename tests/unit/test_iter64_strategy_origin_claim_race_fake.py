"""Real claim coordinator with synchronous synthetic source and owner dependencies.

No worker, network, file lock, account or database is used. Other coverage
writers are synthetic; these tests do not certify the CTP composition.
"""
import threading
from types import SimpleNamespace

import pytest

from bt_api_base.claim_fence import DispatchClaimError
from bt_api_py.gateway_direct_owner import GatewayDirectOwner
from bt_api_py.gateway_dispatch_claim import GatewayDispatchClaimCoordinator, GatewayNativeDispatchClaimPort
from bt_api_py.gateway_strategy_origin_source import GatewayStrategyOriginSource
from tests.unit import test_iter64_origin_immutable_owner_fake as fake


@pytest.fixture
def scenario(monkeypatch):
    import bt_api_ctp.gateway_session as session_module
    import bt_api_gateway.direct_command_journal as journal_module
    import bt_api_gateway.session_guard as guard_module
    import bt_api_py._gateway_owner_guard_binding as binding_module

    monkeypatch.setattr(journal_module, 'GatewayDirectCommandJournal', fake.FakeJournal)
    monkeypatch.setattr(guard_module, 'LocalAccountSessionGuard', fake.FakeGuard)
    monkeypatch.setattr(session_module, 'CtpGatewaySession', fake.FakeSession)
    monkeypatch.setattr(binding_module, '_GatewayOwnerGuardBinding', fake.FakeBinding)
    owner = GatewayDirectOwner(lambda: None, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1)
    owner._thread = threading.current_thread()  # no thread is created or started
    coordinator = GatewayDispatchClaimCoordinator._for_test('account', 'generation')
    owner._bind_dispatch_lifecycle(coordinator, account_scope='account', generation='generation')
    for domain in coordinator._domains:
        token = coordinator.begin_change(domain)
        coordinator.finish_change(token, 'READY')
    context, order, key = fake._inputs()
    source, journal = GatewayStrategyOriginSource(instruments=(key,)), fake.FakeJournal()
    source._bind_owner(owner, journal, fake.FakeGuard(), fake.FakeSession(), coordinator, fake.FakeBinding())
    owner._binding = object()
    port = GatewayNativeDispatchClaimPort(coordinator, journal_id='journal', max_attempts=1)
    record = SimpleNamespace(account_scope='account', generation='generation', strategy_scope='strategy',
        principal='principal', request_key='req', reservation_id='reservation', digest='a' * 64)
    def validate(_):
        assert source.bound_to(owner, journal, coordinator)
        assert source.recheck(source.resolve(context, order))
    return SimpleNamespace(owner=owner, coordinator=coordinator, source=source, journal=journal,
                           port=port, record=record, validate=validate)


@pytest.mark.parametrize('change', ['source_close', 'owner_shutdown', 'registry_aba'])
def test_change_after_validation_before_claim_refuses_without_marker_or_native(scenario, change):
    coordinator = scenario.coordinator
    original = coordinator.try_claim
    def change_then_claim(candidate):
        if change == 'source_close': scenario.source.close()
        elif change == 'owner_shutdown': scenario.owner._fence_dispatch_shutdown()
        else:
            token = coordinator.begin_change('registry')
            coordinator.finish_change(token, 'READY')
        return original(candidate)
    coordinator.try_claim = change_then_claim
    calls = []
    with pytest.raises(DispatchClaimError, match='DISPATCH_CLAIM_BUSY'):
        claim = scenario.port.claim_native(scenario.record, None, scenario.validate)
        calls.append(claim)
    assert calls == []  # no marker or native callback is invoked by this harness


def test_close_after_claim_keeps_existing_claim_in_flight(scenario):
    claim = scenario.port.claim_native(scenario.record, None, scenario.validate)
    scenario.owner._fence_dispatch_shutdown()
    assert scenario.port.native_check(claim)() is True
    scenario.port.retire(claim)
    with pytest.raises(DispatchClaimError, match='DOMAIN_UNAVAILABLE'):
        scenario.coordinator.capture()


def test_other_owner_or_generation_is_not_same_lifecycle_writer(scenario):
    other_owner = GatewayDirectOwner(lambda: None, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1)
    assert not scenario.source.bound_to(other_owner, scenario.journal, scenario.coordinator)
    other = GatewayDispatchClaimCoordinator._for_test('account', 'other-generation')
    assert not scenario.source.bound_to(scenario.owner, scenario.journal, other)
    assert not scenario.owner._lifecycle_writer.matches(other._core, domain='session_source',
                                                        account='account', owner_generation='other-generation')
