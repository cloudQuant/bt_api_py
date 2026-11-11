"""Who released a control cause, stored in the journal (I63-E144); synthetic owners and journals only."""
import hashlib
import json
import logging
import sqlite3

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
from tests.unit.test_iter63_gateway_lease_recovery_proof import scenario  # noqa: F401
from tests.unit.test_service_registry import _next_request, _register

OPERATIONS = frozenset({"direct.control.apply", "direct.control.resume", "direct.control.status",
                        "direct.control.list", "direct.control.resume_status"})
WRITES = frozenset({"direct.control.apply", "direct.control.resume"})


def operator_ref(principal, actor):
    return "operator:" + hashlib.sha256(json.dumps([principal, actor], separators=(",", ":")).encode()).hexdigest()


def releases(path):
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT cause_id,resume_id,releaser FROM direct_control_releases ORDER BY cause_id").fetchall()
    finally:
        db.close()


class Operators:
    """Two account-wide operators and one strategy operator on one audited (or plain) journal."""

    def __init__(self, tmp_path, *, audited):
        self.h = Harness(tmp_path)
        self.owner = create(self.h)
        assert self.owner.start(2)
        self.registry = None
        self.control = self.on_owner(lambda: self.h.journal.enable_controls(self.h.guard))
        if audited:
            self.on_owner(lambda: self.h.journal.enable_control_release_audit(self.h.guard))
        self.binding = GatewayDirectServiceBinding(
            self.h.journal, {"strategy-p": GatewayDirectGrant("strategy-a", frozenset({"rb"}))}, owner=self.owner,
            control=self.control, control_grants={
                "alice": GatewayControlGrant("alice-a", True, frozenset(), OPERATIONS),
                "bob": GatewayControlGrant("bob-a", True, frozenset(), OPERATIONS),
                "carol": GatewayControlGrant("carol-a", False, frozenset({"strategy-a"}), OPERATIONS)})
        account = WireScope("test-domain", account_ref="account")
        strategy = WireScope("test-domain", account_ref="account", strategy_ref="strategy-a")
        self.callers = {name: (WireIdentity(WireRole.APPLICATION_CLIENT, name, name + "-a"), scope)
                        for name, scope in (("alice", account), ("bob", account), ("carol", strategy))}
        capability = ServiceCapability("gateway-a", "service.direct_write", OPERATIONS, WRITES)
        self.registry = ServiceRegistry(
            ":memory:", service_id="gateway-service", capabilities=(capability,),
            grants={name: (PrincipalGrant(identity, (ResourceGrant(
                "gateway-a", "service.direct_write", "scope-a", scope, OPERATIONS),)),)
                for name, (identity, scope) in self.callers.items()},
            policy_version="policy", requirements_version="requirements", lease_seconds=10, clock=lambda: 100.0)
        for operation in OPERATIONS:
            self.registry.register_handler("gateway-a", operation, lambda ctx, args, op=operation: self.binding.handle(
                op, context=ctx, arguments=args))
        self.records = {}
        for name, (identity, scope) in self.callers.items():
            registered = _register(self.registry, principal=name, identity=identity, scope=scope,
                                   instance_id="gateway-a", port="service.direct_write", scope_ref="scope-a",
                                   operations=tuple(OPERATIONS))
            assert registered["ok"], registered
            self.records[name] = registered["result"]

    def on_owner(self, callback):
        return self.owner.submit_service_call(callback).result(timeout_seconds=2)

    def call(self, name, operation, arguments):
        identity, scope = self.callers[name]
        return self.registry.handle(name, _next_request(operation, {
            "registration_id": self.records[name]["registration_id"],
            "service_epoch": self.records[name]["service_epoch"], "instance_id": "gateway-a",
            "arguments": arguments}, identity=identity, scope=scope))["result"]

    def close(self):
        if self.registry is not None:
            self.registry.close()
        assert self.owner.close(2)


@pytest.fixture
def audited(tmp_path):
    value = Operators(tmp_path, audited=True)
    try:
        yield value
    finally:
        value.close()


