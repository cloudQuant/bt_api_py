"""Resolve provider-declared validation for direct order quantity units."""

from __future__ import annotations

from typing import Any

from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py.provider_entrypoints import ProviderEntryPointError, load_route_entrypoint

ENTRY_POINT_GROUP = "bt_api.order_quantity_unit_validators"


def validate_order_quantity_units(exchange_name: str, request: Any) -> None:
    """Apply exactly one selected provider's existing quantity-unit policy."""
    try:
        validator = load_route_entrypoint(ENTRY_POINT_GROUP, exchange_name)
    except ProviderEntryPointError:
        raise CapabilityNotSupportedError(
            "make_order", detail="provider quantity-unit policy unavailable", definite_reject=True
        ) from None
    validator(exchange_name, request)


__all__ = ("validate_order_quantity_units",)
