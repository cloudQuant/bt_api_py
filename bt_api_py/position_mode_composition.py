"""Discover provider-owned direct position-mode adapters."""

from __future__ import annotations

from bt_api_py._contracts.errors import CapabilityNotSupportedError, NormalizedApiError
from bt_api_py.provider_entrypoints import ProviderEntryPointError, load_route_entrypoint


def _entry(group: str, exchange_name: str, *, operation: str, optional: bool):
    try:
        return load_route_entrypoint(group, exchange_name, optional=optional)
    except ProviderEntryPointError:
        raise CapabilityNotSupportedError(
            operation, detail="provider position-mode adapter unavailable", definite_reject=True
        ) from None


def position_mode_read_aliases(exchange_name: str) -> tuple[str, ...]:
    """Return only explicit provider aliases; other routes use the standard method."""
    provider = _entry("bt_api.position_mode_read_aliases", exchange_name, operation="get_position_mode", optional=True)
    if provider is None:
        return ()
    aliases = provider()
    if type(aliases) is not tuple or any(type(name) is not str or not name for name in aliases):
        raise CapabilityNotSupportedError("get_position_mode", detail="provider aliases invalid")
    return aliases


def position_mode_guard_required(exchange_name: str) -> bool:
    """Select the provider's explicit placement fence policy for this route."""
    from bt_api_py.venue_normalization_policy import normalization_policy

    try:
        policy = normalization_policy(exchange_name)
        required = getattr(policy, "position_mode_placement_guard_required", None)
    except Exception:
        raise NormalizedApiError(
            "make_order", "position_mode_guard_policy_unavailable", definite_reject=True
        ) from None
    if type(required) is not bool:
        raise NormalizedApiError(
            "make_order", "position_mode_guard_policy_unavailable", definite_reject=True
        )
    return required


def resolve_position_mode_writer(exchange_name: str):
    """Require an explicit provider mapping for the account-wide mutation."""
    return _entry("bt_api.position_mode_writers", exchange_name, operation="set_position_mode", optional=False)


__all__ = ("position_mode_guard_required", "position_mode_read_aliases", "resolve_position_mode_writer")