def test_an_operator_release_stores_the_operator_and_every_reply_names_it(audited, caplog):
    caplog.set_level(logging.WARNING, logger="bt_api_py.gateway_direct_service")
    o = audited
    alice, bob, carol = (operator_ref(name, name + "-a") for name in ("alice", "bob", "carol"))
    assert o.call("alice", "direct.control.apply", {"cause_id": "stop", "mode": "STOP_NEW"})["active"] is True
    assert o.call("alice", "direct.control.apply", {"cause_id": "freeze", "mode": "FREEZE"})["active"] is True
    # The releaser is derived exactly as the origin of the operator's own cause is.
    status = o.call("alice", "direct.control.status", {"cause_id": "stop"})
    assert status["cause"]["origin"] == alice and "releaser" not in status["cause"]

    release = {"cause_id": "stop", "expected_version": 1, "operation_id": "release-stop"}
    released = o.call("bob", "direct.control.resume", release)
    assert released == {"status": "OBSERVED", "cause_id": "stop", "version": 2, "active": False, "releaser": bob}
    assert releases(o.h.root / "journal") == [("stop", "release-stop", bob)]
    assert o.call("alice", "direct.control.status", {"cause_id": "stop"})["cause"]["releaser"] == bob
    assert o.call("alice", "direct.control.resume_status", {"operation_id": "release-stop"}) == {
        "status": "OBSERVED", "cause_id": "stop", "operation_id": "release-stop", "version": 2, "active": False,
        "releaser": bob}
    listed = {row["cause_id"]: row for row in o.call("alice", "direct.control.list", {"after": "", "limit": 10})["causes"]}
    assert listed["stop"]["releaser"] == bob and "releaser" not in listed["freeze"]   # active: nothing

    # Alice, who may release it as well, replays Bob's release: the stored record comes back unchanged.
    assert o.call("alice", "direct.control.resume", release) == released
    assert releases(o.h.root / "journal") == [("stop", "release-stop", bob)]

    # A strategy-scoped operator's release names that operator.
    assert o.call("carol", "direct.control.apply", {"cause_id": "own", "mode": "FREEZE"})["active"] is True
    mine = o.call("carol", "direct.control.resume", {"cause_id": "own", "expected_version": 1, "operation_id": "own-1"})
    assert mine["releaser"] == carol
    # The Gateway log still names who released what, as before.
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Operator control release")]
    assert len(lines) == 3 and "by principal bob actor bob-a" in lines[0] and "by principal alice actor alice-a" in lines[1]


def test_a_journal_without_the_audit_replies_as_before(tmp_path):
    o = Operators(tmp_path, audited=False)
    try:
        o.call("alice", "direct.control.apply", {"cause_id": "stop", "mode": "STOP_NEW"})
        release = {"cause_id": "stop", "expected_version": 1, "operation_id": "release-stop"}
        assert o.call("bob", "direct.control.resume", release) == {
            "status": "OBSERVED", "cause_id": "stop", "version": 2, "active": False}
        assert "releaser" not in o.call("alice", "direct.control.status", {"cause_id": "stop"})["cause"]
        assert o.call("alice", "direct.control.resume_status", {"operation_id": "release-stop"}) == {
            "status": "OBSERVED", "cause_id": "stop", "operation_id": "release-stop", "version": 2, "active": False}
        assert all("releaser" not in row for row in
                   o.call("alice", "direct.control.list", {"after": "", "limit": 10})["causes"])
    finally:
        o.close()


def test_the_contract_watcher_names_itself_when_it_lifts_a_block(tmp_path):
    from bt_api_base.direct_write_protocol import DirectExecutionEventKind, DirectOrderStatus
    from test_iter63_direct_execution_journal import fact

    from bt_api_py.gateway_instrument_block import AUTOMATIC_RELEASER
    from tests.unit.test_iter63_gateway_instrument_block import World

    world = World(tmp_path)
    try:
        world.journal.enable_control_release_audit(world.guard)
        row = world.reserve("s1", "rb2601")
        native = {"exchange_name": "CTP", "client_order_id": "o1", "session_generation": 1, "fields_json": "{}"}
        world.journal.begin_dispatch("s1", "key-1", row.reservation_id, row.digest, "principal", 1, native, ("anchor",))
        world.journal.finish("s1", "key-1", row.reservation_id, row.digest, "principal", 2, native, ("anchor",), "SENT")
        world.ledger.ingest(fact(keys=("anchor", "venue-o1"), binding=("anchor",), venue="venue-o1", ref="trade-1",
                                 trade_key=("SHFE", "t1")))
        world.step()
        cause, = world.causes(active=True)
        world.ledger.ingest(fact(kind=DirectExecutionEventKind.ORDER, keys=("anchor", "venue-o1"), binding=("anchor",),
                                 venue="venue-o1", ref="order-1", status=DirectOrderStatus.ACCEPTED))
        world.ledger.reconcile_pending()
        world.step()
        released, = world.causes(active=False)
        assert (released["cause_id"], released["releaser"]) == (cause["cause_id"], AUTOMATIC_RELEASER)
        assert AUTOMATIC_RELEASER == "system:instrument-block"
        status = world.control.resume_status("resolved:" + cause["cause_id"], scope_kind="instrument")
        assert status["releaser"] == AUTOMATIC_RELEASER
    finally:
        world.guard.release()


