"""
dYdX Exchange Integration for bt_api_py

This package provides the dYdX exchange integration, supporting:
- REST API for market data and account information
- Perpetual contracts (SWAP) trading
- WebSocket support (placeholder for future implementation)

dYdX is a decentralized derivatives exchange built on dYdX Chain.
"""

from .request_base import DydxRequestData
from .spot import DydxRequestDataSpot

__all__ = [
    "DydxRequestData",
    "DydxRequestDataSpot",
]
