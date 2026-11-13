"""Execution-only delegate fences, using a synthetic atomic host and runtime."""
from dataclasses import replace
import threading

import pytest

from bt_api_py.gateway_delegate_service_client import GatewayDelegateServiceClient
from bt_api_py.service_runtime_thread import ThreadedRuntimeError
from tests.unit.test_iter63_gateway_delegate_service_client import Host
from tests.unit.test_iter64_gateway_delegate_client_outcomes import ROUTE, _Site


class AtomicHost(Host):
    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.retry_allowed = True
        self.retry_checks = []

    def claim_first_delegate_reserve(self, intent):
        with self.lock:
            key = intent.command.request_key
            if key in self.intent:
                return False
            self.intent[key] = intent
            return True

    def check_delegate_dispatch_retry(self, key, marker):
        self.retry_checks.append((key, marker))
        return self.retry_allowed


def execution_site(monkeypatch, host=None, *, synthetic_unbound=False):
    site = _Site(monkeypatch, host=host or AtomicHost())
    site.cmd = replace(site.cmd, actor_kind='execution', parent_ref='parent', child_id='child')
    site.client = GatewayDelegateServiceClient(site.runtime, route=ROUTE, host=site.host,
                                                execution_one_shot=True)
    if synthetic_unbound:
        # Exercise host fences in isolation, with a fake runtime only. This is
        # not evidence that the real runtime can pin its transport binding.
        monkeypatch.setattr(GatewayDelegateServiceClient, 'execution_write_available', property(lambda _self: True))
    return site


def test_execution_binding_requires_explicit_complete_capabilities(monkeypatch):
    site = _Site(monkeypatch)
    with pytest.raises(ValueError, match='INVALID_DELEGATE_CLIENT'):
        GatewayDelegateServiceClient(site.runtime, route=ROUTE, host=site.host, execution_one_shot=True)
    with pytest.raises(ValueError, match='INVALID_DELEGATE_CLIENT'):
        GatewayDelegateServiceClient(site.runtime, route=ROUTE, host=AtomicHost(), execution_one_shot=1)
    client = GatewayDelegateServiceClient(site.runtime, route=ROUTE, host=AtomicHost(), execution_one_shot=True)
    with pytest.raises(ValueError, match='INVALID_INTENT'):
        client.reserve(site.cmd, site.request)


def test_execution_write_closes_before_first_claim_without_bound_runtime(monkeypatch):
    site = execution_site(monkeypatch)
    result = site.client.reserve(site.cmd, site.request)
    assert (result.outcome, result.code) == ('UNKNOWN', 'EXECUTION_BOUND_ROUTE_UNAVAILABLE')
    assert site.host.intent == {} and site.calls == []


def test_legacy_execution_never_writes_and_can_query_existing_intent(monkeypatch):
    site = execution_site(monkeypatch)
    legacy = GatewayDelegateServiceClient(site.runtime, route=ROUTE, host=site.host)
    assert legacy.reserve(site.cmd, site.request).code == 'EXECUTION_ONE_SHOT_REQUIRED'
    assert site.host.intent == {} and site.calls == []
    from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
    from bt_api_py.gateway_delegate_service_client import GatewayDelegateIntent
    intent = GatewayDelegateIntent(ROUTE, site.cmd, canonical_gateway_payload(site.request.to_dict()), 10.0)
    site.host.save_delegate_intent(intent)
    assert legacy.reserve(site.cmd, site.request).code == 'EXECUTION_ONE_SHOT_REQUIRED'
    assert legacy.dispatch(site.cmd.request_key).code == 'EXECUTION_ONE_SHOT_REQUIRED'
    site.answers[:] = [{'status': 'UNKNOWN'}]
    assert legacy.actor_status(site.cmd.request_key).outcome == 'UNKNOWN'
    assert site.calls == ['direct.delegate_actor_status']


