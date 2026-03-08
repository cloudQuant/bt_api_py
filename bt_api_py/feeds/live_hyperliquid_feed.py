"""Backward-compatible re-export wrapper.

All classes have been moved to the ``bt_api_py.feeds.live_hyperliquid`` package.
Import from here continues to work so that existing code is not broken.
"""

# Re-export exchange data classes for convenience
from bt_api_py.containers.exchanges.hyperliquid_exchange_data import (  # noqa: F401
    HyperliquidExchangeDataSpot,
)

# Base classes
from bt_api_py.feeds.live_hyperliquid.request_base import HyperliquidRequestData  # noqa: F401

# Spot
from bt_api_py.feeds.live_hyperliquid.spot import (  # noqa: F401
    HyperliquidAccountWssDataSpot,
    HyperliquidMarketWssDataSpot,
    HyperliquidRequestDataSpot,
)

__all__ = [
    # Base
    "HyperliquidRequestData",
    # Spot
    "HyperliquidRequestDataSpot",
    "HyperliquidMarketWssDataSpot",
    "HyperliquidAccountWssDataSpot",
    # Data containers
    "HyperliquidExchangeDataSpot",
]
