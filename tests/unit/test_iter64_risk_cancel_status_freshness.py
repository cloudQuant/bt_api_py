"""Fake Gateway responses test freshness, not source or cancellation proof."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.risk_service_component import RiskOrderService
from bt_api_py.risk_service_config import RiskPrincipal
from bt_api_py.risk_service_gateway import GatewayControlAdapter

pytest_plugins = ["tests.unit.test_iter64_gateway_risk_control_port"]


STATES = ("QUEUED", "SENT", "NOT_SENT", "UNKNOWN", "TERMINAL")
OP = "cancel-op"


def target(index, state="QUEUED", cancel=None):
    return {"reservation_id": f"res-{index:04d}", "strategy": "strategy-a",
            "request_key": f"request-{index}", "state": state, "cancel": cancel}


def observed(targets=(), **overrides):
    counts = dict.fromkeys(STATES, 0)
    for row in targets:
        counts[row["state"]] += 1
    result = {"status": "OBSERVED", "operation_id": OP, "scope_kind": "account",
              "scope_value": "", "origin": "risk:identity", "targets": list(targets),
              "counts": counts, "after": "", "limit": 256}
    result.update(overrides)
    result["remaining_at_cursor"] = sum(result["counts"].values())
    return result


class Runtime:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def status(self):
        binding = SimpleNamespace(active=True, mount="app", service_id="gateway",
                                  instance_id="gateway-a", scope_ref="scope-a",
                                  operations=frozenset({"direct.control.cancel_scope",
                                                        "direct.control.cancel_scope_status"}))
        return SimpleNamespace(state="running", healthy=True, bindings=[binding])

    def call(self, mount, operation, arguments, **kwargs):
        self.calls.append((operation, arguments))
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def adapter(replies):
    runtime = Runtime(replies)
    control = GatewayControlAdapter(runtime, mount="app", service_id="gateway",
                                    instance_id="gateway-a", scope_ref="scope-a",
                                    account_ref="account-a", timeout_seconds=5)
    return control, runtime


@pytest.mark.parametrize("bad", [RuntimeError("private error"), TimeoutError("private timeout"),
    {"status": "REQUEST_REJECTED", "code": "DENIED"}, {"status": "UNKNOWN"},
    {"status": "NOT_FOUND", "operation_id": "other"},
    observed(operation_id="other"), {"status": "OBSERVED", "operation_id": OP},
    {"status": "NOT_FOUND", "operation_id": OP, "private": "secret"}])
def test_failed_second_query_does_not_return_or_keep_old_observation(bad):
    first = observed([target(1)])
    control, runtime = adapter([first, bad])
    assert control.cancel_scope_status(OP) == first
    assert control.cancel_scope_status(OP) is None
    assert OP not in control.last_cancel_status
    assert runtime.calls == [("direct.control.cancel_scope_status", {"operation_id": OP, "after": "", "limit": 256})] * 2


def test_failed_query_does_not_fall_back_to_cancel_request_reply():
    issued = {"status": "OBSERVED", "operation_id": OP, "scope_kind": "account",
              "scope_value": "", "issued": 1,
              "counts": {**dict.fromkeys(STATES, 0), "SENT": 1}}
    control, runtime = adapter([issued, TimeoutError("timeout")])
    assert control.cancel_scope(operation_id=OP, scope_kind="account", scope_value="").outcome == "OBSERVED"
    assert control.cancel_scope_status(OP) is None
    assert OP not in control.last_cancel_status
    assert [operation for operation, _ in runtime.calls] == [
        "direct.control.cancel_scope", "direct.control.cancel_scope_status"]


def test_current_valid_status_empty_nonempty_and_not_found():
    item = target(1, "SENT", "SENT")
    replies = [observed(), observed([item]), {"status": "NOT_FOUND", "operation_id": OP}]
    control, _ = adapter(replies)
    for reply in replies:
        current = control.cancel_scope_status(OP)
        assert current == reply
        assert current is not control.last_cancel_status[OP]
    current["operation_id"] = "changed"
    assert control.last_cancel_status[OP]["operation_id"] == OP


def test_multi_page_counts_cover_all_targets_and_only_first_page_is_returned():
    first_page = [target(index) for index in range(256)]
    counts = dict.fromkeys(STATES, 0)
    counts.update(QUEUED=256, SENT=4)
    reply = observed(first_page, counts=counts)
    control, _ = adapter([reply])
    assert control.cancel_scope_status(OP) == reply


@pytest.mark.parametrize("state,cancel", [
    ("SENT", "SENT"),
    ("NOT_SENT", None), ("NOT_SENT", "REQUESTED"), ("NOT_SENT", "NOT_SENT"),
    ("UNKNOWN", "DISPATCHING"), ("UNKNOWN", "UNKNOWN"),
] + [(state, cancel) for state in ("QUEUED", "TERMINAL")
     for cancel in (None, "REQUESTED", "DISPATCHING", "SENT", "NOT_SENT", "UNKNOWN")])
def test_valid_target_state_and_cancel_combinations_follow_gateway_projection(state, cancel):
    reply = observed([target(1, state, cancel)])
    control, _ = adapter([reply])
    assert control.cancel_scope_status(OP) == reply


@pytest.mark.parametrize("state,cancel", [
    ("SENT", None), ("SENT", "REQUESTED"), ("SENT", "DISPATCHING"),
    ("SENT", "NOT_SENT"), ("SENT", "UNKNOWN"),
    ("NOT_SENT", "SENT"), ("NOT_SENT", "DISPATCHING"), ("NOT_SENT", "UNKNOWN"),
    ("UNKNOWN", None), ("UNKNOWN", "REQUESTED"), ("UNKNOWN", "NOT_SENT"),
    ("UNKNOWN", "SENT"),
])
def test_contradictory_target_state_and_cancel_are_unavailable(state, cancel):
    control, _ = adapter([observed([target(1, state, cancel)])])
    assert control.cancel_scope_status(OP) is None
    assert OP not in control.last_cancel_status


@pytest.mark.parametrize("reply", [
    observed(scope_kind="account", scope_value="wrong"),
    observed(scope_kind="strategy", scope_value="strategy-a"),
    observed(scope_kind="instrument", scope_value="invalid"),
    observed(origin=""), dict(observed(), targets=[{"private": "secret"}]),
    observed(counts={"QUEUED": 0}), observed(counts=dict.fromkeys(STATES, -1)),
    observed(counts=dict.fromkeys(STATES, True)),
    observed([target(1)], counts=dict.fromkeys(STATES, 0)),
    observed([target(2), target(1)]), observed([target(1), target(1)]),
    dict(observed([target(1)]), targets=[dict(target(1), state="BOGUS")]),
    dict(observed([target(1)]), targets=[dict(target(1), cancel="BOGUS")]),
])
def test_malformed_response_is_unavailable_without_leaking_fields(reply):
    control, runtime = adapter([reply])
    assert control.cancel_scope_status(OP) is None
    assert OP not in control.last_cancel_status
    assert len(runtime.calls) == 1


def test_unavailable_gateway_is_none_in_actual_risk_service_response():
    control, runtime = adapter([observed(), TimeoutError("secret")])
    assert control.cancel_scope_status(OP) is not None

    class Ledger:
        @contextmanager
        def transaction(self):
            yield SimpleNamespace(cancel_intent=lambda operation: {"operation_id": operation, "state": "PENDING"})

    principal = RiskPrincipal("operator", "operator", "actor", frozenset(),
                              frozenset({"risk.cancel.status"}), b"x" * 40)
    service = RiskOrderService(None, Ledger(), account_scope="account-a", route="ctp", execution_domain_ref="domain",
        principals={"operator": principal}, instrument_for=lambda *_: None, ready=lambda: True,
        cancel_status=control.cancel_scope_status)
    context = SimpleNamespace(identity=WireIdentity(WireRole.APPLICATION_CLIENT, "operator", "actor"),
                              scope=WireScope("domain", account_ref="account-a"), principal_ref="operator")
    assert service.handle("risk.cancel.status", context=context, arguments={"operation_id": OP}) == {
        "status": "OBSERVED", "operation_id": OP, "state": "PENDING", "gateway": None}
    assert len(runtime.calls) == 2


def test_actual_gateway_first_page_contract_with_temporary_owner_and_journal(port):
    # This fixture uses a temporary journal and synthetic provider callback.
    port.sent("synthetic", {"symbol": "rb2701", "exchange_id": "SHFE", "side": "buy",
                           "offset": "open", "quantity": "1"})
    port.upgrade()
    issued = port.call("risk-acct", "direct.control.cancel_scope", {"operation_id": OP, "limit": 1})
    assert issued["ok"] and issued["result"]["status"] == "OBSERVED"

    class RegistryRuntime(Runtime):
        def call(self, mount, operation, arguments, **kwargs):
            self.calls.append((operation, arguments))
            result = port.call("risk-acct", operation, arguments)
            assert result["ok"]
            return result["result"]

    runtime = RegistryRuntime([])
    control = GatewayControlAdapter(runtime, mount="app", service_id="gateway", instance_id="gateway-a",
                                    scope_ref="scope-a", account_ref="account-a", timeout_seconds=5)
    result = control.cancel_scope_status(OP)
    assert result["status"] == "OBSERVED" and result["operation_id"] == OP
    assert len(result["targets"]) == 1 and result["counts"]["SENT"] == 1
    assert [operation for operation, _ in runtime.calls] == ["direct.control.cancel_scope_status"]
