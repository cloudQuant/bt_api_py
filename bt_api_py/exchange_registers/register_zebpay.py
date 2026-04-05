"""
Zebpay Exchange Registration Module
"""

from __future__ import annotations

from typing import Any

from bt_api_py.balance_utils import simple_balance_handler as _zebpay_balance_handler
from bt_api_py.containers.exchanges.zebpay_exchange_data import ZebpayExchangeDataSpot
from bt_api_py.feeds.live_zebpay.spot import ZebpayRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def _zebpay_spot_subscribe_handler(feed: Any, topics: list[Any]) -> None:
    """Placeholder subscribe handler for Zebpay WebSocket.

    Args:
        feed: Feed instance.
        topics: List of topics to subscribe.
    """


def register_zebpay() -> None:
    """Register Zebpay SPOT to global ExchangeRegistry.

    This function registers:
    - Feed class for market data
    - Exchange data configuration
    - Balance handler for account management
    - Subscribe handler for WebSocket streams
    """
    ExchangeRegistry.register_feed("ZEBPAY___SPOT", ZebpayRequestDataSpot)
    ExchangeRegistry.register_exchange_data("ZEBPAY___SPOT", ZebpayExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("ZEBPAY___SPOT", _zebpay_balance_handler)
    ExchangeRegistry.register_stream("ZEBPAY___SPOT", "subscribe", _zebpay_spot_subscribe_handler)


register_zebpay()
