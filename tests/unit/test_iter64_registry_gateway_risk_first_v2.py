"""Registry-local synthetic admission; no Gateway handler or venue is installed."""

import copy
import threading
from dataclasses import replace

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_delegation import GatewayDelegateGrant

from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceDispatchAdmission,
    ServiceDispatchAdmissionError,
    ServiceRegistry,
)
from tests.unit.test_service_registry import _next_request, _register

OP = "direct.risk_first_reserve.v2"
KEY = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")
TOKEN = "9f" * 32  # synthetic opaque data, never persisted in registry SQLite


def setup_registry(*, path=":memory:", enabled=True, actor_kind="risk", port="service.direct_write", write=True):
    strategy = WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-p", "strategy-a")
    actor = WireIdentity(WireRole.SERVICE, "risk-p", "risk-actor")
    scope = WireScope("domain", account_ref="acct", strategy_ref="strategy-a", run_id="run")
    template = replace(scope, run_id=None)
    direct = frozenset({"direct.reserve"})
    requested = frozenset({OP})
    capability = ServiceCapability("gateway", port, direct | requested, direct | requested if write else direct)
    strategy_grant = ResourceGrant("gateway", port, "scope", template, direct)
    actor_grant = ResourceGrant("gateway", port, "scope", template, requested)
    clock = [100.0]
    registry = ServiceRegistry(
        path, service_id="gateway-service", capabilities=(capability,),
        grants={"strategy-p": (PrincipalGrant(strategy, (strategy_grant,)),),
                "risk-p": (PrincipalGrant(actor, (actor_grant,)),)},
        policy_version="p", requirements_version="r", lease_seconds=10,
        clock=lambda: clock[0], enable_gateway_risk_first_reserve_admission=enabled,
    )
    grant = GatewayDelegateGrant(actor, "acct", "strategy-a", "domain", actor_kind,
                                 "revision", frozenset({KEY}), direct)
    registry.configure_gateway_delegates(instance_id="gateway", scope_ref="scope", grants=(grant,))
    registry.register_handler("gateway", "direct.reserve", lambda *_: {"status": "unused"})
    strategy_result = _register(
        registry, principal="strategy-p", identity=strategy, scope=scope, key="strategy-key",
        instance_id="gateway", port=port, scope_ref="scope", operations=("direct.reserve",),
    )
    assert strategy_result["ok"], strategy_result
    return registry, clock, actor, scope, grant


def register_actor(registry, actor, scope, *, operation=OP):
    return _register(
        registry, principal="risk-p", identity=actor, scope=scope, key="risk-key",
        instance_id="gateway", port="service.direct_write", scope_ref="scope", operations=(operation,),
    )


def request(actor, scope, registration, args):
    return _next_request(OP, {
        "registration_id": registration["registration_id"],
        "service_epoch": registration["service_epoch"],
        "instance_id": "gateway", "arguments": args,
    }, identity=actor, scope=scope)


def test_default_off_and_write_capability_required():
    registry, _, actor, scope, _ = setup_registry(enabled=False)
    try:
        with pytest.raises(ValueError, match="not explicitly enabled"):
            registry.register_handler("gateway", OP, lambda *_: {})
        assert not register_actor(registry, actor, scope)["ok"]
    finally:
        registry.close()
    registry, _, _, _, _ = setup_registry(write=False)
    try:
        with pytest.raises(ValueError, match="not explicitly enabled"):
            registry.register_handler("gateway", OP, lambda *_: {})
    finally:
        registry.close()
    with pytest.raises(TypeError, match="enable_gateway_risk_first_reserve_admission"):
        setup_registry(enabled=1)


@pytest.mark.parametrize("enabled,install_handler,visible", [
    (False, False, False), (True, False, False), (True, True, True),
])
def test_discovery_only_advertises_enabled_installed_operation(enabled, install_handler, visible):
    registry, _, actor, scope, _ = setup_registry(enabled=enabled)
    try:
        if install_handler:
            registry.register_handler("gateway", OP, lambda *_: {})
        answer = registry.handle(
            "risk-p", _next_request("service.discover", {}, identity=actor, scope=scope)
        )
        assert answer["ok"], answer
        operations = answer["result"]["capabilities"][0]["operations"]
        assert "direct.reserve" in operations
        assert (OP in operations) is visible
    finally:
        registry.close()


