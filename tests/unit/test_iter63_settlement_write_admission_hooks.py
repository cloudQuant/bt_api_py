"""Local settlement admission fence tests; no account or native I/O."""

import threading
import time

import pytest

from bt_api_base.direct_write_protocol import WriteAdmissionDenied, WriteOperation
from bt_api_py.gateway_dispatch_claim import DispatchClaimError
from bt_api_py.gateway_settlement_claim import (
    GatewaySettlementClaimCoordinator,
    SettlementClaimBinding,
)
from bt_api_py.write_admission import WriteAdmission

ROUTE = "CTP___FUTURE"
DOMAINS = ("write_admission", "sdk_source", "provider_session", "owner",
           "observation", "journal_marker")


def ready(coordinator):
    for domain in DOMAINS:
        token = coordinator.begin_change(domain)
        coordinator.finish_change(token, "READY")


def setup_bound():
    coordinator = GatewaySettlementClaimCoordinator._for_test("account", "owner")
    admission = WriteAdmission({ROUTE: True})
    admission._bind_settlement_claim_coordinator(coordinator)
    ready(coordinator)
    return admission, coordinator


def issue(admission):
    return admission.issue(ROUTE, WriteOperation.PLACE, 1)


def candidate(coordinator):
    before = coordinator.capture()
    after = coordinator.capture()
    binding = SettlementClaimBinding(ROUTE, "account", "20261109", "obs", 1,
                                     "owner", "journal", "marker")
    return coordinator.prepare(binding, before, after,
                               deadline_ns=time.monotonic_ns() + 1_000_000_000)


def consume(admission, grant):
    admission.consume(grant, exchange_name=ROUTE, operation=WriteOperation.PLACE,
                      session_generation=1)


@pytest.mark.parametrize("operation", ("issue", "consume", "revoke", "revoke_grant"))
def test_grant_mutation_invalidates_prepared_candidate(operation):
    admission, coordinator = setup_bound()
    grant = issue(admission)
    ready(coordinator)
    prepared = candidate(coordinator)
    if operation == "issue":
        issue(admission)
    elif operation == "consume":
        consume(admission, grant)
    elif operation == "revoke":
        admission.revoke(ROUTE)
    else:
        admission.revoke_grant(grant)
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.try_claim(prepared)
    ready(coordinator)
    with pytest.raises(DispatchClaimError, match="CANDIDATE_STALE"):
        coordinator.try_claim(prepared)


def test_revoke_only_drops_existing_grants_not_route_enablement():
    admission, _ = setup_bound()
    original = issue(admission)
    admission.revoke(ROUTE)
    with pytest.raises(WriteAdmissionDenied, match="write_grant_invalid_or_consumed"):
        consume(admission, original)
    assert admission.is_enabled(ROUTE)
    consume(admission, issue(admission))


def test_claim_then_revoke_does_not_wait_for_inflight_work():
    admission, coordinator = setup_bound()
    grant = issue(admission)
    ready(coordinator)
    prepared = candidate(coordinator)
    claimed, release = threading.Event(), threading.Event()
    results = []

    def native():
        results.append(coordinator.try_claim(prepared))
        claimed.set()
        assert release.wait(2)

    thread = threading.Thread(target=native)
    thread.start()
    try:
        assert claimed.wait(1)
        admission.revoke_grant(grant)
        assert thread.is_alive() and len(results) == 1
        with pytest.raises(WriteAdmissionDenied, match="write_grant_invalid_or_consumed"):
            consume(admission, grant)
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()


def test_revoke_invalidates_before_mutating_grants(monkeypatch):
    admission, coordinator = setup_bound()
    grant = issue(admission)
    ready(coordinator)
    prepared = candidate(coordinator)
    invalidated, resume = threading.Event(), threading.Event()
    original = GatewaySettlementClaimCoordinator.begin_change

    def pause(self, domain):
        token = original(self, domain)
        if self is coordinator and domain == "write_admission":
            invalidated.set()
            assert resume.wait(2)
        return token

    monkeypatch.setattr(GatewaySettlementClaimCoordinator, "begin_change", pause)
    thread = threading.Thread(target=admission.revoke, args=(ROUTE,))
    thread.start()
    try:
        assert invalidated.wait(1)
        with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
            coordinator.try_claim(prepared)
    finally:
        resume.set()
        thread.join(2)
    assert not thread.is_alive()
    with pytest.raises(WriteAdmissionDenied, match="write_grant_invalid_or_consumed"):
        consume(admission, grant)


def test_publication_failure_poison_and_revoke_still_removes_grant(monkeypatch):
    admission, coordinator = setup_bound()
    grant = issue(admission)
    ready(coordinator)
    prepared = candidate(coordinator)
    original = GatewaySettlementClaimCoordinator.finish_change

    def broken(self, token, status):
        if self is coordinator:
            raise RuntimeError("publication failed")
        return original(self, token, status)

    monkeypatch.setattr(GatewaySettlementClaimCoordinator, "finish_change", broken)
    with pytest.raises(RuntimeError, match="publication failed"):
        admission.revoke(ROUTE)
    with pytest.raises(DispatchClaimError, match="DOMAIN_UNAVAILABLE"):
        coordinator.try_claim(prepared)
    # Inspecting under the admission lock confirms revoke still cleared it;
    # the poisoned coordinator intentionally refuses subsequent operations.
    with admission._lock:
        assert grant.grant_id not in admission._outstanding


def test_bind_is_instance_scoped_and_plain_direct_keeps_one_shot_behavior():
    admission, coordinator = setup_bound()
    with pytest.raises(RuntimeError):
        admission._bind_settlement_claim_coordinator(coordinator)
    direct = WriteAdmission({ROUTE: True})
    grant = issue(direct)
    consume(direct, grant)
    with pytest.raises(WriteAdmissionDenied, match="write_grant_invalid_or_consumed"):
        consume(direct, grant)
    assert coordinator.capture()
    with pytest.raises(RuntimeError, match="cannot be bound"):
        direct._bind_settlement_claim_coordinator(
            GatewaySettlementClaimCoordinator._for_test("account", "other-owner")
        )


def test_bind_after_issued_then_revoked_grant_is_rejected():
    admission = WriteAdmission({ROUTE: True})
    grant = issue(admission)
    admission.revoke_grant(grant)
    coordinator = GatewaySettlementClaimCoordinator._for_test("account", "owner")
    with pytest.raises(RuntimeError, match="cannot be bound"):
        admission._bind_settlement_claim_coordinator(coordinator)


def test_denied_issue_does_not_prevent_first_binding():
    admission = WriteAdmission()
    with pytest.raises(WriteAdmissionDenied, match="write_admission_disabled"):
        issue(admission)
    coordinator = GatewaySettlementClaimCoordinator._for_test("account", "owner")
    admission._bind_settlement_claim_coordinator(coordinator)
    ready(coordinator)
    assert coordinator.capture()
