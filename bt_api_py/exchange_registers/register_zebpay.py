"""
Zebpay Exchange Registration Module
"""

from bt_api_py.balance_utils import simple_balance_handler as _zebpay_balance_handler
from bt_api_py.containers.exchanges.zebpay_exchange_data import ZebpayExchangeDataSpot
from bt_api_py.feeds.live_zebpay.spot import ZebpayRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def _zebpay_spot_subscribe_handler(feed, topics):
    """Placeholder subscribe handler for Zebpay WebSocket."""
    pass


def register_zebpay():
    """Register Zebpay SPOT to global ExchangeRegistry"""
    ExchangeRegistry.register_feed("ZEBPAY___SPOT", ZebpayRequestDataSpot)
    ExchangeRegistry.register_exchange_data("ZEBPAY___SPOT", ZebpayExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("ZEBPAY___SPOT", _zebpay_balance_handler)
    ExchangeRegistry.register_stream("ZEBPAY___SPOT", "subscribe", _zebpay_spot_subscribe_handler)


# Auto-register on module import
register_zebpay()
