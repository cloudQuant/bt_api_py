"""
Bithumb 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _bithumb_balance_handler
from bt_api_py.containers.exchanges.bithumb_exchange_data import BithumbExchangeDataSpot
from bt_api_py.feeds.live_bithumb.spot import BithumbRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bithumb() -> None:
    """Register Bithumb SPOT to global ExchangeRegistry.

    This function registers:
    - Feed class for market data
    - Exchange data configuration
    - Balance handler for account management
    """
    ExchangeRegistry.register_feed("BITHUMB___SPOT", BithumbRequestDataSpot)
    ExchangeRegistry.register_exchange_data("BITHUMB___SPOT", BithumbExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("BITHUMB___SPOT", _bithumb_balance_handler)


register_bithumb()
