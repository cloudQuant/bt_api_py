"""
Bitget Exchange Integration for bt_api_py

This package provides Bitget exchange API integration including:
- REST API implementation with HMAC SHA256 authentication
- Spot trading feed
- Swap (USDT-M Futures) trading feed
- WebSocket support (to be implemented)
"""

from .request_base import BitgetRequestData
from .spot import BitgetRequestDataSpot, BitgetAccountWssDataSpot, BitgetMarketWssDataSpot
from .swap import BitgetRequestDataSwap, BitgetAccountWssDataSwap, BitgetMarketWssDataSwap

__all__ = [
    "BitgetRequestData",
    "BitgetRequestDataSpot",
    "BitgetAccountWssDataSpot",
    "BitgetMarketWssDataSpot",
    "BitgetRequestDataSwap",
    "BitgetAccountWssDataSwap",
    "BitgetMarketWssDataSwap",
]
