"""bt_api_py - Unified Multi-Exchange Trading API Framework.

This package provides a unified API for interacting with multiple cryptocurrency exchanges
and traditional financial markets (CTP, Interactive Brokers).
"""

from __future__ import annotations

import os as _os

# 版本单一源：从 pyproject.toml 经 importlib.metadata 读取。
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

from bt_api_base._compat import UTC

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

from bt_api_py._contracts import (
    AccountSnapshot,
    AuthorizationError,
    BalanceSnapshot,
    CancelAllRequest,
    CancelOrderRequest,
    CapabilityNotSupportedError,
    CapabilityReport,
    CommandResultUnknownError,
    Consistency,
    DepthSnapshot,
    FeeSchedule,
    FillSnapshot,
    ForwardingConfig,
    Freshness,
    FundingSnapshot,
    InstrumentSpec,
    KlineSnapshot,
    LegacyOrderApiError,
    LiveQueryFailedError,
    NormalizedApiError,
    OrderRequest,
    OrderSnapshot,
    OrderType,
    PluginNotInstalledError,
    PositionModeUpdate,
    PositionSnapshot,
    ProtocolCorrelationError,
    QueryOrderRequest,
    Side,
    StaleDataUnavailableError,
    SubscribeRequest,
    SubscriptionHandle,
    TickerSnapshot,
    TradingReadiness,
    TransportMode,
)
from bt_api_py._execution_session import (
    migrate_execution_journal,
    migrate_legacy_execution_journal,
)
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
from bt_api_py.cross_venue import (
    CostBreakdown,
    CrossVenueLeg,
    CrossVenueValueError,
    ExecutableVWAP,
    InsufficientDepth,
    QuantityLattice,
    RealizedEconomics,
    aggregate_confirmed_fills,
    coerce_funding_snapshot,
    decimal_value,
    executable_vwap,
    funding_settlement_count,
    normalize_orderbook_evidence,
    quantity_lattice,
    realized_round_trip_economics,
    round_trip_cost,
    signed_funding_cashflow,
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
    "NormalizedApiError",
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
    # v1 BtApi contract types (data types only, not a second trading client)
    "Side",
    "OrderType",
    "PositionModeUpdate",
    "Consistency",
    "TransportMode",
    "ForwardingConfig",
    "OrderRequest",
    "CancelOrderRequest",
    "CancelAllRequest",
    "QueryOrderRequest",
    "SubscribeRequest",
    "SubscriptionHandle",
    "Freshness",
    "TickerSnapshot",
    "DepthSnapshot",
    "FeeSchedule",
    "FundingSnapshot",
    "InstrumentSpec",
    "KlineSnapshot",
    "AccountSnapshot",
    "BalanceSnapshot",
    "PositionSnapshot",
    "OrderSnapshot",
    "FillSnapshot",
    "TradingReadiness",
    "CapabilityNotSupportedError",
    "CapabilityReport",
    "PluginNotInstalledError",
    "LiveQueryFailedError",
    "StaleDataUnavailableError",
    "CommandResultUnknownError",
    "ProtocolCorrelationError",
    "AuthorizationError",
    "LegacyOrderApiError",
    # Provider-neutral cross-venue execution-planning primitives. They consume
    # the contracts above and do not create a second SDK client or strategy.
    "CrossVenueValueError",
    "InsufficientDepth",
    "CrossVenueLeg",
    "QuantityLattice",
    "ExecutableVWAP",
    "CostBreakdown",
    "RealizedEconomics",
    "decimal_value",
    "coerce_funding_snapshot",
    "normalize_orderbook_evidence",
    "quantity_lattice",
    "executable_vwap",
    "aggregate_confirmed_fills",
    "funding_settlement_count",
    "signed_funding_cashflow",
    "round_trip_cost",
    "realized_round_trip_economics",
    "migrate_execution_journal",
    "migrate_legacy_execution_journal",
]
