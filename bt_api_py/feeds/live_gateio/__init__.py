"""
Gate.io Exchange Feed Implementation

Provides real-time market data and trading functionality for Gate.io exchange.
Supports Spot and Futures (USDT-M) trading.

Components:
- request_base.py: REST API client with HMAC SHA512 authentication
- spot.py: Spot trading feed implementation
- swap.py: Futures (USDT-M) trading feed implementation
"""

from bt_api_py.feeds.live_gateio.request_base import GateioRequestData
from bt_api_py.feeds.live_gateio.spot import (
    GateioRequestDataSpot,
    GateioMarketWssDataSpot,
    GateioAccountWssDataSpot,
)
from bt_api_py.feeds.live_gateio.swap import (
    GateioRequestDataSwap,
    GateioMarketWssDataSwap,
    GateioAccountWssDataSwap,
)

__all__ = [
    "GateioRequestData",
    "GateioRequestDataSpot",
    "GateioMarketWssDataSpot",
    "GateioAccountWssDataSpot",
    "GateioRequestDataSwap",
    "GateioMarketWssDataSwap",
    "GateioAccountWssDataSwap",
]
