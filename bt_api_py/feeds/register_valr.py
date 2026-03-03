"""
VALR Exchange Registration Module
"""

from bt_api_py.balance_utils import simple_balance_handler as _valr_balance_handler
from bt_api_py.containers.exchanges.valr_exchange_data import ValrExchangeDataSpot
from bt_api_py.feeds.live_valr.spot import ValrRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def _valr_spot_subscribe_handler(feed, topics):
    """Placeholder subscribe handler for VALR WebSocket."""
    pass


def register_valr():
    """Register VALR SPOT to global ExchangeRegistry"""
    ExchangeRegistry.register_feed("VALR___SPOT", ValrRequestDataSpot)
    ExchangeRegistry.register_exchange_data("VALR___SPOT", ValrExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("VALR___SPOT", _valr_balance_handler)
    ExchangeRegistry.register_stream(
        "VALR___SPOT", "subscribe", _valr_spot_subscribe_handler
    )


# Auto-register on module import
register_valr()
