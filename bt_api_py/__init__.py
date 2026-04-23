"""bt_api_py - Unified Multi-Exchange Trading API Framework.

This package provides a unified API for interacting with multiple cryptocurrency exchanges
and traditional financial markets (CTP, Interactive Brokers).
"""

from __future__ import annotations

from bt_api_base._compat import UTC

# Re-export from bt_api_base for backward compatibility
from bt_api_base._version import __version__
from bt_api_base.auth_config import (
    AuthConfig,
    CryptoAuthConfig,
    CtpAuthConfig,
    IbAuthConfig,
    IbWebAuthConfig,
)
from bt_api_base.balance_utils import nested_balance_handler, simple_balance_handler
from bt_api_base.error import (
    ErrorCategory,
    ErrorTranslator,
    OKXErrorTranslator,
    ServerError,
    UnifiedAuthError,
    UnifiedError,
    UnifiedErrorCode,
    UnifiedRateLimitError,
    UnifiedRequestFailedError,
)
from bt_api_base.event_bus import EventBus
from bt_api_base.exceptions import (
    AuthenticationError,
    BtApiError,
    ConfigurationError,
    CurrencyNotFoundError,
    DataParseError,
    ExchangeConnectionError,
    ExchangeNotFoundError,
    InsufficientBalanceError,
    InvalidOrderError,
    InvalidSymbolError,
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
from bt_api_base.instrument_manager import InstrumentManager, get_instrument_manager
from bt_api_base.logging_factory import _LoggerProxy, get_logger
from bt_api_base.registry import ExchangeRegistry

from bt_api_py.bt_api import BtApi

__all__ = [
    "__version__",
    "UTC",
    "AuthConfig",
    "CryptoAuthConfig",
    "CtpAuthConfig",
    "IbAuthConfig",
    "IbWebAuthConfig",
    "nested_balance_handler",
    "simple_balance_handler",
    "BtApi",
    "BtApiError",
    "EventBus",
    "ExchangeNotFoundError",
    "ExchangeConnectionError",
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
    "ExchangeRegistry",
    "get_logger",
    "InstrumentManager",
    "get_instrument_manager",
    "ErrorCategory",
    "UnifiedErrorCode",
    "UnifiedError",
    "UnifiedRateLimitError",
    "UnifiedAuthError",
    "ServerError",
    "UnifiedRequestFailedError",
    "ErrorTranslator",
    "OKXErrorTranslator",
]
