"""Registry-only admin cancel admissions; no host, files, OS locks or threads."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

import bt_api_py.service_registry as registry_module
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    RiskAdminCancelAdmission,
    ServiceCapability,
    ServiceDispatchAdmission,
    ServiceDispatchAdmissionError,
    ServiceRegistry,
)
from tests.unit.test_service_registry import _next_request, _register

OP = "risk.cancel.request"
OTHER = "risk.cancel.status"
ARGS = {"operation_id": "operation-a", "scope_kind": "strategy", "scope_value": "strategy-a"}


class FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def acquire(self, *_args, **_kwargs):
        return True

    def release(self):
        pass


@pytest.fixture
def setup_registry(monkeypatch):
    thread = object()
    fake_threading = SimpleNamespace(Lock=FakeLock, RLock=FakeLock, current_thread=lambda: thread)
    monkeypatch.setattr(registry_module, "threading", fake_threading)
    registry_holder = []

    def create(*, enabled=True, handler=None, operations=(OP, OTHER), write_operations=()):
        clock = [100.0]
        identity = WireIdentity(WireRole.APPLICATION_CLIENT, "principal-a", "operator-a")
        scope = WireScope("domain-a", account_ref="account-a")
        capability = ServiceCapability(
            "risk-a", "service.risk_admin", frozenset(operations), frozenset(write_operations),
        )
        grant = ResourceGrant("risk-a", "service.risk_admin", "scope-a", scope, frozenset(operations))
        registry = ServiceRegistry(
            ":memory:", service_id="risk-service", capabilities=(capability,),
            grants={"curve-a": (PrincipalGrant(identity, (grant,)),)},
            policy_version="policy-v1", requirements_version="requirements-v1", clock=lambda: clock[0],
            lease_seconds=10, enable_risk_admin_cancel_admission=enabled,
        )
        registry_holder.append(registry)
        registry._db.execute("PRAGMA temp_store=MEMORY")
        assert registry._db.execute("PRAGMA temp_store").fetchone()[0] == 2
        assert registry._db.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
        assert registry._db.execute("PRAGMA database_list").fetchone()[2] == ""
        captured = []
        callback = handler or (lambda context, args: captured.append((context, args)) or {"status": "ok"})
        for operation in operations:
            registry.register_handler("risk-a", operation, callback)
        reply = _register(
            registry, principal="curve-a", identity=identity, scope=scope,
            instance_id="risk-a", port="service.risk_admin", scope_ref="scope-a", operations=operations,
        )
        assert reply["ok"], reply
        return registry, clock, identity, scope, reply["result"], captured

    yield create
    for registry in registry_holder:
        registry.close()


def dispatch(registry, identity, scope, registration, arguments=ARGS, *, operation=OP):
    return registry.handle(
        "curve-a", _next_request(operation, {
            "registration_id": registration["registration_id"],
            "service_epoch": registration["service_epoch"],
            "instance_id": "risk-a", "arguments": arguments,
        }, identity=identity, scope=scope),
    )


def test_real_registry_issues_single_request_admission_and_closes_it_on_return(setup_registry):
    observed = []

    def handler(context, args):
        admission = context.dispatch_admission
        assert type(admission) is RiskAdminCancelAdmission
        assert type(admission) is not ServiceDispatchAdmission
        assert admission.matches_context(context) and admission.matches(args)
        assert admission.matches_operation(OP) and not admission.matches_operation(OTHER)
        assert not admission.matches(dict(args, scope_value="strategy-b"))
        assert not admission.matches_context(replace(context))
        with admission.scope() as recheck:
            assert admission.matches_scope_recheck(recheck)
            recheck()
            observed.append((context, args, admission))
        assert not admission.matches_scope_recheck(recheck)
        with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CONSUMED"), admission.scope():
            pass
        return {"status": "ok"}

    registry, _, identity, scope, registration, _ = setup_registry(handler=handler)
    assert dispatch(registry, identity, scope, registration)["ok"]
    context, args, admission = observed[0]
    assert not admission.matches_context(context) and not admission.matches(args)
    with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"), admission.scope():
        pass


def test_default_disabled_and_other_admin_operation_are_unchanged(setup_registry):
    registry, _, identity, scope, registration, captured = setup_registry(enabled=False)
    assert dispatch(registry, identity, scope, registration)["ok"]
    assert captured[-1][0].dispatch_admission is None
    registry, _, identity, scope, registration, captured = setup_registry()
    assert dispatch(registry, identity, scope, registration, {}, operation=OTHER)["ok"]
    assert captured[-1][0].dispatch_admission is None


@pytest.mark.parametrize("condition", ["expired", "unregistered", "revoked", "not_ready", "policy",
                                     "stale_epoch", "disabled", "capability_removed", "registration_changed"])
def test_admission_rechecks_registry_authority_before_local_commit(setup_registry, condition):
    holder = {}

    def handler(context, args):
        admission = context.dispatch_admission
        with admission.scope() as recheck:
            recheck()
            registry, clock, registration = holder["registry"], holder["clock"], holder["registration"]
            if condition == "expired":
                clock[0] += 10
            elif condition == "unregistered":
                registry._db.execute("DELETE FROM sessions WHERE registration_id=?", (registration["registration_id"],))
            elif condition == "revoked":
                registry._grants = {}
            elif condition == "not_ready":
                registry._ready = False
            elif condition == "policy":
                registry.policy_version = "policy-v2"
            elif condition == "stale_epoch":
                registry._epoch = "other-epoch"
            elif condition == "disabled":
                registry._risk_admin_cancel_admission_enabled = False
            elif condition == "capability_removed":
                registry._capabilities = {}
            else:
                registry._db.execute("UPDATE sessions SET request_digest='different' WHERE registration_id=?",
                                     (registration["registration_id"],))
            with pytest.raises(ServiceDispatchAdmissionError):
                recheck()
        return {"status": "ok"}

    registry, clock, identity, scope, registration, _ = setup_registry(handler=handler)
    holder.update(registry=registry, clock=clock, registration=registration)
    assert dispatch(registry, identity, scope, registration)["ok"]


def test_unregistration_before_scope_is_refused(setup_registry):
    holder = {}

    def handler(context, _args):
        registry = holder["registry"]
        result = registry.handle("curve-a", _next_request("service.unregister", {
            "registration_id": context.registration_id, "service_epoch": holder["registration"]["service_epoch"],
        }, identity=context.identity, scope=context.scope))
        assert result["ok"]
        with pytest.raises(ServiceDispatchAdmissionError, match="NOT_REGISTERED"), context.dispatch_admission.scope():
            pytest.fail("stale registration entered")
        return {"status": "ok"}

    registry, _, identity, scope, registration, _ = setup_registry(handler=handler)
    holder["registry"] = registry
    holder["registration"] = registration
    assert dispatch(registry, identity, scope, registration)["ok"]


def test_close_and_nested_use_fail_closed_during_scope(setup_registry):
    def handler(context, _args):
        admission = context.dispatch_admission
        with admission.scope() as recheck:
            with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CONSUMED"), admission.scope():
                pass
            with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_SCOPE_ACTIVE"):
                admission.close()
            with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"):
                recheck()
        return {"status": "ok"}

    registry, _, identity, scope, registration, _ = setup_registry(handler=handler)
    assert dispatch(registry, identity, scope, registration)["ok"]


def test_recheck_cannot_move_to_a_different_owner_thread(setup_registry, monkeypatch):
    def handler(context, _args):
        admission = context.dispatch_admission
        current_thread = registry_module.threading.current_thread
        with admission.scope() as recheck:
            monkeypatch.setattr(registry_module.threading, "current_thread", lambda: object())
            assert not admission.matches_scope_recheck(recheck)
            with pytest.raises(ServiceDispatchAdmissionError, match="WRONG_ISSUER_THREAD"):
                recheck()
            monkeypatch.setattr(registry_module.threading, "current_thread", current_thread)
            recheck()
        return {"status": "ok"}

    registry, _, identity, scope, registration, _ = setup_registry(handler=handler)
    assert dispatch(registry, identity, scope, registration)["ok"]


def test_foreign_thread_cannot_enter_or_close_before_issuer_scope(setup_registry, monkeypatch):
    def handler(context, _args):
        admission = context.dispatch_admission
        current_thread = registry_module.threading.current_thread
        foreign_thread = object()
        monkeypatch.setattr(registry_module.threading, "current_thread", lambda: foreign_thread)
        with pytest.raises(ServiceDispatchAdmissionError, match="WRONG_ISSUER_THREAD"), admission.scope():
            pytest.fail("foreign thread entered")
        with pytest.raises(ServiceDispatchAdmissionError, match="WRONG_ISSUER_THREAD"):
            admission.close()
        monkeypatch.setattr(registry_module.threading, "current_thread", current_thread)
        with admission.scope() as recheck:
            recheck()
        return {"status": "ok"}

    registry, _, identity, scope, registration, _ = setup_registry(handler=handler)
    assert dispatch(registry, identity, scope, registration)["ok"]


def test_handler_return_with_open_scope_is_failure_and_recheck_is_invalidated(setup_registry):
    retained = []

    def handler(context, _args):
        admission = context.dispatch_admission
        manager = admission.scope()
        recheck = manager.__enter__()
        retained.append((manager, recheck))
        return {"status": "ok"}

    registry, _, identity, scope, registration, _ = setup_registry(handler=handler)
    try:
        result = dispatch(registry, identity, scope, registration)
        assert not result["ok"] and result["code"] == "HANDLER_FAILED"
        with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"):
            retained[0][1]()
    finally:
        retained[0][0].__exit__(None, None, None)


def test_handler_error_invalidates_admission(setup_registry):
    retained = []

    def handler(context, _args):
        retained.append(context.dispatch_admission)
        raise RuntimeError("synthetic handler failure")

    registry, _, identity, scope, registration, _ = setup_registry(handler=handler)
    result = dispatch(registry, identity, scope, registration)
    assert not result["ok"] and result["code"] == "HANDLER_FAILED"
    with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"), retained[0].scope():
        pass


def test_handler_interrupt_invalidates_admission(setup_registry):
    retained = []

    def handler(context, _args):
        retained.append(context.dispatch_admission)
        raise KeyboardInterrupt("synthetic interrupt")

    registry, _, identity, scope, registration, _ = setup_registry(handler=handler)
    with pytest.raises(KeyboardInterrupt, match="synthetic interrupt"):
        dispatch(registry, identity, scope, registration)
    with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"), retained[0].scope():
        pass


@pytest.mark.parametrize("arguments", [
    {"operation_id": "op", "scope_kind": "strategy"},
    dict(ARGS, extra="value"),
    dict(ARGS, scope_value=["strategy-a"]),
])
def test_invalid_complete_arguments_do_not_reach_handler(setup_registry, arguments):
    registry, _, identity, scope, registration, captured = setup_registry()
    reply = dispatch(registry, identity, scope, registration, arguments)
    assert not reply["ok"] and reply["code"] == "INVALID_REQUEST"
    assert not captured


def test_caller_cannot_construct_or_copy_an_admission(setup_registry):
    import copy

    def handler(context, args):
        admission = context.dispatch_admission
        with pytest.raises(ServiceDispatchAdmissionError, match="NOT_ISSUED"):
            RiskAdminCancelAdmission(object(), admission._registry, binding=admission._binding, arguments=args)
        copied = copy.copy(admission)
        assert not copied.matches(args)
        assert not copied.matches_context(replace(context, dispatch_admission=copied))
        with pytest.raises(ServiceDispatchAdmissionError, match="ADMISSION_CLOSED"), copied.scope():
            pass
        with admission.scope() as recheck:
            active_copy = copy.copy(admission)
            assert admission.matches_scope_recheck(recheck)
            assert not active_copy.matches_scope_recheck(recheck)
        return {"status": "ok"}

    registry, _, identity, scope, registration, _ = setup_registry(handler=handler)
    assert dispatch(registry, identity, scope, registration)["ok"]


def test_expiry_rolls_back_a_local_memory_transaction(setup_registry):
    import sqlite3

    local = sqlite3.connect(":memory:")
    local.execute("PRAGMA temp_store=MEMORY")
    assert local.execute("PRAGMA temp_store").fetchone()[0] == 2
    assert local.execute("PRAGMA journal_mode=MEMORY").fetchone()[0] == "memory"
    assert local.execute("PRAGMA database_list").fetchone()[2] == ""
    local.execute("CREATE TABLE local_intents (operation_id TEXT)")
    holder = {}

    def handler(context, args):
        with (pytest.raises(ServiceDispatchAdmissionError, match="NOT_REGISTERED"),
              context.dispatch_admission.scope() as recheck):
            local.execute("BEGIN IMMEDIATE")
            try:
                recheck()
                local.execute("INSERT INTO local_intents VALUES (?)", (args["operation_id"],))
                holder["clock"][0] += 10
                recheck()
                local.commit()
            except BaseException:
                local.rollback()
                raise
        return {"status": "ok"}

    try:
        registry, clock, identity, scope, registration, _ = setup_registry(handler=handler)
        holder["clock"] = clock
        assert dispatch(registry, identity, scope, registration)["ok"]
        assert local.execute("SELECT COUNT(*) FROM local_intents").fetchone()[0] == 0
    finally:
        local.close()


def test_opted_in_cancel_crosses_migration_write_gate(setup_registry):
    registry, _, identity, scope, registration, captured = setup_registry()
    registry._migration_writes_enabled = False
    reply = dispatch(registry, identity, scope, registration)
    assert not reply["ok"] and reply["code"] == "MIGRATION_WRITE_DISABLED"
    assert not captured


RISK_ADMIN_WRITES = ("risk.policy.publish", "risk.latch.release", OP)
RISK_ADMIN_READS = ("risk.admin.status", "risk.policy.read", "risk.latch.list", "risk.unknown.list",
                    "risk.counts.export", "risk.audit.read", "risk.cancel.status")


def test_builtin_manifest_declares_only_current_risk_admin_writes():
    from bt_api_py.service_components import _ROLE_BUILTINS

    manifest = _ROLE_BUILTINS[("risk", "order_service")]
    assert manifest.write_operations_by_port["service.risk_admin"] == frozenset(RISK_ADMIN_WRITES)
    assert frozenset(RISK_ADMIN_READS).isdisjoint(manifest.write_operations_by_port["service.risk_admin"])


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("declared", [False, True])
@pytest.mark.parametrize("operation", RISK_ADMIN_WRITES)
def test_all_risk_admin_writes_respect_migration_gate_without_admission_dependency(
    setup_registry, enabled, declared, operation,
):
    writes = (operation,) if declared else ()
    registry, _, identity, scope, registration, captured = setup_registry(
        enabled=enabled, operations=(operation,), write_operations=writes,
    )
    registry._migration_writes_enabled = False
    arguments = ARGS if operation == OP else {}
    result = dispatch(registry, identity, scope, registration, arguments, operation=operation)
    assert not result["ok"] and result["code"] == "MIGRATION_WRITE_DISABLED"
    assert not captured


@pytest.mark.parametrize("operation", RISK_ADMIN_READS)
def test_risk_admin_read_remains_available_when_migration_writes_disabled(setup_registry, operation):
    registry, _, identity, scope, registration, captured = setup_registry(operations=(operation,))
    registry._migration_writes_enabled = False
    assert dispatch(registry, identity, scope, registration, {}, operation=operation)["ok"]
    assert len(captured) == 1 and captured[0][0].dispatch_admission is None


@pytest.mark.parametrize("operation", RISK_ADMIN_WRITES)
def test_admin_write_is_recorded_before_handler_even_if_capability_omits_write_flag(
    setup_registry, monkeypatch, operation,
):
    registry, _, identity, scope, registration, captured = setup_registry(
        enabled=False, operations=(operation,), write_operations=(),
    )
    calls = []
    monkeypatch.setattr(registry, "_record_migration_write_attempt", lambda *, fact_kind: calls.append(fact_kind))
    assert dispatch(registry, identity, scope, registration, ARGS if operation == OP else {},
                    operation=operation)["ok"]
    assert calls == ["dispatch"] and len(captured) == 1


@pytest.mark.parametrize("declared", [False, True])
def test_non_operator_scope_cannot_register_risk_admin_write(setup_registry, declared):
    registry, _, _, _, _, _ = setup_registry(
        enabled=False, operations=(OP,), write_operations=(OP,) if declared else (),
    )
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-principal", "strategy-a")
    scope = WireScope("domain-a", account_ref="account-a", strategy_ref="strategy-a", run_id="run-a")
    grant = ResourceGrant("risk-a", "service.risk_admin", "scope-b", scope, frozenset({OP}))
    registry._grants = {"curve-b": (PrincipalGrant(identity, (grant,)),)}
    with registry._transaction():
        code, _, _ = registry._authorized_resource(
            "curve-b", identity, instance_id="risk-a", port="service.risk_admin",
            scope_ref="scope-b", scope=scope, requested_operations=frozenset({OP}),
        )
    assert code == "SCOPE_DENIED"


def test_revoked_opt_in_never_falls_back_to_unprotected_handler(setup_registry):
    registry, _, identity, scope, registration, captured = setup_registry()
    registry._risk_admin_cancel_admission_enabled = False
    reply = dispatch(registry, identity, scope, registration)
    assert not reply["ok"] and reply["code"] == "CAPABILITY_UNAVAILABLE"
    assert not captured


def test_untrusted_registration_identity_and_account_never_reach_handler(setup_registry):
    registry, _, identity, scope, registration, captured = setup_registry()
    assert not dispatch(registry, replace(identity, actor_ref="other"), scope, registration)["ok"]
    assert not dispatch(registry, identity, replace(scope, account_ref="other"), registration)["ok"]
    assert not captured
