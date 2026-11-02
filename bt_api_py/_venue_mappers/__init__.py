"""Discover provider-owned order projections by exact SDK route."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bt_api_base.order_request_projection import legacy_order_request_projection
from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._contracts.models import OrderRequest
from bt_api_py.provider_entrypoints import ProviderEntryPointError, load_route_entrypoint

OrderMapper = Callable[[OrderRequest], dict[str, Any]]
ENTRY_POINT_GROUP = "bt_api.order_request_projections"

# Explicit programmatic registrations remain available to custom SDK hosts.
MAPPERS: dict[str, OrderMapper] = {}


def get_venue_mapper(exchange_name: str) -> OrderMapper | None:
    """Select one provider's mapper or its explicitly registered legacy projection."""
    if exchange_name in MAPPERS:
        return MAPPERS[exchange_name]
    try:
        projector = load_route_entrypoint(ENTRY_POINT_GROUP, exchange_name)
        if projector is legacy_order_request_projection:
            return None
    except ProviderEntryPointError:
        raise CapabilityNotSupportedError(
            "make_order", detail="provider order request projection unavailable", definite_reject=True
        ) from None
    return projector


__all__ = ["MAPPERS", "get_venue_mapper"]
