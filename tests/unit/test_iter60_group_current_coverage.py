"""Current group authorization checks using local, synthetic resources only.

These checks exercise the current SDK coordinator contract. They do not
reconstruct the retired tests or establish provider/account provenance.
"""

import time
from dataclasses import replace

import pytest

from tests.unit import test_service_group_policy_admission_gate as fixtures


@pytest.mark.parametrize("denial", ["portfolio", "group"])
def test_policy_denial_has_no_binding_or_rpc_across_reopen(tmp_path, denial):
    """Reopening both local stores cannot turn a denied intent into a write."""
    now_ns = [time.time_ns()]
    policy_db = tmp_path / "policy.sqlite"
    admitted = ("another-portfolio",) if denial == "portfolio" else (fixtures._PORTFOLIO,)
    denied_groups = (fixtures._GROUP_ID,) if denial == "group" else ()
    expected = "GROUP_POLICY_SCOPE_DENIED" if denial == "portfolio" else "GROUP_POLICY_DENIED"

    for _ in range(2):
        policy_store, gate = fixtures._pinned_gate(
            policy_db, admitted=admitted, denied=denied_groups
        )
        runtime = fixtures._NoRpcRuntime()
        service, groups, bindings, registration, metadata = fixtures._registered_gated_service(
            tmp_path / "coordinator", gate, now_ns=now_ns, runtime=runtime
        )
        try:
            result = fixtures._submit(service, registration, metadata, now_ns)
            assert result == {"ok": False, "code": expected, "result": {}}
            assert bindings.list_all() == ()
            assert groups.get(fixtures._PORTFOLIO, fixtures._GROUP_ID) is None
            assert runtime.calls == []
        finally:
            service.close()
            groups.close()
            bindings.close()
            policy_store.close()


def test_changed_registration_scope_cannot_enter_gate_or_write(tmp_path):
    """A context cannot borrow another registration's group authority."""
    now_ns = [time.time_ns()]
    gate = fixtures._RecordingGate()
    runtime = fixtures._NoRpcRuntime()
    service, groups, bindings, registration, metadata = fixtures._registered_gated_service(
        tmp_path, gate, now_ns=now_ns, runtime=runtime
    )
    try:
        original = registration.context
        changed_scope = replace(original.scope, strategy_ref="another-strategy")
        forged_context = replace(original, scope=changed_scope)
        result = service.handle(
            "groups.submit",
            context=forged_context,
            arguments=fixtures._arguments(now_ns[0], metadata),
        )
        assert result == {"ok": False, "code": "SERVICE_AUTHORITY_INVALID", "result": {}}
        assert gate.calls == []
        assert bindings.list_all() == ()
        assert groups.get(fixtures._PORTFOLIO, fixtures._GROUP_ID) is None
        assert runtime.calls == []
    finally:
        service.close()
        groups.close()
        bindings.close()


def test_revoked_registration_does_not_reach_policy_or_create_state(tmp_path):
    """A stale caller loses write access as soon as its registration is revoked."""
    now_ns = [time.time_ns()]
    gate = fixtures._RecordingGate()
    runtime = fixtures._NoRpcRuntime()
    service, groups, bindings, registration, metadata = fixtures._registered_gated_service(
        tmp_path, gate, now_ns=now_ns, runtime=runtime
    )
    try:
        service.maintain_registrations(())
        result = fixtures._submit(service, registration, metadata, now_ns)
        assert result == {"ok": False, "code": "REGISTRATION_NOT_ACTIVE", "result": {}}
        assert gate.calls == []
        assert bindings.list_all() == ()
        assert groups.get(fixtures._PORTFOLIO, fixtures._GROUP_ID) is None
        assert runtime.calls == []
    finally:
        service.close()
        groups.close()
        bindings.close()
