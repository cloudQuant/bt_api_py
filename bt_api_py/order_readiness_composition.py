"""Provider discovery for neutral, read-only order-readiness workflows."""

from __future__ import annotations

from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._normalization import _trading_permissions, instrument_spec, number, pick, rows
from bt_api_py.provider_entrypoints import ProviderEntryPointError, load_route_entrypoint

ENTRY_POINT_GROUP = "bt_api.order_readiness_providers"


def resolve_readiness_provider(exchange_name: str):
    """Select exactly one provider; missing or ambiguous declarations reject."""
    try:
        provider = load_route_entrypoint(ENTRY_POINT_GROUP, exchange_name)
    except ProviderEntryPointError:
        raise CapabilityNotSupportedError(
            "get_order_readiness", detail="provider readiness projection unavailable"
        ) from None
    return provider


def readiness_normalizers():
    """Expose the existing neutral value conversion functions to a provider."""
    return {"rows": rows, "pick": pick, "trading_permissions": _trading_permissions,
            "instrument_spec": instrument_spec, "number": number}


__all__ = ("resolve_readiness_provider", "readiness_normalizers")
