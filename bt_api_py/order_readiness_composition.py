"""Provider discovery for neutral, read-only order-readiness workflows."""

from __future__ import annotations

from importlib import metadata

from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._normalization import _trading_permissions, instrument_spec, number, pick, rows

ENTRY_POINT_GROUP = "bt_api.order_readiness_providers"


def resolve_readiness_provider(exchange_name: str):
    """Select exactly one provider; missing or ambiguous declarations reject."""
    try:
        discovered = metadata.entry_points()
        if hasattr(discovered, "select"):
            entries = tuple(discovered.select(group=ENTRY_POINT_GROUP, name=exchange_name))
        elif isinstance(discovered, dict):
            entries = tuple(e for e in discovered.get(ENTRY_POINT_GROUP, ()) if e.name == exchange_name)
        else:
            entries = tuple(e for e in discovered if e.group == ENTRY_POINT_GROUP and e.name == exchange_name)
        if len(entries) != 1:
            raise ValueError("readiness provider is absent or ambiguous")
        provider = entries[0].load()
        if not callable(provider):
            raise TypeError("readiness provider is not callable")
    except Exception:
        raise CapabilityNotSupportedError(
            "get_order_readiness", detail="provider readiness projection unavailable"
        ) from None
    return provider


def readiness_normalizers():
    """Expose the existing neutral value conversion functions to a provider."""
    return {"rows": rows, "pick": pick, "trading_permissions": _trading_permissions,
            "instrument_spec": instrument_spec, "number": number}


__all__ = ("resolve_readiness_provider", "readiness_normalizers")
