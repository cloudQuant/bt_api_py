"""
Bitunix 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _bitunix_balance_handler
from bt_api_py.containers.exchanges.bitunix_exchange_data import BitunixExchangeDataSpot
from bt_api_py.feeds.live_bitunix.spot import BitunixRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bitunix() -> None:
    """Register Bitunix SPOT to global ExchangeRegistry.

    This function registers:
    - Feed class for market data
    - Exchange data configuration
    - Balance handler for account management
    """
    ExchangeRegistry.register_feed("BITUNIX___SPOT", BitunixRequestDataSpot)
    ExchangeRegistry.register_exchange_data("BITUNIX___SPOT", BitunixExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("BITUNIX___SPOT", _bitunix_balance_handler)


register_bitunix()
