"""
Zaif Exchange Registration Module
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _zaif_balance_handler
from bt_api_py.containers.exchanges.zaif_exchange_data import ZaifExchangeDataSpot
from bt_api_py.feeds.live_zaif.spot import ZaifRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def _zaif_spot_subscribe_handler(feed, topics):
    """Placeholder subscribe handler for Zaif WebSocket."""


def register_zaif():
    """Register Zaif SPOT to global ExchangeRegistry"""
    ExchangeRegistry.register_feed("ZAIF___SPOT", ZaifRequestDataSpot)
    ExchangeRegistry.register_exchange_data("ZAIF___SPOT", ZaifExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("ZAIF___SPOT", _zaif_balance_handler)
    ExchangeRegistry.register_stream("ZAIF___SPOT", "subscribe", _zaif_spot_subscribe_handler)


# Auto-register on module import
register_zaif()
