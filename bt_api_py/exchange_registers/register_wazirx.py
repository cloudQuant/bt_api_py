"""
WazirX Exchange Registration Module
"""

from bt_api_py.balance_utils import simple_balance_handler as _wazirx_balance_handler
from bt_api_py.containers.exchanges.wazirx_exchange_data import WazirxExchangeDataSpot
from bt_api_py.feeds.live_wazirx.spot import WazirxRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def _wazirx_spot_subscribe_handler(feed, topics):
    """Placeholder subscribe handler for WazirX WebSocket."""
    pass


def register_wazirx():
    """Register WazirX SPOT to global ExchangeRegistry"""
    ExchangeRegistry.register_feed("WAZIRX___SPOT", WazirxRequestDataSpot)
    ExchangeRegistry.register_exchange_data("WAZIRX___SPOT", WazirxExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("WAZIRX___SPOT", _wazirx_balance_handler)
    ExchangeRegistry.register_stream(
        "WAZIRX___SPOT", "subscribe", _wazirx_spot_subscribe_handler
    )


# Auto-register on module import
register_wazirx()
