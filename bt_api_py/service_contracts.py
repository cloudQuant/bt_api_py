"""Public neutral contracts used by Backtrader's service integrations.

The exported names are the original ``bt_api_base`` objects. This module
provides the stable ``bt_api_py`` import surface without wrapping, copying, or
changing their validation and type identity.
"""

from bt_api_base.account_protocol import (
    AccountKey,
    AccountObservation,
    AccountOpenOrder,
    AccountPosition,
    AccountResourceKey,
)
from bt_api_base.market_history_protocol import (
    MarketBar,
    MarketHistoryPage,
    MarketHistoryRequest,
)
from bt_api_base.market_protocol import (
    MARKET_EVENT_OPERATION,
    MARKET_EVENT_SCHEMA,
    MARKET_EVENT_SCHEMA_VERSION,
    MarketInstrumentKey,
    MarketProviderEvent,
    MarketSourceDescriptor,
    MarketSubscriptionKey,
)
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
    validate_event,
)

__all__ = [
    "AccountKey",
    "AccountObservation",
    "AccountOpenOrder",
    "AccountPosition",
    "AccountResourceKey",
    "MARKET_EVENT_OPERATION",
    "MARKET_EVENT_SCHEMA",
    "MARKET_EVENT_SCHEMA_VERSION",
    "MarketBar",
    "MarketHistoryPage",
    "MarketHistoryRequest",
    "MarketInstrumentKey",
    "MarketProviderEvent",
    "MarketSourceDescriptor",
    "MarketSubscriptionKey",
    "WireEnvelope",
    "WireIdentity",
    "WireMessageKind",
    "WireRole",
    "WireScope",
    "validate_event",
]
