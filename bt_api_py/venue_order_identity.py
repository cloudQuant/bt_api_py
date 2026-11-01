"""Discover provider-owned direct-order identity projections by exact route."""

from __future__ import annotations

from importlib import metadata
from typing import Any

from bt_api_py._contracts.errors import CapabilityNotSupportedError

ENTRY_POINT_GROUP = "bt_api.order_identity_projections"


def project_order_identity(
    exchange_name: str, operation: str, request: Any, kwargs: dict[str, Any], default_order_id: Any
) -> Any:
    """Project one query/cancel using its selected provider; never guess a missing mapping."""
    if operation not in ("query", "cancel"):
        raise ValueError("unsupported order identity operation")
    try:
        discovered = metadata.entry_points()
        if hasattr(discovered, "select"):
            entries = tuple(discovered.select(group=ENTRY_POINT_GROUP, name=exchange_name))
        elif isinstance(discovered, dict):
            entries = tuple(e for e in discovered.get(ENTRY_POINT_GROUP, ()) if e.name == exchange_name)
        else:
            entries = tuple(e for e in discovered if e.group == ENTRY_POINT_GROUP and e.name == exchange_name)
        if len(entries) != 1:
            raise ValueError("order identity provider is absent or ambiguous")
        projector = entries[0].load()
        if not callable(projector):
            raise TypeError("order identity provider is not callable")
    except Exception:
        raise CapabilityNotSupportedError(
            operation + "_order", detail="provider order identity projection unavailable", definite_reject=True
        ) from None
    return projector(operation, request, kwargs, default_order_id)


__all__ = ("project_order_identity",)