def test_authentic_scope_and_argument_binding(tmp_path):
    path = tmp_path / "registry.sqlite"
    registry, _, actor, scope, grant = setup_registry(path=path)
    captured = []
    try:
        registry.register_handler("gateway", OP, lambda context, args: captured.append((context, args)) or {})
        registered = register_actor(registry, actor, scope)
        assert registered["ok"], registered
        args = {"request": {"attempt_nonce_digest": TOKEN, "payload": {"quantity": "1"}}}
        reply = registry.handle("risk-p", request(actor, scope, registered["result"], args))
        assert reply["ok"], reply
        context, wire_args = captured[0]
        admission = context.dispatch_admission
        assert type(admission) is ServiceDispatchAdmission
        assert admission.matches_context(context)
        assert admission.matches_operation(OP)
        assert admission.matches(wire_args)
        assert context._is_registry_wire_arguments(wire_args)
        assert not context._is_registry_wire_arguments(dict(wire_args))
        assert admission.matches_delegate_grant(grant)
        assert TOKEN not in repr(admission._binding)
        assert not admission.matches({"request": {"attempt_nonce_digest": "a" * 64}})
        assert not admission.matches_context(replace(context, principal_ref="foreign"))
        forged = copy.copy(admission)
        assert not forged.matches(wire_args)
        with admission.scope() as recheck:
            assert admission.matches_scope_recheck(recheck)
            recheck()
            with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_BUSY"), admission.scope():
                pass
        assert not admission.matches_scope_recheck(recheck)
        admission.close()
        assert not admission.matches(wire_args)
    finally:
        registry.close()
    assert TOKEN.encode() not in path.read_bytes()


def test_execution_grant_and_wrong_actor_cannot_use_risk_operation():
    registry, _, actor, scope, _ = setup_registry(actor_kind="execution")
    try:
        registry.register_handler("gateway", OP, lambda *_: {})
        assert not register_actor(registry, actor, scope)["ok"]
        assert registry.gateway_delegate_grant(instance_id="gateway", scope_ref="scope", identity=actor,
                                               scope=scope, operation=OP) is None
    finally:
        registry.close()


def test_wrong_port_and_non_service_identity_are_not_admitted():
    registry, _, _, _, _ = setup_registry(port="service.risk_order")
    try:
        with pytest.raises(ValueError, match="not explicitly enabled"):
            registry.register_handler("gateway", OP, lambda *_: {})
    finally:
        registry.close()
    registry, _, actor, scope, _ = setup_registry()
    try:
        for role in (WireRole.STRATEGY_CLIENT, WireRole.APPLICATION_CLIENT):
            identity = replace(actor, role=role)
            assert registry.gateway_delegate_grant(instance_id="gateway", scope_ref="scope", identity=identity,
                                                   scope=scope, operation=OP) is None
        assert registry.gateway_delegate_grant(instance_id="gateway", scope_ref="scope",
                                               identity=replace(actor, delegate_ref="delegate"),
                                               scope=scope, operation=OP) is None
    finally:
        registry.close()


def test_v1_resource_grant_does_not_grant_new_operation():
    registry, _, actor, scope, _ = setup_registry()
    try:
        registry.register_handler("gateway", OP, lambda *_: {})
        existing = registry._grants["risk-p"][0]
        old = replace(existing.resources[0], operations=frozenset({"direct.delegated_reserve"}))
        registry._grants = {**registry._grants, "risk-p": (replace(existing, resources=(old,)),)}
        assert not register_actor(registry, actor, scope)["ok"]
    finally:
        registry.close()


