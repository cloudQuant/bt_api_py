"""Retired Iteration 41 gateway-managed order writer API.

The former implementation assembled a second order writer beside the current
ServiceHost route.  New live order execution belongs to
``ServiceExecutionSession -> ServiceHost -> service.order_entry``.  This
module keeps only explicit migration errors for callers importing the old
public names; it has no capability imports, database setup, gateway router,
transport construction, or provider dispatch path.
"""

from __future__ import annotations

from typing import Any

from .catalog import RuntimePluginError

_RETIRED_MESSAGE = (
    "the runtime_plugins gateway-managed order writer is retired; migrate to "
    "Backtrader ServiceExecutionSession with ServiceHost and service.order_entry"
)


class GatewayManagedDispatchError(RuntimePluginError):
    """Stable error type retained for code that handled the retired API."""


class GatewayManagedOutcomeUnknownError(GatewayManagedDispatchError):
    """Historical exception name; the retired API cannot create new outcomes."""


GatewayManagedOutcomeUnknown = GatewayManagedOutcomeUnknownError


def _retired() -> None:
    raise GatewayManagedDispatchError("LEGACY_GATEWAY_RUNTIME_RETIRED", _RETIRED_MESSAGE)


class GatewayManagedDispatcher:
    """Retired client-side writer; construction fails before touching resources."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        del cls, args, kwargs
        _retired()


class GatewayManagedExecutionRuntime:
    """Retired Backtrader-bindable gateway writer runtime."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        del cls, args, kwargs
        _retired()


class GatewayExecutionAuthority:
    """Retired router-backed provider writer authority."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        del cls, args, kwargs
        _retired()


def compose_gateway_managed_client(*args: Any, **kwargs: Any) -> Any:
    """Reject the retired client writer before reading capabilities or paths."""

    del args, kwargs
    _retired()


def compose_gateway_execution_authority(*args: Any, **kwargs: Any) -> Any:
    """Reject the retired server writer before creating stores or a router."""

    del args, kwargs
    _retired()


def gateway_command_id(intent: Any) -> str:
    """Reject the command-ID helper for the retired managed-gateway protocol."""

    del intent
    _retired()


__all__ = [
    "GatewayExecutionAuthority",
    "GatewayManagedDispatchError",
    "GatewayManagedDispatcher",
    "GatewayManagedExecutionRuntime",
    "GatewayManagedOutcomeUnknown",
    "GatewayManagedOutcomeUnknownError",
    "compose_gateway_execution_authority",
    "compose_gateway_managed_client",
    "gateway_command_id",
]