def test_durable_route_and_runtime_are_used_if_client_changes_during_claim(monkeypatch):
    site = execution_site(monkeypatch, synthetic_unbound=True)
    other = _Site(monkeypatch)
    original_claim = site.host.claim_first_delegate_reserve
    def claim_and_replace(intent):
        result = original_claim(intent)
        site.client._route = replace(ROUTE, service_id='other-gateway', scope_ref='other-scope')
        site.client._runtime = other.runtime
        return result
    site.host.claim_first_delegate_reserve = claim_and_replace
    site.answers[:] = [TimeoutError('synthetic ambiguous')]
    assert site.client.reserve(site.cmd, site.request).outcome == 'UNKNOWN'
    assert site.host.intent[site.cmd.request_key].route == ROUTE
    assert site.calls == ['direct.delegated_reserve'] and other.calls == []


def test_real_sqlite_host_execution_writes_stay_closed_without_bound_runtime(tmp_path, monkeypatch):
    pytest.importorskip('bt_api_execution')
    from test_parent_child_identity_iter65 import journal
    from test_iter65_child_dispatch_preflight import prepare
    from test_iter65_delegate_host_final_fence import delegate_row, reserve_locally
    resources = journal.__wrapped__(tmp_path)
    store, authority = next(resources)
    try:
        command, request, client, calls = prepare(store, authority, monkeypatch, bound_transport=False)
        assert client.execution_write_available is False
        authority.claim_child('p', 'c')
        assert client.reserve(command, request).code == 'EXECUTION_BOUND_ROUTE_UNAVAILABLE'
        assert delegate_row(store, authority) is None and calls == []
        legacy = GatewayDelegateServiceClient(client._runtime, route=client._route, host=client._host)
        assert legacy.reserve(command, request).code == 'EXECUTION_ONE_SHOT_REQUIRED'
        reserve_locally(authority, command, request, client)
        before = delegate_row(store, authority)
        assert client.reserve(command, request).code == 'EXECUTION_BOUND_ROUTE_UNAVAILABLE'
        assert client.dispatch('c').code == 'EXECUTION_BOUND_ROUTE_UNAVAILABLE'
        assert legacy.reserve(command, request).code == 'EXECUTION_ONE_SHOT_REQUIRED'
        assert legacy.dispatch('c').code == 'EXECUTION_ONE_SHOT_REQUIRED'
        assert delegate_row(store, authority) == before and calls == []
    finally:
        resources.close()


def test_existing_intent_after_unknown_never_reserves_again_and_can_query(monkeypatch):
    site = execution_site(monkeypatch, synthetic_unbound=True)
    site.answers[:] = [TimeoutError('ambiguous'), {'status': 'UNKNOWN'}]
    assert site.client.reserve(site.cmd, site.request).outcome == 'UNKNOWN'
    assert site.client.reserve(site.cmd, site.request).outcome == 'UNKNOWN'
    assert site.client.actor_status(site.cmd.request_key).outcome == 'UNKNOWN'
    assert site.calls == ['direct.delegated_reserve', 'direct.delegate_actor_status']


def test_even_proven_unsent_reserve_does_not_reuse_the_claim(monkeypatch):
    site = execution_site(monkeypatch, synthetic_unbound=True)
    site.answers[:] = [ThreadedRuntimeError('NOT_READY')]
    assert site.client.reserve(site.cmd, site.request).outcome == 'TRANSPORT_NOT_STARTED'
    assert site.client.reserve(site.cmd, site.request).outcome == 'UNKNOWN'
    assert site.calls == ['direct.delegated_reserve']


def test_claim_committed_before_crash_never_sends_on_restart(monkeypatch):
    host = AtomicHost()
    site = execution_site(monkeypatch, host, synthetic_unbound=True)
    claim = host.claim_first_delegate_reserve

    def committed_then_lost(intent):
        claim(intent)
        raise RuntimeError('lost after commit')

    host.claim_first_delegate_reserve = committed_then_lost
    assert site.client.reserve(site.cmd, site.request).outcome == 'UNKNOWN'
    host.claim_first_delegate_reserve = claim
    restarted = execution_site(monkeypatch, host)
    assert restarted.client.reserve(restarted.cmd, restarted.request).outcome == 'UNKNOWN'
    assert site.calls == restarted.calls == []


