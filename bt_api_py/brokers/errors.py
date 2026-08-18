"""Module-level docstring."""

from __future__ import annotations

from enum import StrEnum


class BrokerErrorCode(StrEnum):
    """Class BrokerErrorCode"""

    ADAPTER_NOT_INSTALLED = "adapter_not_installed"
    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    NOT_SUPPORTED = "not_supported"
    INVALID_ORDER = "invalid_order"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    ORDER_NOT_FOUND = "order_not_found"


class BrokerError(Exception):
    """Class BrokerError"""

    def __init__(
        self,
        code: BrokerErrorCode,
        message: str,
        *,
        retryable: bool = False,
        cause: Exception | None = None,
    ) -> None:
        """__init__ method"""
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.cause = cause

    def to_dict(self) -> dict[str, object]:
        """to_dict method"""
        return {"code": self.code.value, "message": self.message, "retryable": self.retryable}
