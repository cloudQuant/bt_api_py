"""
Kraken Exchange Feed Module
"""

from bt_api_py.feeds.live_kraken.request_base import KrakenRequestData
from bt_api_py.feeds.live_kraken.spot import (
    KrakenRequestDataSpot,
    KrakenMarketWssDataSpot,
    KrakenAccountWssDataSpot,
)
from bt_api_py.feeds.live_kraken.futures import (
    KrakenRequestDataFutures,
    KrakenMarketWssDataFutures,
    KrakenAccountWssDataFutures,
)

__all__ = [
    "KrakenRequestData",
    "KrakenRequestDataSpot",
    "KrakenMarketWssDataSpot",
    "KrakenAccountWssDataSpot",
    "KrakenRequestDataFutures",
    "KrakenMarketWssDataFutures",
    "KrakenAccountWssDataFutures",
]
