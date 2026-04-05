# Spot trading feed
from __future__ import annotations

from bt_api_py.feeds.live_bitfinex.request_base import BitfinexRequestData
from bt_api_py.feeds.live_bitfinex.spot import BitfinexRequestDataSpot

__all__ = [
    # Base
    "BitfinexRequestData",
    # Spot
    "BitfinexRequestDataSpot",
]
