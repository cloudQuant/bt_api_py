"""Typed errors for the v1 BtApi contract."""

from __future__ import annotations

from bt_api_base.exceptions import BtApiError


class BtApiContractError(BtApiError):
    """Base error for the v1 BtApi typed contract."""


class CapabilityNotSupportedError(BtApiContractError):
    """Raised when an operation is not supported by the selected venue."""

    def __init__(self, operation: str, *, detail: str = "") -> None:
        message = f"capability not supported: {operation}"
        if detail:
            message = f"{message} — {detail}"
        super().__init__(message)
        self.operation = operation
        self.detail = detail


class PluginNotInstalledError(BtApiContractError):
    """Raised when a required exchange plugin is not installed/loadable."""

    def __init__(self, exchange_name: str, *, detail: str = "") -> None:
        message = f"plugin not installed: {exchange_name}"
        if detail:
            message = f"{message} — {detail}"
        super().__init__(message)
        self.exchange_name = exchange_name


class LiveQueryFailedError(BtApiContractError):
    """Raised when a LIVE consistency query fails (no cache fallback allowed)."""

    def __init__(self, operation: str, exchange_name: str, *, detail: str = "") -> None:
        message = f"live query failed: {operation} on {exchange_name}"
        if detail:
            message = f"{message} — {detail}"
        super().__init__(message)
        self.operation = operation
        self.exchange_name = exchange_name


class StaleDataUnavailableError(BtApiContractError):
    """Raised when a CACHE_OK query has no cached/stale data to return."""

    def __init__(self, operation: str, exchange_name: str, *, detail: str = "") -> None:
        message = f"stale data unavailable: {operation} on {exchange_name}"
        if detail:
            message = f"{message} — {detail}"
        super().__init__(message)
        self.operation = operation
        self.exchange_name = exchange_name


class CommandResultUnknownError(BtApiContractError, TimeoutError):
    """Raised when a command result is unknown (e.g. transport timeout).

    The caller must reconcile via ``get_command_status``, never blind-retry.
    """

    def __init__(self, command_id: str, idempotency_key: str, *, detail: str = "") -> None:
        message = (
            f"command result unknown: command_id={command_id} idempotency_key={idempotency_key}"
        )
        if detail:
            message = f"{message} — {detail}"
        super().__init__(message)
        self.command_id = command_id
        self.idempotency_key = idempotency_key


class ProtocolCorrelationError(BtApiContractError):
    """Raised when a response does not correlate to the issued command."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"protocol correlation failure: {detail}")
        self.detail = detail


class AuthorizationError(BtApiContractError):
    """Raised when a principal is not authorized for the requested operation."""

    def __init__(self, operation: str, *, detail: str = "") -> None:
        message = f"authorization denied: {operation}"
        if detail:
            message = f"{message} — {detail}"
        super().__init__(message)
        self.operation = operation


class LegacyOrderApiError(BtApiContractError):
    """Raised when legacy positional order args cannot resolve to a side."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"legacy positional order args must resolve to an explicit side; {detail}")
        self.detail = detail


__all__ = [
    "AuthorizationError",
    "BtApiContractError",
    "CapabilityNotSupportedError",
    "CommandResultUnknownError",
    "LegacyOrderApiError",
    "LiveQueryFailedError",
    "PluginNotInstalledError",
    "ProtocolCorrelationError",
    "StaleDataUnavailableError",
]
