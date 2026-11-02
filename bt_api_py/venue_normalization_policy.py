"""Select one provider's declared legacy normalization policy by route."""

from bt_api_py._contracts.errors import NormalizedApiError
from bt_api_py.provider_entrypoints import ProviderEntryPointError, load_route_entrypoint

ENTRY_POINT_GROUP = "bt_api.normalization_policies"


def normalization_policy(exchange_name):
    try:
        factory = load_route_entrypoint(ENTRY_POINT_GROUP, exchange_name)
    except ProviderEntryPointError:
        raise NormalizedApiError(
            "provider_normalization",
            "PROVIDER_NORMALIZATION_POLICY_UNAVAILABLE",
            execution_unknown=True,
        ) from None
    return factory(exchange_name)


def native_quantity_unit(exchange_name):
    """Resolve a provider-declared unit without inferring it from a venue name."""
    policy = normalization_policy(exchange_name)
    unit = getattr(policy, "quantity_unit", None)
    if type(unit) is not str or unit not in {"base", "contracts", "lots"}:
        raise NormalizedApiError(
            "provider_normalization",
            "PROVIDER_QUANTITY_UNIT_UNAVAILABLE",
            execution_unknown=True,
        )
    return unit


__all__ = ("native_quantity_unit", "normalization_policy")
