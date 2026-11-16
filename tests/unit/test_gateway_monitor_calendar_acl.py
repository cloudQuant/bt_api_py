"""Independent Monitor calendar authorization and Registry grants."""

from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_calendar_service import CalendarMonitorGrant, GatewayCalendarService
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCallContext,
    ServiceCapability,
    ServiceRegistry,
    validate_capabilities_and_grants,
)
from tests.unit.test_gateway_calendar_ranges import REQUEST
from tests.unit.test_service_registry import _business, _register

OP = "calendar.monitor.windows.v1"
IDENTITY = WireIdentity(WireRole.SERVICE, "monitor", "actor")
SCOPE = WireScope("domain", account_ref="account")
GRANT = CalendarMonitorGrant(
    "monitor", "actor", "domain", "account", "route-a", "SIM", "future", "x", "x2601"
)
CONTEXT = ServiceCallContext(IDENTITY, SCOPE, "registration", "monitor", lambda _: True)
CAP = ServiceCapability(
    "instance", "service.calendar_read", frozenset({OP, "calendar.query.v1", "calendar.windows.v1"})
)
RESOURCE = ResourceGrant("instance", "service.calendar_read", "scope", SCOPE, frozenset({OP}))


def test_monitor_service_acl_checks_every_identity_and_request_field():
    service = GatewayCalendarService(grants=frozenset(), monitor_grants=frozenset({GRANT}))
    # Correct authorization reaches the separate registry availability check.
    with pytest.raises(ValueError, match="SERVICE_UNAVAILABLE"):
        service.handle(OP, context=CONTEXT, arguments=REQUEST)
    changes = [
        replace(CONTEXT, identity=replace(IDENTITY, **{field: value}))
        for field, value in (
            ("principal_ref", "other"),
            ("actor_ref", "other"),
            ("delegate_ref", "other"),
            ("role", WireRole.STRATEGY_CLIENT),
        )
    ]
    changes += [
        replace(CONTEXT, scope=replace(SCOPE, **{field: value}))
        for field, value in (
            ("execution_domain_ref", "other"),
            ("account_ref", "other"),
            ("strategy_ref", "other"),
            ("portfolio_ref", "other"),
        )
    ]
    for context in changes:
        with pytest.raises(ValueError, match="SCOPE_DENIED"):
            service.handle(OP, context=context, arguments=REQUEST)
    for field, value in (
        ("route", "other"),
        ("venue", "other"),
        ("kind", "option"),
        ("product", "other"),
        ("contract", "other"),
    ):
        with pytest.raises(ValueError, match="SCOPE_DENIED"):
            service.handle(OP, context=CONTEXT, arguments=dict(REQUEST, **{field: value}))
    with pytest.raises(ValueError, match="SCOPE_DENIED"):
        service.handle("calendar.windows.v1", context=CONTEXT, arguments=REQUEST)
    with pytest.raises(ValueError):
        service.handle(OP, context=CONTEXT, arguments=dict(REQUEST, policy="injected"))
    from tests.unit.test_gateway_calendar_service import GRANT as STRATEGY_GRANT

    no_monitor_grant = GatewayCalendarService(grants=frozenset({STRATEGY_GRANT}))
    with pytest.raises(ValueError, match="SCOPE_DENIED"):
        no_monitor_grant.handle(OP, context=CONTEXT, arguments=REQUEST)