def test_claim_without_readable_matching_intent_never_sends(monkeypatch):
    site = execution_site(monkeypatch, synthetic_unbound=True)
    site.host.claim_first_delegate_reserve = lambda _intent: True
    result = site.client.reserve(site.cmd, site.request)
    assert (result.outcome, result.code) == ('UNKNOWN', 'RESERVE_CLAIM_UNKNOWN')
    assert site.calls == []


@pytest.mark.parametrize('claim_result', [False, None, 1])
def test_claim_requires_exact_true(monkeypatch, claim_result):
    site = execution_site(monkeypatch, synthetic_unbound=True)
    site.host.claim_first_delegate_reserve = lambda _intent: claim_result
    assert site.client.reserve(site.cmd, site.request).outcome == 'UNKNOWN'
    assert site.calls == []


def test_concurrent_clients_share_atomic_first_claim(monkeypatch):
    host = AtomicHost()
    sites = [execution_site(monkeypatch, host, synthetic_unbound=True), execution_site(monkeypatch, host)]
    barrier = threading.Barrier(2)
    outcomes = []
    def worker(site):
        client = GatewayDelegateServiceClient(site.runtime, route=ROUTE, host=host, execution_one_shot=True)
        barrier.wait()
        outcomes.append(client.reserve(site.cmd, site.request).outcome)
    for site in sites:
        site.answers[:] = [TimeoutError('ambiguous')]
    threads = [threading.Thread(target=worker, args=(site,)) for site in sites]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert outcomes == ['UNKNOWN', 'UNKNOWN']
    assert sum(site.calls.count('direct.delegated_reserve') for site in sites) == 1


@pytest.mark.parametrize('retry_allowed', [False, None, 1, RuntimeError('fence unavailable')])
def test_unsent_retry_requires_successful_fresh_host_fence(monkeypatch, retry_allowed):
    site = execution_site(monkeypatch, synthetic_unbound=True)
    site.answers[:] = [site.reserved(), ThreadedRuntimeError('NOT_READY'), site.queued()]
    assert site.client.reserve(site.cmd, site.request).outcome == 'OBSERVED'
    assert site.client.dispatch(site.cmd.request_key).outcome == 'TRANSPORT_NOT_STARTED'
    def check(key, marker):
        site.host.retry_checks.append((key, marker))
        if isinstance(retry_allowed, Exception):
            raise retry_allowed
        return retry_allowed
    site.host.check_delegate_dispatch_retry = check
    assert site.client.dispatch(site.cmd.request_key).outcome == 'UNKNOWN'
    assert site.client.dispatch(site.cmd.request_key).outcome == 'UNKNOWN'
    assert len(site.host.retry_checks) == 1
    assert site.calls.count('direct.delegated_dispatch') == 1


def test_unsent_retry_after_current_fence_succeeds(monkeypatch):
    site = execution_site(monkeypatch, synthetic_unbound=True)
    site.answers[:] = [site.reserved(), ThreadedRuntimeError('NOT_READY'), site.queued()]
    assert site.client.reserve(site.cmd, site.request).outcome == 'OBSERVED'
    assert site.client.dispatch(site.cmd.request_key).outcome == 'TRANSPORT_NOT_STARTED'
    assert site.client.dispatch(site.cmd.request_key).outcome == 'QUEUED'
    assert site.host.retry_checks == [(site.cmd.request_key, site.host.marker[site.cmd.request_key])]
    assert site.calls.count('direct.delegated_dispatch') == 2


def test_restarted_client_never_inherits_unsent_proof(monkeypatch):
    host = AtomicHost()
    site = execution_site(monkeypatch, host, synthetic_unbound=True)
    site.answers[:] = [site.reserved(), ThreadedRuntimeError('NOT_READY')]
    assert site.client.reserve(site.cmd, site.request).outcome == 'OBSERVED'
    assert site.client.dispatch(site.cmd.request_key).outcome == 'TRANSPORT_NOT_STARTED'
    restarted = execution_site(monkeypatch, host)
    assert restarted.client.dispatch(site.cmd.request_key).outcome == 'UNKNOWN'
    assert restarted.calls == []
    assert host.retry_checks == []
