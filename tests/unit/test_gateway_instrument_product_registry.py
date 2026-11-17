"""Product registration is independent and always requires the three read operations."""

from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_instrument_product_codec import BEGIN, OPERATIONS
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
    validate_capabilities_and_grants,
)
from tests.unit.test_service_registry import _business, _register

IDENTITY = WireIdentity(WireRole.SERVICE, "monitor", "reader")
SCOPE = WireScope("domain", account_ref="account")
PORT = "service.instrument_product_read"
CAPABILITY = ServiceCapability("instance", PORT, OPERATIONS)
RESOURCE = ResourceGrant("instance", PORT, "scope", SCOPE, OPERATIONS)


@pytest.mark.parametrize(
    "identity,resource",
    [
        (WireIdentity(WireRole.STRATEGY_CLIENT, "monitor", "reader"), RESOURCE),
        (replace(IDENTITY, delegate_ref="other"), RESOURCE),
        (IDENTITY, replace(RESOURCE, operations=frozenset({BEGIN}))),
        (IDENTITY, replace(RESOURCE, scope=replace(SCOPE, strategy_ref="extra"))),
    ],
)
def test_registry_refuses_partial_or_cross_role_product_grant(identity, resource):
    with pytest.raises(ValueError):
        validate_capabilities_and_grants(
            (CAPABILITY,), {"monitor": (PrincipalGrant(identity, (resource,)),)}
        )


def test_registry_rechecks_scope_and_requires_complete_operation_set(tmp_path):
    registry = ServiceRegistry(
        tmp_path / "registry.sqlite",
        service_id="service",
        capabilities=(CAPABILITY,),
        grants={"monitor": (PrincipalGrant(IDENTITY, (RESOURCE,)),)},
        policy_version="policy",
        requirements_version="requirements",
    )
    registry.register_handler("instance", BEGIN, lambda ctx, args: {"ok": True})
    try:
        for scope, ops in (
            (replace(SCOPE, account_ref="other"), tuple(OPERATIONS)),
            (SCOPE, (BEGIN,)),
        ):
            assert not _register(
                registry,
                identity=IDENTITY,
                scope=scope,
                principal="monitor",
                operations=ops,
                instance_id="instance",
                port=PORT,
                scope_ref="scope",
            )["ok"]
        receipt = _register(
            registry,
            identity=IDENTITY,
            scope=SCOPE,
            principal="monitor",
            operations=tuple(OPERATIONS),
            instance_id="instance",
            port=PORT,
            scope_ref="scope",
        )["result"]
        assert _business(
            registry,
            receipt,
            identity=IDENTITY,
            scope=SCOPE,
            principal="monitor",
            operation=BEGIN,
            instance_id="instance",
        )["ok"]
        assert (
            _business(
                registry,
                receipt,
                identity=IDENTITY,
                scope=replace(SCOPE, account_ref="other"),
                principal="monitor",
                operation=BEGIN,
                instance_id="instance",
            )["code"]
            == "SCOPE_DENIED"
        )
    finally:
        registry.close()