def test_service_resource_registry_rechecks_registration_and_each_call(tmp_path):
    registry = ServiceRegistry(
        tmp_path / "registry.sqlite",
        service_id="service",
        capabilities=(CAP,),
        grants={"monitor": (PrincipalGrant(IDENTITY, (RESOURCE,)),)},
        policy_version="policy",
        requirements_version="requirements",
    )
    calls = []
    registry.register_handler(
        "instance", OP, lambda ctx, args: calls.append(ctx.scope) or {"ok": True}
    )
    try:
        receipt = _register(
            registry,
            identity=IDENTITY,
            scope=SCOPE,
            principal="monitor",
            operations=(OP,),
            instance_id="instance",
            port="service.calendar_read",
            scope_ref="scope",
        )["result"]

        def read(scope):
            return _business(
                registry,
                receipt,
                identity=IDENTITY,
                scope=scope,
                principal="monitor",
                operation=OP,
                instance_id="instance",
            )

        assert read(SCOPE)["ok"] is True
        assert read(replace(SCOPE, account_ref="other"))["code"] == "SCOPE_DENIED"
        assert calls == [SCOPE]
        denied = _register(
            registry,
            identity=IDENTITY,
            scope=replace(SCOPE, account_ref="other"),
            principal="monitor",
            operations=(OP,),
            instance_id="instance",
            port="service.calendar_read",
            scope_ref="scope",
        )
        assert denied["ok"] is False
    finally:
        registry.close()


def test_without_monitor_resource_registry_refuses_registration(tmp_path):
    strategy = WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-p", "strategy")
    strategy_scope = WireScope("domain", account_ref="account", strategy_ref="strategy")
    strategy_resource = ResourceGrant(
        "instance",
        "service.calendar_read",
        "scope",
        strategy_scope,
        frozenset({"calendar.query.v1"}),
    )
    registry = ServiceRegistry(
        tmp_path / "registry.sqlite",
        service_id="service",
        capabilities=(CAP,),
        grants={"strategy-p": (PrincipalGrant(strategy, (strategy_resource,)),)},
        policy_version="policy",
        requirements_version="requirements",
    )
    try:
        denied = _register(
            registry,
            identity=IDENTITY,
            scope=SCOPE,
            principal="monitor",
            operations=(OP,),
            instance_id="instance",
            port="service.calendar_read",
            scope_ref="scope",
        )
        assert denied["ok"] is False
    finally:
        registry.close()


def test_resource_grant_alone_does_not_authorize_calendar_query(tmp_path):
    from tests.unit.test_gateway_calendar_service import GRANT as STRATEGY_GRANT

    service = GatewayCalendarService(grants=frozenset({STRATEGY_GRANT}))
    registry = ServiceRegistry(
        tmp_path / "registry.sqlite",
        service_id="service",
        capabilities=(CAP,),
        grants={"monitor": (PrincipalGrant(IDENTITY, (RESOURCE,)),)},
        policy_version="policy",
        requirements_version="requirements",
    )
    registry.register_handler(
        "instance", OP, lambda ctx, args: service.handle(OP, context=ctx, arguments=REQUEST)
    )
    try:
        receipt = _register(
            registry,
            identity=IDENTITY,
            scope=SCOPE,
            principal="monitor",
            operations=(OP,),
            instance_id="instance",
            port="service.calendar_read",
            scope_ref="scope",
        )["result"]
        result = _business(
            registry,
            receipt,
            identity=IDENTITY,
            scope=SCOPE,
            principal="monitor",
            operation=OP,
            instance_id="instance",
        )
        assert result["ok"] is False
    finally:
        registry.close()


@pytest.mark.parametrize(
    "identity,resource",
    [
        (WireIdentity(WireRole.STRATEGY_CLIENT, "monitor", "actor"), RESOURCE),
        (replace(IDENTITY, delegate_ref="other"), RESOURCE),
        (IDENTITY, replace(RESOURCE, scope=replace(SCOPE, strategy_ref="other", run_id="other"))),
        (
            IDENTITY,
            replace(RESOURCE, scope=replace(SCOPE, account_ref="other", strategy_ref="other")),
        ),
        (IDENTITY, replace(RESOURCE, operations=frozenset({"calendar.windows.v1"}))),
        (IDENTITY, replace(RESOURCE, operations=frozenset({OP, "calendar.windows.v1"}))),
    ],
)
def test_registry_rejects_cross_role_or_scope_grants(identity, resource):
    with pytest.raises(ValueError):
        validate_capabilities_and_grants(
            (CAP,), {"monitor": (PrincipalGrant(identity, (resource,)),)}
        )
