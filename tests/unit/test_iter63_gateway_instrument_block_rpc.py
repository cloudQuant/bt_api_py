"""Operator RPCs on a contract cause (D63-59): account-wide operators see and release it."""
import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import DirectCommandJournalError

from bt_api_py.gateway_direct_service import (
    GatewayControlGrant,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from tests.test_iter63_gateway_direct_owner import Harness, create
from tests.unit.test_service_registry import _next_request, _register

OPERATIONS = frozenset({"direct.control.identity", "direct.control.apply", "direct.control.resume",
                        "direct.control.status", "direct.control.list", "direct.control.resume_status"})
WRITES = frozenset({"direct.control.apply", "direct.control.resume"})
RB = {"symbol": "rb", "exchange_id": "SHFE"}
SCOPE = "SHFE:rb"
NATIVE = {"exchange_name": "CTP", "client_order_id": "o1", "session_generation": 1, "fields_json": "{}"}


def test_account_operator_lists_and_releases_a_contract_cause_and_a_strategy_operator_cannot(tmp_path):
    h = Harness(tmp_path)
    owner = create(h)
    assert owner.start(2)
    registry = None
    try:
        control = owner.submit_service_call(lambda: h.journal.enable_controls(h.guard)).result(timeout_seconds=2)
        binding = GatewayDirectServiceBinding(
            h.journal, {"strategy-p": GatewayDirectGrant("strategy-a", frozenset({"rb"}))}, owner=owner,
            control=control, control_grants={
                "account-op": GatewayControlGrant("account-op-a", True, frozenset(), OPERATIONS),
                "strategy-op": GatewayControlGrant("strategy-op-a", False, frozenset({"strategy-a"}), OPERATIONS)})
        account_scope = WireScope("test-domain", account_ref="account")
        strategy_scope = WireScope("test-domain", account_ref="account", strategy_ref="strategy-a")
        callers = {"account-op": (WireIdentity(WireRole.APPLICATION_CLIENT, "account-op", "account-op-a"),
                                  account_scope),
                   "strategy-op": (WireIdentity(WireRole.APPLICATION_CLIENT, "strategy-op", "strategy-op-a"),
                                   strategy_scope)}
        capability = ServiceCapability("gateway-a", "service.direct_write", OPERATIONS, WRITES)
        registry = ServiceRegistry(
            ":memory:", service_id="gateway-service", capabilities=(capability,),
            grants={name: (PrincipalGrant(identity, (ResourceGrant(
                "gateway-a", "service.direct_write", "scope-a", scope, OPERATIONS),)),)
                for name, (identity, scope) in callers.items()},
            policy_version="policy", requirements_version="requirements", lease_seconds=10, clock=lambda: 100.0)
        for operation in OPERATIONS:
            registry.register_handler("gateway-a", operation,
                                      lambda ctx, args, op=operation: binding.handle(op, context=ctx, arguments=args))
        records = {}
        for name, (identity, scope) in callers.items():
            registered = _register(registry, principal=name, identity=identity, scope=scope, instance_id="gateway-a",
                                   port="service.direct_write", scope_ref="scope-a", operations=tuple(OPERATIONS))
            assert registered["ok"], registered
            records[name] = registered["result"]

        def call(name, operation, arguments):
            identity, scope = callers[name]
            return registry.handle(name, _next_request(operation, {
                "registration_id": records[name]["registration_id"],
                "service_epoch": records[name]["service_epoch"], "instance_id": "gateway-a",
                "arguments": arguments}, identity=identity, scope=scope))["result"]

        def on_owner(callback):
            return owner.submit_service_call(callback).result(timeout_seconds=2)

        # The Gateway itself blocks the contract (as the watcher of unresolved returns does); one
        # order on it is already on its way to the venue with no outcome yet.
        sent = h.journal.reserve("strategy-a", "going-out", "strategy-p", RB)
        h.journal.begin_dispatch("strategy-a", "going-out", sent.reservation_id, sent.digest, "strategy-p", 1,
                                 NATIVE, ("anchor",))
        on_owner(lambda: control.apply("unresolved-return:rb", "FREEZE", scope_kind="instrument",
                                       scope_value=SCOPE, origin="system:unresolved_return:x"))
        on_owner(lambda: control.apply("account-freeze", "FREEZE", scope_kind="account", origin="operator:x"))
        on_owner(lambda: control.resume("account-freeze", expected_version=1, operation_id="resume-account"))

        # Listing: the account operator sees its own causes and the contract causes in one page.
        listed = call("account-op", "direct.control.list", {"after": "", "limit": 10})
        assert [(row["cause_id"], row["scope_kind"]) for row in listed["causes"]] == [
            ("account-freeze", "account"), ("unresolved-return:rb", "instrument")]
        assert [row["cause_id"] for row in call("account-op", "direct.control.list",
                                                {"after": "", "limit": 1})["causes"]] == ["account-freeze"]
        assert [row["cause_id"] for row in call("account-op", "direct.control.list",
                                                {"after": "account-freeze", "limit": 10})["causes"]] == [
            "unresolved-return:rb"]
        status = call("account-op", "direct.control.status", {"cause_id": "unresolved-return:rb"})
        assert (status["status"], status["cause"]["scope_value"], status["cause"]["active"]) == ("OBSERVED", SCOPE, 1)
        # A strategy operator neither sees nor releases a cause that concerns every strategy.
        assert call("strategy-op", "direct.control.list", {"after": "", "limit": 10})["causes"] == []
        release = {"cause_id": "unresolved-return:rb", "expected_version": 1, "operation_id": "release-rb",
                   "expected_journal_id": h.journal.journal_id}
        assert call("strategy-op", "direct.control.resume", release)["status"] != "OBSERVED"
        assert call("strategy-op", "direct.control.status", {"cause_id": "unresolved-return:rb"})["status"] != "OBSERVED"
        with pytest.raises(DirectCommandJournalError, match="^CONTROL_FREEZE$"):
            h.journal.reserve("strategy-a", "new-1", "strategy-p", RB)

        # While an own order on the contract has no known outcome, the release is refused.
        assert call("account-op", "direct.control.resume", release) == {
            "status": "REQUEST_REJECTED", "code": "CONTRACT_ORDERS_UNDECIDED"}
        assert call("account-op", "direct.control.resume_status", {"operation_id": "release-rb"})["status"] == "NOT_FOUND"
        with pytest.raises(DirectCommandJournalError, match="^CONTROL_FREEZE$"):
            h.journal.reserve("strategy-a", "new-2", "strategy-p", RB)
        h.journal.finish("strategy-a", "going-out", sent.reservation_id, sent.digest, "strategy-p", 2,
                         NATIVE, ("anchor",), "SENT")

        # Now the account operator releases it; the release is recorded and idempotent.
        released = call("account-op", "direct.control.resume", release)
        assert released == {"status": "OBSERVED", "cause_id": "unresolved-return:rb", "version": 2, "active": False}
        assert call("account-op", "direct.control.resume", release) == released
        assert call("account-op", "direct.control.resume_status", {"operation_id": "release-rb"}) == {
            "status": "OBSERVED", "cause_id": "unresolved-return:rb", "operation_id": "release-rb",
            "version": 2, "active": False}
        assert call("strategy-op", "direct.control.resume_status", {"operation_id": "release-rb"})["status"] == "NOT_FOUND"
        assert h.journal.reserve("strategy-a", "new-3", "strategy-p", RB).status == "RESERVED"
    finally:
        if registry is not None:
            registry.close()
        assert owner.close(2)
