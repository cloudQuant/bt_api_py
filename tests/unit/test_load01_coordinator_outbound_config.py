"""Pure configuration regression for N5 coordinator-to-owner service routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import bt_api_py.service_config as service_config_module
import bt_api_py.service_deployment as service_deployment_module
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_deployment import validate_deployment
from tests.unit import _load01_group_coordinator as coordinator
from tests.unit import test_load01_hundred_strategy_30min as runner

_SOURCE_ROOT = Path(runner.__file__).resolve().parents[2]


def _owner_service(account_index: int, endpoint_port: int) -> dict[str, Any]:
    endpoint = f"tcp://127.0.0.1:{endpoint_port}"
    service = runner._execution_service_info(
        account_index, endpoint, bytes([account_index + 1]) * 40
    )
    service_config = runner._execution_service_config(service, group_owner=True)
    parsed = parse_service_config(service_config)
    public = parsed.to_public_dict()
    reservation_targets = tuple(
        component["settings"]["instance_id"]
        for component in public["components"].values()
        if "service.group_reservations" in component["settings"]["bind_ports"]
    )
    assert len(reservation_targets) == 1
    route = {
        "account_ref": service["account_ref"],
        "order_scope_ref": service["order_scope_ref"],
        "query_scope_ref": service["query_scope_ref"],
        "reservation_scope_ref": f"load01-n5-reservation-scope-a{account_index:02d}-v1",
        "reservation_instance_id": reservation_targets[0],
    }
    return {
        **service,
        "service_config": service_config,
        "n5_group_route": route,
    }


@pytest.mark.parametrize("target_count", (1, 10))
def test_coordinator_outbound_config_validates_for_one_and_ten_owners(
    target_count: int,
) -> None:
    """Use the committed parser and deployment validator, without starting Hosts."""

    owner_services = {
        account_index: _owner_service(account_index, 41_001 + account_index)
        for account_index in range(target_count)
    }
    coordinator_endpoint = f"tcp://127.0.0.1:{41_100 + target_count}"
    coordinator_info = coordinator.n5_coordinator_service_info(
        coordinator_endpoint, b"c" * 40, target_count=target_count
    )
    raw = coordinator.build_n5_coordinator_service_config(
        endpoint=coordinator_endpoint,
        server_security_ref=coordinator_info["security_ref"],
        owner_services=owner_services,
        target_count=target_count,
    )

    coordinator_config = parse_service_config(raw)
    owner_configs = {
        index: parse_service_config(owner["service_config"])
        for index, owner in owner_services.items()
    }
    directory = {coordinator_config.service_id: coordinator_config}
    directory.update({config.service_id: config for config in owner_configs.values()})
    plan = validate_deployment(directory)

    coordinator_bindings = tuple(
        binding
        for client in raw["clients"].values()
        for binding in client["settings"]["bindings"]
    )
    assert len(coordinator_bindings) == target_count * 3
    for account_index, owner in owner_services.items():
        route = owner["n5_group_route"]
        scoped = tuple(
            binding
            for binding in coordinator_bindings
            if binding["account_ref"] == owner["account_ref"]
        )
        assert {
            binding["mount"]: binding["instance_id"] for binding in scoped
        } == {
            "upstream.orders": owner["instance_id"],
            "upstream.order_query": owner["instance_id"],
            "upstream.account_reservations": route["reservation_instance_id"],
        }
    assert len(plan.client_binding_requests) == target_count * 3
    assert Path(runner.__file__).resolve().is_relative_to(_SOURCE_ROOT)
    assert Path(service_config_module.__file__).resolve().is_relative_to(_SOURCE_ROOT)
    assert Path(service_deployment_module.__file__).resolve().is_relative_to(_SOURCE_ROOT)


def test_coordinator_requires_reservation_component_instance() -> None:
    owner = _owner_service(0, 41_001)
    del owner["n5_group_route"]["reservation_instance_id"]
    info = coordinator.n5_coordinator_service_info(
        "tcp://127.0.0.1:41_200", b"c" * 40, target_count=1
    )
    with pytest.raises(coordinator.Load01N5CoordinatorError, match="COORDINATOR_OWNER_ROUTE_INVALID"):
        coordinator.build_n5_coordinator_service_config(
            endpoint="tcp://127.0.0.1:41_200",
            server_security_ref=info["security_ref"],
            owner_services={0: owner},
            target_count=1,
        )