def test_the_required_monitor_names_itself_when_a_heartbeat_releases_its_cause(tmp_path, monkeypatch):
    import bt_api_py.gateway_required_monitor as module
    from tests.unit.test_iter63_gateway_required_monitor import setup_monitor

    monkeypatch.setattr(module.time, "monotonic_ns", lambda: 1_000)
    monitor, journal, control, guard = setup_monitor(tmp_path)
    try:
        journal.enable_control_release_audit(guard)
        cause, = control.active_system_causes(monitor.origin)
        proof = (monitor.generation, "reg", "epoch", "nonce")
        monitor.gate.acknowledge(proof, 2_000)
        monitor._restore(proof)
        assert monitor.gate() is True
        assert control.get(cause["cause_id"])["releaser"] == module.MONITOR_RECOVERY_RELEASER == "system:required-monitor"
    finally:
        monitor.gate.close()
        guard.release()


def test_the_lease_recovery_names_itself_and_an_operator_by_the_operator(scenario):  # noqa: F811
    from bt_api_py.gateway_lease_recovery import LEASE_RECOVERY_RELEASER

    h, owner, _, control, _, recovery, event, _ = scenario
    owner.submit_service_call(lambda: h.journal.enable_control_release_audit(h.guard)).result(timeout_seconds=2)
    assert recovery.recover_auto(event["event_id"]) == "RECOVERED"
    lease = owner.submit_service_call(lambda: control.get(event["event_id"])).result(timeout_seconds=2)
    assert lease["releaser"] == LEASE_RECOVERY_RELEASER == "system:lease-drain"


def test_an_operator_release_of_a_lease_cause_stores_the_operator_and_a_replay_keeps_it(scenario):  # noqa: F811
    h, owner, registry, control, _, recovery, event, _ = scenario
    owner.submit_service_call(lambda: h.journal.enable_control_release_audit(h.guard)).result(timeout_seconds=2)
    ops = frozenset({"direct.control.resume"})
    binding = GatewayDirectServiceBinding(
        h.journal, {"principal": GatewayDirectGrant("strategy", frozenset({"X"}))}, owner=owner, control=control,
        control_grants={"operator": GatewayControlGrant("operator-actor", False, frozenset({"strategy"}), ops)})
    binding._lease_recovery = recovery
    first = operator_ref("operator", "operator-actor")
    calls = []

    def handler(ctx, args):
        if not calls:
            calls.append(1)
            return binding.handle("direct.control.resume", context=ctx, arguments=args)
        # A replay of the same release, made in another operator's name: the stored record wins.
        try:
            return owner.submit_service_call(lambda: recovery.recover_manual_owned(
                args["cause_id"], args["expected_version"], args["operation_id"], admission=ctx.dispatch_admission,
                context=ctx, arguments=args, releaser=operator_ref("other", "other-a"))).result(timeout_seconds=2)
        finally:
            ctx.dispatch_admission.close()

    registry.register_handler("gateway", "direct.control.resume", handler)
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, "operator", "operator-actor")
    scope = WireScope("domain", account_ref="account", strategy_ref="strategy")
    registration = _register(registry, principal="operator", identity=identity, scope=scope, key="operator",
                             instance_id="gateway", port="service.direct_write", scope_ref="scope",
                             operations=("direct.control.resume",))["result"]
    args = {"cause_id": event["event_id"], "expected_version": 1, "operation_id": "manual"}

    def resume():
        return registry.handle("operator", _next_request("direct.control.resume", {
            "registration_id": registration["registration_id"], "service_epoch": registration["service_epoch"],
            "instance_id": "gateway", "arguments": args}, identity=identity, scope=scope))["result"]

    assert resume() == {"status": "OBSERVED", "cause_id": event["event_id"], "version": 2, "active": False,
                        "releaser": first}
    replay = resume()
    assert (replay["active"], replay["version"], replay["releaser"]) == (0, 2, first)
    assert releases(h.root / "journal") == [(event["event_id"], "manual", first)]


def test_a_caller_that_names_no_releaser_cannot_release_on_an_audited_journal(scenario):  # noqa: F811
    h, owner, _, control, _, _, _, _ = scenario
    owner.submit_service_call(lambda: h.journal.enable_control_release_audit(h.guard)).result(timeout_seconds=2)

    def unnamed():
        with pytest.raises(DirectCommandJournalError, match="^CONTROL_RELEASER_REQUIRED$"):
            control.resume("other", expected_version=1, operation_id="x")
        return control.get("other")

    assert owner.submit_service_call(unnamed).result(timeout_seconds=2)["active"] == 1
