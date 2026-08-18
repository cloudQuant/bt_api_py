"""Module-level docstring."""

from bt_api_py.brokers.base import BrokerAdapter
from bt_api_py.brokers.errors import BrokerError, BrokerErrorCode
from bt_api_py.brokers.feed_bridge import FeedBrokerAdapter
from bt_api_py.brokers.loader import available_adapters, load_adapter, register_builtin_adapters
from bt_api_py.brokers.registry import list_registered_adapters, register_adapter
from bt_api_py.brokers.types import (
    AccountSnapshot,
    BrokerCapabilities,
    BrokerEvent,
    CancelOrderRequest,
    OrderRequest,
    OrderSnapshot,
    PositionSnapshot,
)

__all__ = [
    "AccountSnapshot",
    "available_adapters",
    "BrokerAdapter",
    "BrokerCapabilities",
    "BrokerError",
    "BrokerErrorCode",
    "BrokerEvent",
    "CancelOrderRequest",
    "FeedBrokerAdapter",
    "list_registered_adapters",
    "load_adapter",
    "OrderRequest",
    "OrderSnapshot",
    "PositionSnapshot",
    "register_adapter",
    "register_builtin_adapters",
]
