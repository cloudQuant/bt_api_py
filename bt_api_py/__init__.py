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

from bt_api_py.backtrader import BtApiBroker
from bt_api_py.brokers import (
    available_adapters,
    list_registered_adapters,
    load_adapter,
    register_adapter,
)
from bt_api_py.bt_api import BtApi
from bt_api_py.certification import (
    CertificationAuditEvent,
    CertificationScenarioRegistry,
    default_certification_scenario_registry,
)
from bt_api_py.forwarding import (
    MAX_MESSAGE_BYTES,
    BtApiForwardingAdapter,
    ForwardingClient,
    ForwardingRuntime,
    InMemoryForwardingBus,
    MarketDataHub,
    MarketEvent,
    OrderCommand,
    OrderRouter,
    PrivateEvent,
    SQLiteStateStore,
    ZmqForwardingClient,
    ZmqForwardingRuntime,
)
from bt_api_py.gateway import GatewayClient

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
    "CertificationAuditEvent",
    "CertificationScenarioRegistry",
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
    "default_certification_scenario_registry",
    "QueueNotInitializedError",
    "ExchangeRegistry",
    "get_logger",
    "InstrumentManager",
    "get_instrument_manager",
    "available_adapters",
    "BtApiBroker",
    "ErrorCategory",
    "UnifiedErrorCode",
    "UnifiedError",
    "UnifiedRateLimitError",
    "UnifiedAuthError",
    "ServerError",
    "UnifiedRequestFailedError",
    "ErrorTranslator",
    "OKXErrorTranslator",
    "list_registered_adapters",
    "load_adapter",
    "register_adapter",
    "ForwardingClient",
    "GatewayClient",
    "ForwardingRuntime",
    "BtApiForwardingAdapter",
    "InMemoryForwardingBus",
    "MAX_MESSAGE_BYTES",
    "MarketDataHub",
    "MarketEvent",
    "OrderCommand",
    "OrderRouter",
    "PrivateEvent",
    "SQLiteStateStore",
    "ZmqForwardingClient",
    "ZmqForwardingRuntime",
]
