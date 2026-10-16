"""Passive legacy wire DTOs; the SDK forwarding runtime is retired.

The old forwarding package remains in the source distribution only for passive
wire records and historical evidence.  Resolving a runtime, client, router,
backend, or adapter from this package raises a typed migration error.  The
implementation modules are not imported as a side effect of this package
initializer.
"""

from __future__ import annotations

from bt_api_py._contracts.errors import LegacyForwardingRetiredError
from bt_api_py.forwarding.schema import (
    MAX_MESSAGE_BYTES,
    CancelCommand,
    CommandAck,
    ForwardingError,
    MarketEvent,
    OrderCommand,
    PrivateEvent,
    deserialize_message,
    normalize_market_symbol,
    serialize_message,
)

_RETIRED_EXPORTS = frozenset(
    {
        "BtApiForwardingAdapter",
        "BtApiForwardingBridge",
        "ForwardingClient",
        "ForwardingRuntime",
        "InMemoryForwardingBus",
        "MarketDataHub",
        "MarketSubscription",
        "OrderRouter",
        "RiskRuleSet",
        "SQLiteStateStore",
        "SourceSupervisor",
        "ZmqBtApiBackend",
        "ZmqForwardingClient",
        "ZmqForwardingRuntime",
    }
)


def __getattr__(name: str):
    if name in _RETIRED_EXPORTS:
        raise LegacyForwardingRetiredError(
            f"bt_api_py.forwarding.{name}",
            detail=(
                "the SDK forwarding runtime was retired; ordinary calls use BtApi with the "
                "direct transport, and managed writes use Backtrader ServiceExecutionSession "
                "with ServiceHost and service.order_entry"
            ),
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "MAX_MESSAGE_BYTES",
    "CancelCommand",
    "CommandAck",
    "ForwardingError",
    "MarketEvent",
    "OrderCommand",
    "PrivateEvent",
    "deserialize_message",
    "normalize_market_symbol",
    "serialize_message",
]
