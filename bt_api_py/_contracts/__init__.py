"""Private v1 BtApi contract package.

Dataclasses and enums here are the typed request/result surface for ``BtApi``.
They are data types only — not a second business client.
"""

from __future__ import annotations

from bt_api_py._contracts.cache_policy import CacheEntry, CachePolicy
from bt_api_py._contracts.capabilities import SUPPORT_STATUSES, CapabilityReport
from bt_api_py._contracts.errors import (
    AuthorizationError,
    BtApiContractError,
    CapabilityNotSupportedError,
    CommandResultUnknownError,
    LegacyOrderApiError,
    LiveQueryFailedError,
    NormalizedApiError,
    PluginNotInstalledError,
    ProtocolCorrelationError,
    StaleDataUnavailableError,
)
from bt_api_py._contracts.models import (
    AccountSnapshot,
    BalanceSnapshot,
    CancelAllRequest,
    CancelOrderRequest,
    CommandStatus,
    Consistency,
    DepthSnapshot,
    FeeSchedule,
    FillSnapshot,
    ForwardingConfig,
    Freshness,
    FundingSnapshot,
    InstrumentSpec,
    KlineSnapshot,
    OrderRequest,
    OrderSnapshot,
    OrderType,
    PositionModeUpdate,
    PositionSnapshot,
    QueryOrderRequest,
    Side,
    SubscribeRequest,
    TickerSnapshot,
    TradingReadiness,
    TransportMode,
)
from bt_api_py._contracts.subscriptions import SubscriptionHandle

__all__ = [
    "AccountSnapshot",
    "AuthorizationError",
    "BalanceSnapshot",
    "BtApiContractError",
    "CacheEntry",
    "CachePolicy",
    "CancelAllRequest",
    "CancelOrderRequest",
    "CommandStatus",
    "CapabilityNotSupportedError",
    "CapabilityReport",
    "CommandResultUnknownError",
    "Consistency",
    "DepthSnapshot",
    "FeeSchedule",
    "FillSnapshot",
    "ForwardingConfig",
    "Freshness",
    "FundingSnapshot",
    "InstrumentSpec",
    "KlineSnapshot",
    "LegacyOrderApiError",
    "LiveQueryFailedError",
    "NormalizedApiError",
    "OrderRequest",
    "OrderSnapshot",
    "OrderType",
    "PluginNotInstalledError",
    "PositionModeUpdate",
    "PositionSnapshot",
    "ProtocolCorrelationError",
    "QueryOrderRequest",
    "Side",
    "StaleDataUnavailableError",
    "SUPPORT_STATUSES",
    "SubscribeRequest",
    "SubscriptionHandle",
    "TickerSnapshot",
    "TransportMode",
    "TradingReadiness",
]
