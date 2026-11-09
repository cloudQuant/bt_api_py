"""Actual SDK adapter and Base core: schema failure is globally fenced."""

import time

import pytest

from bt_api_base.claim_fence import ClaimSchema
from bt_api_py.gateway_dispatch_claim import (
    DispatchClaimError,
    DispatchCommandBinding,
    GatewayDispatchClaimCoordinator,
)


def test_gateway_schema_exception_closes_raw_core_claim(monkeypatch):
    coordinator = GatewayDispatchClaimCoordinator._for_test("account", "owner")
    for domain in ("registry", "market", "parent_facts", "control", "session_source", "risk"):
        token = coordinator.begin_change(domain)
        coordinator.finish_change(token, "NOT_SELECTED" if domain == "risk" else "READY")
    values = dict(account="account", owner_generation="owner", journal_id="journal",
                  strategy="strategy", principal="principal", run_id="run",
                  request_key="first", reservation_id="reservation", payload_digest="a" * 64,
                  native_identity_digest="b" * 64, marker_ref="marker")
    before, after = coordinator.capture(), coordinator.capture()
    pending = coordinator.prepare(DispatchCommandBinding(**values), before, after,
                                  deadline_ns=time.monotonic_ns() + 1_000_000_000)
    before, after = coordinator.capture(), coordinator.capture()
    values["request_key"] = "second"
    failure = RuntimeError("synthetic schema failure")

    def broken(*args):
        raise failure

    monkeypatch.setattr(ClaimSchema, "validate", broken)
    with pytest.raises(RuntimeError) as captured:
        coordinator.prepare(DispatchCommandBinding(**values), before, after,
                            deadline_ns=time.monotonic_ns() + 1_000_000_000)
    assert captured.value is failure
    with pytest.raises(DispatchClaimError, match="COORDINATOR_FAILED"):
        coordinator._core.try_claim(pending)
