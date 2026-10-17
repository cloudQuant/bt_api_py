"""Keep policy-catalog grants within the declared component/runtime contract."""

from __future__ import annotations

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.service_components import ComponentSpec, validate_components
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    validate_capabilities_and_grants,
)
from bt_api_py.service_runtime import PORT_OPERATIONS

_INSTANCE = "policy-catalog"
_PORT = "service.risk_policy"
_READ_OPERATIONS = frozenset(
    {"policy.inspect", "policy.confirmation.read", "policy.receipt"}
)


def test_policy_catalog_read_grants_are_declared_and_runtime_supported() -> None:
    (prepared,) = validate_components(
        (
            ComponentSpec(
                "risk",
                _INSTANCE,
                (_PORT,),
                {"role": "policy_catalog"},
            ),
        )
    )
    operations = prepared.operations_by_port[_PORT]
    write_operations = prepared.write_operations_by_port[_PORT]

    assert _READ_OPERATIONS <= operations
    assert operations <= PORT_OPERATIONS[_PORT]
    assert _READ_OPERATIONS.isdisjoint(write_operations)

    capability = ServiceCapability(
        _INSTANCE,
        _PORT,
        operations,
        write_operations=write_operations,
    )
    scope = WireScope(
        "paper:policy-catalog-contract",
        account_ref="account-a",
        portfolio_ref="portfolio-a",
    )
    maker_operations = frozenset(
        {
            "policy.draft",
            "policy.publish",
            "policy.inspect",
            "policy.receipt",
            "policy.confirmation.read",
        }
    )
    checker_operations = frozenset(
        {
            "policy.approve",
            "policy.inspect",
            "policy.receipt",
            "policy.confirmation.read",
        }
    )
    grants = {
        "policy-maker": (
            PrincipalGrant(
                WireIdentity(WireRole.APPLICATION_CLIENT, "policy-maker", "maker"),
                (
                    ResourceGrant(
                        _INSTANCE,
                        _PORT,
                        "account-a-policy",
                        scope,
                        maker_operations,
                    ),
                ),
            ),
        ),
        "policy-checker": (
            PrincipalGrant(
                WireIdentity(WireRole.APPLICATION_CLIENT, "policy-checker", "checker"),
                (
                    ResourceGrant(
                        _INSTANCE,
                        _PORT,
                        "account-a-policy",
                        scope,
                        checker_operations,
                    ),
                ),
            ),
        ),
    }
    validate_capabilities_and_grants((capability,), grants)
