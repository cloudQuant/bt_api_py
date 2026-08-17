"""bt_api_py - Unified Multi-Exchange Trading API Framework.

This package provides a unified API for interacting with multiple cryptocurrency exchanges
and traditional financial markets (CTP, Interactive Brokers).
"""

from __future__ import annotations

import os as _os

from bt_api_base._compat import UTC

# 版本单一源：从 pyproject.toml 经 importlib.metadata 读取。
from importlib.metadata import PackageNotFoundError, version as _package_version

try:
    __version__ = _package_version("bt_api_py")
except PackageNotFoundError:  # 源码树直接 import(未安装)时的兜底
    __version__ = "0.0.0.dev0"

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

from bt_api_py.brokers import (
    available_adapters,
    list_registered_adapters,
    load_adapter,
    register_adapter,
)
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

_LIGHT_IMPORT = str(_os.getenv("BT_API_PY_LIGHT_IMPORT") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

if not _LIGHT_IMPORT:
    from bt_api_py.bt_api import BtApi


def __getattr__(name: str):
    if name == "BtApi":
        from bt_api_py.bt_api import BtApi as _BtApi

        globals()["BtApi"] = _BtApi
        return _BtApi
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
