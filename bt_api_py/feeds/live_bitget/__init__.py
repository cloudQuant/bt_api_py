"""
Bitget Exchange Integration for bt_api_py

This package provides Bitget exchange API integration including:
- REST API implementation with HMAC SHA256 authentication
- Spot trading feed
- Swap (USDT-M Futures) trading feed
- WebSocket support (to be implemented)
"""

from __future__ import annotations

from .request_base import BitgetRequestData
from .spot import BitgetAccountWssDataSpot, BitgetMarketWssDataSpot, BitgetRequestDataSpot
from .swap import BitgetAccountWssDataSwap, BitgetMarketWssDataSwap, BitgetRequestDataSwap

__all__ = [
    "BitgetRequestData",
    "BitgetRequestDataSpot",
    "BitgetAccountWssDataSpot",
    "BitgetMarketWssDataSpot",
    "BitgetRequestDataSwap",
    "BitgetAccountWssDataSwap",
    "BitgetMarketWssDataSwap",
]
