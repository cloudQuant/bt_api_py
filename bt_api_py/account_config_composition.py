"""Discover provider-owned direct account-configuration read aliases."""

from __future__ import annotations

from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py.provider_entrypoints import ProviderEntryPointError, load_route_entrypoint


def account_config_read_aliases(exchange_name: str) -> tuple[str, ...]:
    """Return explicit route aliases; absent declarations use the standard method."""
    try:
        provider = load_route_entrypoint(
            "bt_api.account_config_read_aliases", exchange_name, optional=True
        )
    except ProviderEntryPointError:
        raise CapabilityNotSupportedError(
            "get_account_config",
            detail="provider account-configuration adapter unavailable",
            definite_reject=True,
        ) from None
    if provider is None:
        return ()
    aliases = provider()
    if type(aliases) is not tuple or any(type(name) is not str or not name for name in aliases):
        raise CapabilityNotSupportedError(
            "get_account_config", detail="provider account-configuration aliases invalid"
        )
    return aliases


__all__ = ("account_config_read_aliases",)
