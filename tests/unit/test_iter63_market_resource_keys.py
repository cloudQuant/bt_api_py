from types import SimpleNamespace

import pytest

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost, ServiceHostError


@pytest.mark.parametrize(
    "extras",
    [
        {},
        {"account_comparison": object()},
        {"account_observer": object(), "account_facts": object()},
        {"account_comparison": object(), "lease_drain": object(),
         "delegation_service": object(), "parent_service": object()},
    ],
)
def test_market_tracking_accepts_declared_optional_keys_before_adapter_validation(extras):
    host = object.__new__(ServiceHost)
    host._pending_resources = []
    item = SimpleNamespace(
        spec=SimpleNamespace(component="gateway", instance_id="gateway", options={"role": "trading_owner"}),
        operations_by_port={"service.market_source": frozenset(), "service.account_read": frozenset()},
    )
    resources = ComponentResources.borrowed(
        {"binding": object(), "hub": object(), "read_service": object(), **extras},
        ready=lambda: True,
    )
    # Tracking reaches the independent exact hub type check. The component
    # builder subsequently checks each actual adapter and authorization.
    with pytest.raises(ServiceHostError, match="MARKET_HUB_CONFIGURATION_INVALID"):
        host._track_resources(item, resources)


def test_market_tracking_rejects_unknown_resource_key():
    host = object.__new__(ServiceHost)
    host._pending_resources = []
    item = SimpleNamespace(
        spec=SimpleNamespace(component="gateway", instance_id="gateway", options={"role": "trading_owner"}),
        operations_by_port={"service.market_source": frozenset()},
    )
    resources = ComponentResources.borrowed(
        {"binding": object(), "hub": object(), "unknown": object()}, ready=lambda: True
    )
    with pytest.raises(ServiceHostError, match="MARKET_RESOURCE_KEYS_INVALID"):
        host._track_resources(item, resources)


def test_market_tracking_requires_account_port_for_account_resource():
    host = object.__new__(ServiceHost)
    host._pending_resources = []
    item = SimpleNamespace(
        spec=SimpleNamespace(component="gateway", instance_id="gateway", options={"role": "trading_owner"}),
        operations_by_port={"service.market_source": frozenset()},
    )
    resources = ComponentResources.borrowed(
        {"binding": object(), "hub": object(), "account_comparison": object()}, ready=lambda: True
    )
    with pytest.raises(ServiceHostError, match="MARKET_RESOURCE_KEYS_INVALID"):
        host._track_resources(item, resources)