@pytest.mark.parametrize("condition", [
    "lease", "revoked", "resource_revoked", "strategy_expired", "run_changed", "not_ready",
    "stopped", "disabled", "epoch", "write_removed", "capability_removed", "pending_expiration",
])
def test_live_admission_rechecks_authority(condition):
    registry, clock, actor, scope, _ = setup_registry()
    captured = []
    try:
        registry.register_handler("gateway", OP, lambda context, args: captured.append(context) or {})
        registration = register_actor(registry, actor, scope)["result"]
        assert registry.handle("risk-p", request(actor, scope, registration, {"request": {}}))["ok"]
        admission = captured[0].dispatch_admission
        if condition == "lease":
            clock[0] = 111.0
        elif condition == "revoked":
            with registry._lock:
                registry._gateway_delegate_grants.clear()
        elif condition == "resource_revoked":
            with registry._lock:
                registry._grants = {}
        elif condition == "strategy_expired":
            with registry._lock:
                registry._db.execute("UPDATE sessions SET expires_at=0 WHERE principal=?", ("strategy-p",))
        elif condition == "run_changed":
            with registry._transaction():
                registry._db.execute("UPDATE strategy_write_claims SET run_id='other'")
        elif condition == "not_ready":
            registry.set_ready(False)
        elif condition == "stopped":
            assert registry.freeze_strategy(scope, "stop-1")["status"] == "applied"
        elif condition == "disabled":
            registry._gateway_risk_first_reserve_admission_enabled = False
        elif condition == "epoch":
            with registry._lock:
                registry._db.execute("UPDATE registry_meta SET value='other' WHERE key='service_epoch'")
        elif condition == "write_removed":
            with registry._transaction():
                registry._db.execute("UPDATE sessions SET write_operations_json='[]' WHERE principal=?", ("risk-p",))
        elif condition == "capability_removed":
            registry._capabilities = {}
        else:
            registry.configure_gateway_lease_drain(instance_id="gateway", account_ref="acct",
                                                   strategies=frozenset({"strategy-a"}), expiry_seconds=0)
            with registry._transaction():
                registry._db.execute(
                    "INSERT INTO gateway_lease_expirations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("event", "gateway", "acct", "strategy-a", "run", "strategy-p", "registration",
                     registry._epoch, 1.0, 1.0, "PENDING", 1.0),
                )
        with pytest.raises(ServiceDispatchAdmissionError), admission.scope():
            pass
    finally:
        registry.close()


def test_exact_context_role_scope_and_registration_are_rejected_when_changed():
    registry, _, actor, scope, _ = setup_registry()
    try:
        registry.register_handler("gateway", OP, lambda *_: {})
        registration = register_actor(registry, actor, scope)["result"]
        bad_inputs = [
            (replace(actor, principal_ref="other"), scope, registration),
            (replace(actor, delegate_ref="nested"), scope, registration),
            (actor, replace(scope, run_id="other"), registration),
            (actor, replace(scope, account_ref="other"), registration),
            (actor, scope, dict(registration, registration_id="other")),
            (actor, scope, dict(registration, service_epoch="other")),
        ]
        for identity, altered_scope, receipt in bad_inputs:
            assert not registry.handle("risk-p", request(identity, altered_scope, receipt, {"request": {}}))["ok"]
    finally:
        registry.close()


def test_owner_thread_binding():
    registry, _, actor, scope, _ = setup_registry()
    captured = []
    try:
        registry.register_handler("gateway", OP, lambda context, args: captured.append(context) or {})
        registration = register_actor(registry, actor, scope)["result"]
        assert registry.handle("risk-p", request(actor, scope, registration, {"request": {}}))["ok"]
        admission = captured[0].dispatch_admission
        with admission.scope():
            pass
        errors = []
        def other_owner():
            try:
                with admission.scope():
                    pass
            except ServiceDispatchAdmissionError as exc:
                errors.append(exc.code)
        thread = threading.Thread(target=other_owner)
        thread.start()
        thread.join(2)
        assert errors == ["WRONG_OWNER_THREAD"]
    finally:
        registry.close()
