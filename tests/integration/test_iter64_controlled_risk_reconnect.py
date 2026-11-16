"""Controlled Risk client reconstruction, synthetic processes and no venue."""

from tests.integration import _iter64_recovery_support as support
from tests.integration.test_iter64_risk_crash_matrix import _Loopback
from tests.unit.test_iter64_risk_service_component import V2_POLICY_BLOCKED


# The recovery fixture bootstraps a v1 policy; the product is v2-only and v2
# activation is blocked by RULE_R04_V2_UNAVAILABLE pending the iteration-64
# R-04/R-11 decision (the host refuses with RISK_SERVICE_POLICY_INVALID).
@V2_POLICY_BLOCKED
def test_composed_risk_client_reconnects_after_ack_without_business_replay(tmp_path_factory):
    root = support.retained_loopback_root(tmp_path_factory.getbasetemp())
    print(f"Retained synthetic Risk loopback root: {root}")
    env = _Loopback(root)
    try:
        env.start_risk()
        old = env.client._runtime
        closed = env.client.close(timeout_seconds=5)
        assert closed.strict_unregister_ack is True and closed.unregister_uncertainties == 0
        assert env.client.reconnect(timeout_seconds=10) is True
        assert env.client._runtime is not old
        result = env.client.status("never-sent", timeout_seconds=10)
        assert result.outcome == "ANSWERED" and result.answer.outcome == "NOT_FOUND"
        env.controls.put("stats")
        from tests.integration._iter64_recovery_support import next_message
        assert next_message(env.messages, "stats")[1] == {}
    finally:
        env.close()
