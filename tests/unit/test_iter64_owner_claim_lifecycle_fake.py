"""Owner lifecycle fencing with in-memory claims; no owner thread is started."""
import time
from types import SimpleNamespace

import pytest

from bt_api_base.claim_fence import ClaimFenceWriter, DispatchClaimError
from bt_api_py.gateway_direct_owner import (
    GatewayDirectOwner, GatewayDirectOwnerBinding, GatewayDirectOwnerError,
    GatewayDispatchCommand, GatewayOwnerServiceCallError, GatewayOwnerStartupCleanupError,
)
from bt_api_py.gateway_dispatch_claim import (
    DispatchCommandBinding,
    GATEWAY_DISPATCH_COVERAGE,
    GatewayDispatchClaimCoordinator,
    seal_gateway_dispatch_coverage,
)


def owner():
    return GatewayDirectOwner(lambda: None, queue_size=1, max_queue_wait_seconds=1,
                              busy_timeout_seconds=1)


def ready_coordinator(subject):
    coordinator = GatewayDispatchClaimCoordinator._for_test("account", "generation")
    subject._bind_dispatch_lifecycle(coordinator, account_scope="account", generation="generation")
    for domain in coordinator._domains:
        token = coordinator.begin_change(domain)
        coordinator.finish_change(token, "READY")
    return coordinator


def candidate(coordinator, request_key):
    before, after = coordinator.capture(), coordinator.capture()
    binding = DispatchCommandBinding("account", "generation", "journal", "strategy",
        "principal", "run", request_key, "reservation", "a" * 64, "b" * 64, "marker")
    return coordinator.prepare(binding, before, after, deadline_ns=time.monotonic_ns() + 1_000_000_000)


def test_close_stales_candidate_but_preserves_already_claimed_and_blocks_later_ready():
    subject = owner()
    coordinator = ready_coordinator(subject)
    claimed = coordinator.try_claim(candidate(coordinator, "claimed"))
    waiting = candidate(coordinator, "waiting")
    assert subject.close(1)
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.try_claim(waiting)
    coordinator.retire(claimed)
    token = coordinator.begin_change("session_source")
    coordinator.finish_change(token, "READY")
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()
    assert not dict((n, poisoned) for n, _, poisoned in coordinator.coverage_status()["domains"])["session_source"]
    assert subject.close(1)
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()


def test_unattached_owner_keeps_legacy_close_and_cannot_bind_after_closing():
    subject = owner()
    assert subject.close(1)
    with pytest.raises(GatewayDirectOwnerError, match="OWNER_LIFECYCLE_INVALID"):
        subject._bind_dispatch_lifecycle(GatewayDispatchClaimCoordinator._for_test("account", "generation"),
                                         account_scope="account", generation="generation")


def test_coverage_requires_actual_lifecycle_writer_label():
    coordinator, sealer = GatewayDispatchClaimCoordinator.with_coverage_sealer("account", "generation")
    labels = {label for domain in GATEWAY_DISPATCH_COVERAGE.values() for label in domain}
    labels.remove("gateway_direct_owner.lifecycle_change")
    with pytest.raises(DispatchClaimError, match="COVERAGE_INCOMPLETE"):
        seal_gateway_dispatch_coverage(coordinator, sealer, wired=frozenset(labels))
    assert not coordinator.sealed


def test_real_owner_binding_allows_trusted_coverage_to_seal():
    subject = owner()
    coordinator, sealer = GatewayDispatchClaimCoordinator.with_coverage_sealer("account", "generation")
    labels = {label for domain in GATEWAY_DISPATCH_COVERAGE.values() for label in domain}
    labels.remove("gateway_direct_owner.lifecycle_change")
    subject._bind_dispatch_lifecycle(coordinator, account_scope="account", generation="generation")
    labels.add("gateway_direct_owner.lifecycle_change")
    seal_gateway_dispatch_coverage(coordinator, sealer, wired=frozenset(labels))
    coordinator.discard_snapshot(coordinator.capture())
    subject._fence_dispatch_shutdown()
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()


def test_outer_shutdown_before_binding_prevents_later_attachment():
    subject = owner()
    subject._fence_dispatch_shutdown()
    subject._fence_dispatch_shutdown()
    assert subject._lifecycle_epoch == 1
    assert not subject._closing
    with pytest.raises(GatewayDirectOwnerError, match="OWNER_LIFECYCLE_INVALID"):
        subject._bind_dispatch_lifecycle(GatewayDispatchClaimCoordinator._for_test("account", "generation"),
                                         account_scope="account", generation="generation")


