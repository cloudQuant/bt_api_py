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


__all__ = ("normalization_policy",)
