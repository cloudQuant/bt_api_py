# Spot trading feed
from bt_api_py.feeds.live_bitfinex.request_base import BitfinexRequestData
from bt_api_py.feeds.live_bitfinex.spot import BitfinexRequestDataSpot

__all__ = [
    # Base
    "BitfinexRequestData",
    # Spot
    "BitfinexRequestDataSpot",
]