def test_outer_shutdown_refuses_new_work_and_drops_only_queued_tasks():
    subject = owner()
    coordinator = ready_coordinator(subject)
    class FakeThread:
        def is_alive(self):
            return True
    journal = object()
    subject._thread = FakeThread()
    subject._binding = GatewayDirectOwnerBinding("account", "journal", "generation")
    subject._journal = journal
    subject._dispatcher = SimpleNamespace(journal=journal, claims_available=lambda: True)
    finalizations = []
    command = GatewayDispatchCommand("strategy", "request", "principal", "reservation", "a" * 64, 1)
    assert subject.enqueue(command, request_fence=lambda: None,
                           finalize=lambda: finalizations.append("queued")) == "QUEUED"
    service = subject.submit_service_call(lambda: None)
    snapshot = subject._lifecycle_snapshot()
    subject._fence_dispatch_shutdown()
    assert finalizations == ["queued"] and not subject._pending
    assert service.done
    with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_CALL_CLOSED"):
        service.result(timeout_seconds=0)
    assert not subject._lifecycle_is_current(snapshot)
    assert not subject.status().accepting and subject.binding() is None
    assert not subject.is_bound_to(journal) and not subject.supports_dispatch_claims()
    assert subject.dispatch_refusal() == "DISPATCH_QUEUE_CLOSING"
    assert subject.enqueue(command, request_fence=lambda: None,
                           finalize=lambda: finalizations.append("refused")) == "CLOSING"
    assert finalizations == ["queued", "refused"]
    with pytest.raises(GatewayOwnerServiceCallError, match="SERVICE_CALL_NOT_READY"):
        subject.submit_service_call(lambda: None)
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()


def test_unexpected_owner_exit_fences_even_without_close(monkeypatch):
    subject = owner()
    coordinator = ready_coordinator(subject)
    def fail():
        raise RuntimeError("synthetic")
    monkeypatch.setattr(subject, "_run_owned", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        subject._run()
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()
    assert subject._lifecycle_epoch == 1
    assert dict((n, poisoned) for n, _, poisoned in coordinator.coverage_status()["domains"])["session_source"]


def test_mutation_exception_poisons_domain():
    subject = owner()
    coordinator = ready_coordinator(subject)
    with pytest.raises(RuntimeError, match="synthetic"):
        with subject._lifecycle_change():
            raise RuntimeError("synthetic")
    assert dict((n, poisoned) for n, _, poisoned in coordinator.coverage_status()["domains"])["session_source"]
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()


def test_writer_begin_is_outside_condition_and_failure_poisons(monkeypatch):
    subject = owner()
    coordinator = ready_coordinator(subject)
    def fail_begin(_writer):
        assert not subject._condition._is_owned()
        raise RuntimeError("synthetic")
    monkeypatch.setattr(ClaimFenceWriter, "begin_change", fail_begin)
    assert subject.close(1)
    assert dict((n, poisoned) for n, _, poisoned in coordinator.coverage_status()["domains"])["session_source"]
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_begin_interrupt_closes_and_propagates(monkeypatch, interruption):
    subject = owner()
    coordinator = ready_coordinator(subject)
    def interrupt(_writer):
        raise interruption()
    monkeypatch.setattr(ClaimFenceWriter, "begin_change", interrupt)
    with pytest.raises(interruption):
        subject.close(1)
    assert subject.closed and subject._closing
    assert dict((n, poisoned) for n, _, poisoned in coordinator.coverage_status()["domains"])["session_source"]


def test_factory_failure_cleans_up_and_poisons_without_thread():
    subject = owner()
    coordinator = ready_coordinator(subject)
    closed = []
    def fail_factory():
        raise GatewayOwnerStartupCleanupError(lambda: closed.append(True))
    subject._factory = fail_factory
    subject._run()
    assert closed == [True]
    assert subject.closed
    assert subject.status().failure_category == "factory_failed"
    assert dict((n, poisoned) for n, _, poisoned in coordinator.coverage_status()["domains"])["session_source"]


def test_start_timeout_fences_with_synchronous_thread_fake(monkeypatch):
    import bt_api_py.gateway_direct_owner as module
    subject = owner()
    coordinator = ready_coordinator(subject)
    class FakeThread:
        def __init__(self, **_kwargs):
            pass
        def start(self):
            pass
    monkeypatch.setattr(module.threading, "Thread", FakeThread)
    monkeypatch.setattr(subject._startup, "wait", lambda _timeout: False)
    assert not subject.start(1)
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.capture()
