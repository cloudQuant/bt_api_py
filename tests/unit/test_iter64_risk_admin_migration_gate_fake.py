"""Risk admin writes cross the migration gate even without a capability label."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

import bt_api_py.service_registry as registry_module
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from tests.unit.test_service_registry import _next_request, _register

WRITES = ("risk.policy.publish", "risk.latch.release", "risk.cancel.request")
READS = ("risk.admin.status", "risk.policy.read", "risk.latch.list", "risk.unknown.list",
         "risk.counts.export", "risk.audit.read", "risk.cancel.status")


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
def registry_factory(monkeypatch):
    thread = object()
    monkeypatch.setattr(registry_module, "threading", SimpleNamespace(
        Lock=FakeLock, RLock=FakeLock, current_thread=lambda: thread,
    ))
    registries = []

    def make(*, operations, write_operations=(), identity=None, scope=None):
        identity = identity or WireIdentity(WireRole.APPLICATION_CLIENT, "principal-a", "operator-a")
        scope = scope or WireScope("domain-a", account_ref="account-a")
        capability = ServiceCapability("risk-a", "service.risk_admin", frozenset(operations),
                                       frozenset(write_operations))
        grant_scope = replace(scope, run_id=None) if scope.run_id is not None else scope
        grant = ResourceGrant("risk-a", "service.risk_admin", "scope-a", grant_scope, frozenset(operations))
        registry = ServiceRegistry(
            ":memory:", service_id="risk-service", capabilities=(capability,),
            grants={"curve-a": (PrincipalGrant(identity, (grant,)),)},
            policy_version="policy-v1", requirements_version="requirements-v1",
            clock=lambda: 100.0, lease_seconds=10,
        )
        registries.append(registry)
        registry._db.execute("PRAGMA temp_store=MEMORY")
        assert registry._db.execute("PRAGMA temp_store").fetchone()[0] == 2
        assert registry._db.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
        assert registry._db.execute("PRAGMA database_list").fetchone()[2] == ""
        calls = []
        for operation in operations:
            registry.register_handler("risk-a", operation, lambda context, args: calls.append((context, args)) or {})
        reply = _register(registry, principal="curve-a", identity=identity, scope=scope,
                          instance_id="risk-a", port="service.risk_admin", scope_ref="scope-a",
                          operations=tuple(operations))
        return registry, identity, scope, reply, calls

    yield make
    for registry in registries:
        registry.close()


def call(registry, identity, scope, registration, operation):
    return registry.handle("curve-a", _next_request(operation, {
        "registration_id": registration["registration_id"],
        "service_epoch": registration["service_epoch"],
        "instance_id": "risk-a", "arguments": {},
    }, identity=identity, scope=scope))


def test_builtin_declares_only_current_risk_admin_writes():
    from bt_api_py.service_components import _ROLE_BUILTINS

    manifest = _ROLE_BUILTINS[("risk", "order_service")]
    assert manifest.write_operations_by_port["service.risk_admin"] == frozenset(WRITES)
    assert frozenset(READS).isdisjoint(manifest.write_operations_by_port["service.risk_admin"])


@pytest.mark.parametrize("operation", WRITES)
@pytest.mark.parametrize("declared", [False, True])
def test_admin_write_blocked_by_migration_even_if_not_declared(registry_factory, operation, declared):
    registry, identity, scope, registration, calls = registry_factory(
        operations=(operation,), write_operations=(operation,) if declared else (),
    )
    assert registration["ok"]
    registry._migration_writes_enabled = False
    result = call(registry, identity, scope, registration["result"], operation)
    assert not result["ok"] and result["code"] == "MIGRATION_WRITE_DISABLED"
    assert calls == []


@pytest.mark.parametrize("operation", WRITES)
def test_admin_write_records_migration_before_handler(registry_factory, monkeypatch, operation):
    registry, identity, scope, registration, calls = registry_factory(operations=(operation,))
    assert registration["ok"]
    events = []
    monkeypatch.setattr(registry, "_record_migration_write_attempt", lambda *, fact_kind: events.append(fact_kind))
    assert call(registry, identity, scope, registration["result"], operation)["ok"]
    assert events == ["dispatch"] and len(calls) == 1


@pytest.mark.parametrize("operation", READS)
def test_current_admin_read_remains_available_without_migration_writes(registry_factory, operation):
    registry, identity, scope, registration, calls = registry_factory(operations=(operation,))
    assert registration["ok"]
    registry._migration_writes_enabled = False
    assert call(registry, identity, scope, registration["result"], operation)["ok"]
    assert len(calls) == 1


@pytest.mark.parametrize("declared", [False, True])
def test_strategy_identity_cannot_register_admin_write(registry_factory, declared):
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "strategy-a")
    scope = WireScope("domain-a", account_ref="account-a", strategy_ref="strategy-a", run_id="run-a")
    _, _, _, registration, _ = registry_factory(
        operations=("risk.cancel.request",),
        write_operations=("risk.cancel.request",) if declared else (), identity=identity, scope=scope,
    )
    assert not registration["ok"] and registration["code"] == "SCOPE_DENIED"
