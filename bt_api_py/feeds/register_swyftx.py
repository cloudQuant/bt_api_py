"""
Swyftx Exchange Registration Module
"""

from bt_api_py.balance_utils import simple_balance_handler as _swyftx_balance_handler
from bt_api_py.containers.exchanges.swyftx_exchange_data import SwyftxExchangeDataSpot
from bt_api_py.feeds.live_swyftx.spot import SwyftxRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_swyftx():
    """Register Swyftx SPOT to global ExchangeRegistry"""
    # Register Feed class
    ExchangeRegistry.register_feed("SWYFTX___SPOT", SwyftxRequestDataSpot)

    # Register config class
    ExchangeRegistry.register_exchange_data("SWYFTX___SPOT", SwyftxExchangeDataSpot)

    # Register balance handler
    ExchangeRegistry.register_balance_handler("SWYFTX___SPOT", _swyftx_balance_handler)


# Auto-register on module import
register_swyftx()
