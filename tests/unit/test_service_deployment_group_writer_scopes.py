"""Deployment validation for one account writer with strategy-scoped routes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from bt_api_py.service_config import (
    ServiceConfig,
    ServiceConfigError,
    parse_client_config,
    parse_service_config,
)
from bt_api_py.service_deployment import validate_deployment
from tests.integration.test_arb02_group_budget_service import (
    _ACCOUNTS,
    _COORDINATOR_SERVICE_ID,
    _account_service_config,
    _coordinator_service_config,
)


def _three_service_configs(
    coordinator_public: dict[str, Any] | None = None,
) -> dict[str, ServiceConfig]:
    account_endpoints = {
        "account-a": "tcp://127.0.0.1:7311",
        "account-b": "tcp://127.0.0.1:7312",
    }
    account_public = {
        account_ref: _account_service_config(
            account_ref,
            account_endpoints[account_ref],
            f"arb02-account-server-{account_ref}",
        )
        for account_ref in _ACCOUNTS
    }
    coordinator = coordinator_public or _coordinator_service_config(
        "tcp://127.0.0.1:7313",
        account_public,
        account_endpoints,
    )
    raw_services = (*account_public.values(), coordinator)
    parsed = tuple(parse_service_config(raw) for raw in raw_services)
    return {service.service_id: service for service in parsed}


def test_arb02_three_service_builder_allows_one_writer_across_strategy_scopes() -> None:
    directory = _three_service_configs()

    plan = validate_deployment(directory)

    order_routes = tuple(
        request for request in plan.client_binding_requests if request.mount == "upstream.orders"
    )
    assert len(order_routes) == len(_ACCOUNTS) * 2
    assert {request.requester_service_id for request in order_routes} == {
        _COORDINATOR_SERVICE_ID
    }
    assert {request.client_alias for request in order_routes} == {"execution"}
    for account_ref in _ACCOUNTS:
        routes = tuple(request for request in order_routes if request.account_ref == account_ref)
        assert len(routes) == 2
        assert len({request.scope_ref for request in routes}) == 2
        assert len({(request.service_id, request.instance_id) for request in routes}) == 1


def test_arb02_config_parser_rejects_an_exact_duplicate_scope_route() -> None:
    coordinator = _coordinator_service_config(
        "tcp://127.0.0.1:7313",
        {
            account_ref: _account_service_config(
                account_ref,
                f"tcp://127.0.0.1:{7311 if account_ref == 'account-a' else 7312}",
                f"arb02-account-server-{account_ref}",
            )
            for account_ref in _ACCOUNTS
        },
        {"account-a": "tcp://127.0.0.1:7311", "account-b": "tcp://127.0.0.1:7312"},
    )
    first_order_binding = next(
        binding
        for binding in coordinator["clients"]["execution"]["settings"]["bindings"]
        if binding["mount"] == "upstream.orders" and binding["account_ref"] == "account-a"
    )
    coordinator["clients"]["execution"]["settings"]["bindings"].append(
        deepcopy(first_order_binding)
    )

    with pytest.raises(ServiceConfigError) as exc_info:
        parse_service_config(coordinator)

    assert exc_info.value.code == "DUPLICATE_BINDING"


def test_arb02_deployment_rejects_a_second_client_writer_for_the_same_account() -> None:
    directory = _three_service_configs()
    account_a = directory["group-owner-service-account-a"]
    second_client = parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": "second-strategy-client"},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "broker.orders",
                                "service_id": account_a.service_id,
                                "instance_id": account_a.components["execution"]["settings"][
                                    "instance_id"
                                ],
                                "scope_ref": "second-client-order-scope",
                                "account_ref": "account-a",
                                "transport_ref": "target",
                            }
                        ]
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "target": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7311"},
                        "security_profile_ref": "second-client-security",
                    }
                },
            },
        }
    )

    with pytest.raises(ServiceConfigError) as exc_info:
        validate_deployment(directory, (second_client,))

    assert exc_info.value.code == "WRITER_CONFLICT"
