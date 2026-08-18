"""Private v1 BtApi contract package.

Dataclasses and enums here are the typed request/result surface for ``BtApi``.
They are data types only — not a second business client.
"""

from __future__ import annotations

from bt_api_py._contracts.errors import (
    AuthorizationError,
    BtApiContractError,
    CapabilityNotSupportedError,
    CommandResultUnknownError,
    LegacyOrderApiError,
    LiveQueryFailedError,
    PluginNotInstalledError,
    ProtocolCorrelationError,
    StaleDataUnavailableError,
)
from bt_api_py._contracts.models import (
    AccountSnapshot,
    BalanceSnapshot,
    CancelAllRequest,
    CancelOrderRequest,
    Consistency,
    DepthSnapshot,
    FillSnapshot,
    ForwardingConfig,
    Freshness,
    KlineSnapshot,
    OrderRequest,
    OrderSnapshot,
    OrderType,
    PositionSnapshot,
    QueryOrderRequest,
    Side,
    SubscribeRequest,
    TickerSnapshot,
    TransportMode,
)

__all__ = [
    "AccountSnapshot",
    "AuthorizationError",
    "BalanceSnapshot",
    "BtApiContractError",
    "CancelAllRequest",
    "CancelOrderRequest",
    "CapabilityNotSupportedError",
    "CommandResultUnknownError",
    "Consistency",
    "DepthSnapshot",
    "FillSnapshot",
    "ForwardingConfig",
    "Freshness",
    "KlineSnapshot",
    "LegacyOrderApiError",
    "LiveQueryFailedError",
    "OrderRequest",
    "OrderSnapshot",
    "OrderType",
    "PluginNotInstalledError",
    "PositionSnapshot",
    "ProtocolCorrelationError",
    "QueryOrderRequest",
    "Side",
    "StaleDataUnavailableError",
    "SubscribeRequest",
    "TickerSnapshot",
    "TransportMode",
]
