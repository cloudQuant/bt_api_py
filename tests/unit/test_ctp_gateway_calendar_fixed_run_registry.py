"""Calendar-only exact-run Registry grants never widen strategy permissions."""
from dataclasses import replace

import pytest

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.service_registry import (PrincipalGrant, ResourceGrant, ServiceCapability,
                                        ServiceRegistry, validate_capabilities_and_grants)
from tests.unit.test_service_registry import _business, _register

IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, "principal", "strategy")
SCOPE = WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run-a")
OPS = frozenset({"calendar.query.v1", "calendar.windows.v1"})


def test_fixed_run_matches_only_exact_calendar_resource():
    resource = ResourceGrant("instance", "service.calendar_read", "scope", SCOPE, OPS)
    capability = ServiceCapability("instance", "service.calendar_read", OPS, frozenset())
    validate_capabilities_and_grants((capability,), {"principal": (PrincipalGrant(IDENTITY, (resource,)),)})
    registry = object.__new__(ServiceRegistry)
    assert registry._resource_scope_matches(resource, SCOPE, IDENTITY)
    assert not registry._scope_matches(SCOPE, SCOPE, IDENTITY)
    for changed in (replace(SCOPE, run_id="run-b"), replace(SCOPE, account_ref="other"),
                    replace(SCOPE, execution_domain_ref="other"), replace(SCOPE, strategy_ref="other")):
        assert not registry._resource_scope_matches(resource, changed, IDENTITY)


def test_registration_rechecks_exact_scope_for_each_read(tmp_path):
    resource = ResourceGrant("instance", "service.calendar_read", "scope", SCOPE, OPS)
    capability = ServiceCapability("instance", "service.calendar_read", OPS, frozenset())
    registry = ServiceRegistry(tmp_path / "registry.sqlite", service_id="service",
        capabilities=(capability,), grants={"principal": (PrincipalGrant(IDENTITY, (resource,)),)},
        policy_version="policy", requirements_version="requirements")
    calls = []
    registry.register_handler("instance", "calendar.query.v1", lambda ctx, args: calls.append(ctx.scope) or {"ok": True})
    try:
        receipt = _register(registry, identity=IDENTITY, scope=SCOPE, principal="principal",
                            operations=("calendar.query.v1",), instance_id="instance",
                            port="service.calendar_read", scope_ref="scope")["result"]
        def read(scope):
            return _business(registry, receipt, identity=IDENTITY, scope=scope, principal="principal",
                             operation="calendar.query.v1", instance_id="instance")
        assert read(SCOPE)["ok"] is True
        assert read(replace(SCOPE, run_id="other"))["code"] == "SCOPE_DENIED"
        assert read(replace(SCOPE, account_ref="other"))["code"] == "SCOPE_DENIED"
        assert calls == [SCOPE]
    finally:
        registry.close()


@pytest.mark.parametrize("port,operations,writes", [
    ("service.direct_write", frozenset({"direct.command_status"}), frozenset()),
    ("service.calendar_read", frozenset({"direct.command_status"}), frozenset()),
    ("service.calendar_read", OPS, frozenset({"calendar.query.v1"})),
])
def test_fixed_run_rejects_other_ports_operations_and_write_capability(port, operations, writes):
    resource = ResourceGrant("instance", port, "scope", SCOPE, operations)
    capability = ServiceCapability("instance", port, operations, writes)
    with pytest.raises(ValueError):
        validate_capabilities_and_grants((capability,), {"principal": (PrincipalGrant(IDENTITY, (resource,)),)})
