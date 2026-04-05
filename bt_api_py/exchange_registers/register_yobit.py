"""
YoBit Exchange Registration Module
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _yobit_balance_handler
from bt_api_py.containers.exchanges.yobit_exchange_data import YobitExchangeDataSpot
from bt_api_py.feeds.live_yobit.spot import YobitRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def _yobit_spot_subscribe_handler(feed, topics):
    """Placeholder subscribe handler for YoBit WebSocket."""


def register_yobit():
    """Register YoBit SPOT to global ExchangeRegistry"""
    ExchangeRegistry.register_feed("YOBIT___SPOT", YobitRequestDataSpot)
    ExchangeRegistry.register_exchange_data("YOBIT___SPOT", YobitExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("YOBIT___SPOT", _yobit_balance_handler)
    ExchangeRegistry.register_stream("YOBIT___SPOT", "subscribe", _yobit_spot_subscribe_handler)


# Auto-register on module import
register_yobit()
