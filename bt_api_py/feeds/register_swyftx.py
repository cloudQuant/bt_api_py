"""
Swyftx Exchange Registration Module
"""

from bt_api_py.balance_utils import simple_balance_handler as _swyftx_balance_handler
from bt_api_py.containers.exchanges.swyftx_exchange_data import SwyftxExchangeDataSpot
from bt_api_py.feeds.live_swyftx.spot import SwyftxRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def _swyftx_spot_subscribe_handler(feed, topics):
    """Placeholder subscribe handler (Swyftx WSS not publicly documented)."""
    pass


def register_swyftx():
    """Register Swyftx SPOT to global ExchangeRegistry"""
    ExchangeRegistry.register_feed("SWYFTX___SPOT", SwyftxRequestDataSpot)
    ExchangeRegistry.register_exchange_data("SWYFTX___SPOT", SwyftxExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("SWYFTX___SPOT", _swyftx_balance_handler)
    ExchangeRegistry.register_stream(
        "SWYFTX___SPOT", "subscribe", _swyftx_spot_subscribe_handler
    )


# Auto-register on module import
register_swyftx()
