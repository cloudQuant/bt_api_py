"""
Upbit registration module.
Registers UpbitRequestDataSpot and UpbitExchangeDataSpot with ExchangeRegistry.
Import this module to auto-register.
"""

from bt_api_py.balance_utils import simple_balance_handler as _upbit_balance_handler
from bt_api_py.containers.exchanges.upbit_exchange_data import UpbitExchangeDataSpot
from bt_api_py.feeds.live_upbit.spot import UpbitRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_upbit():
    """Register Upbit SPOT to ExchangeRegistry."""
    ExchangeRegistry.register_feed("UPBIT___SPOT", UpbitRequestDataSpot)
    ExchangeRegistry.register_exchange_data("UPBIT___SPOT", UpbitExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("UPBIT___SPOT", _upbit_balance_handler)


# Auto-register on import
register_upbit()
