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


def readiness_normalizers(exchange_name: str):
    """Expose the existing neutral value conversion functions to a provider."""
    return {"rows": rows, "pick": pick,
            "trading_permissions": lambda row: _trading_permissions(row, exchange_name),
            "instrument_spec": instrument_spec, "number": number}


def read_sdk_trading_readiness(
    api, exchange_name, symbol, quantity_native, *, margin_mode, position_mode,
    resolve_account_id, contract_mapping, error_type,
):
    """Read one routed SDK readiness result without creating authorization."""
    if callable(getattr(api, "get_trading_readiness", None)):
        contract = api.get_trading_readiness(
            exchange_name, symbol, resolve_account_id(), quantity_native,
            margin_mode=margin_mode, position_mode=position_mode,
        )
        snapshot = contract_mapping(contract, "TradingReadiness")
        snapshot["ready"] = bool(getattr(contract, "ready", snapshot.get("ready", False)))
        reasons = snapshot.get("blocked_reasons", snapshot.get("reasons", ()))
        snapshot["reasons"] = list(reasons or ())
        if type(snapshot.get("definite_failure")) is not bool:
            raise error_type("SDK readiness failure classification is unavailable")
        return snapshot
    snapshot = contract_mapping(
        api.get_order_readiness(
            exchange_name, symbol, quantity_native, margin_mode=margin_mode,
            position_mode=position_mode, normalized=True,
        ),
        "order readiness",
    )
    snapshot["reasons"] = list(snapshot.get("reasons") or ())
    return snapshot


__all__ = ("resolve_readiness_provider", "readiness_normalizers", "read_sdk_trading_readiness")
