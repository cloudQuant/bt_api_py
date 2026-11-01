"""Discover provider-owned direct-order identity projections by exact route."""

from __future__ import annotations

from typing import Any

from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py.provider_entrypoints import ProviderEntryPointError, load_route_entrypoint

ENTRY_POINT_GROUP = "bt_api.order_identity_projections"


def project_order_identity(
    exchange_name: str, operation: str, request: Any, kwargs: dict[str, Any], default_order_id: Any
) -> Any:
    """Project one query/cancel using its selected provider; never guess a missing mapping."""
    if operation not in ("query", "cancel"):
        raise ValueError("unsupported order identity operation")
    try:
        projector = load_route_entrypoint(ENTRY_POINT_GROUP, exchange_name)
    except ProviderEntryPointError:
        raise CapabilityNotSupportedError(
            operation + "_order", detail="provider order identity projection unavailable", definite_reject=True
        ) from None
    return projector(operation, request, kwargs, default_order_id)


__all__ = ("project_order_identity",)
