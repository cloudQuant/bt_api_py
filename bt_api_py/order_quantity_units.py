"""Resolve provider-declared validation for direct order quantity units."""

from __future__ import annotations

from importlib import metadata
from typing import Any

from bt_api_py._contracts.errors import CapabilityNotSupportedError

ENTRY_POINT_GROUP = "bt_api.order_quantity_unit_validators"


def validate_order_quantity_units(exchange_name: str, request: Any) -> None:
    """Apply exactly one selected provider's existing quantity-unit policy."""
    try:
        discovered = metadata.entry_points()
        if hasattr(discovered, "select"):
            entries = tuple(discovered.select(group=ENTRY_POINT_GROUP, name=exchange_name))
        elif isinstance(discovered, dict):
            entries = tuple(e for e in discovered.get(ENTRY_POINT_GROUP, ()) if e.name == exchange_name)
        else:
            entries = tuple(e for e in discovered if e.group == ENTRY_POINT_GROUP and e.name == exchange_name)
        if len(entries) != 1:
            raise ValueError("quantity-unit provider is absent or ambiguous")
        validator = entries[0].load()
        if not callable(validator):
            raise TypeError("quantity-unit provider is not callable")
    except Exception:
        raise CapabilityNotSupportedError(
            "make_order", detail="provider quantity-unit policy unavailable", definite_reject=True
        ) from None
    validator(exchange_name, request)


__all__ = ("validate_order_quantity_units",)
