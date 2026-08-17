"""Module-level docstring."""
from datetime import datetime

from bt_api_base.exceptions import (
    AuthenticationError,
    BtApiError,
    ConfigurationError,
    CurrencyNotFoundError,
    DataParseError,
    ExchangeConnectionAlias,
    ExchangeConnectionError,
    ExchangeNotFoundError,
    InsufficientBalanceError,
    InvalidOrderError,
    OrderError,
    OrderNotFoundError,
    QueueNotInitializedError,
    RateLimitError,
    RequestError,
    RequestFailedError,
    RequestTimeoutError,
    SubscribeError,
    WebSocketError,
)


class PartialDownloadError(Exception):
    """Raised when historical kline download is incomplete after exhausting retries.

    Carries the list of successfully downloaded intervals and the failure reason.
    """

    def __init__(
        self,
        message: str,
        *,
        downloaded_intervals: list[tuple[datetime, datetime]] | None = None,
    ) -> None:
        super().__init__(message)
        self.downloaded_intervals = downloaded_intervals or []


__all__ = [
    "BtApiError",
    "ExchangeNotFoundError",
    "ExchangeConnectionError",
    "ExchangeConnectionAlias",
    "AuthenticationError",
    "RequestTimeoutError",
    "RequestError",
    "RequestFailedError",
    "OrderError",
    "SubscribeError",
    "DataParseError",
    "RateLimitError",
    "InvalidSymbolError",
    "InsufficientBalanceError",
    "InvalidOrderError",
    "OrderNotFoundError",
    "ConfigurationError",
    "WebSocketError",
    "CurrencyNotFoundError",
    "QueueNotInitializedError",
    "PartialDownloadError",
]