"""SatoshiTango Exchange Registration Module."""

from bt_api_py.balance_utils import simple_balance_handler as _satoshitango_balance_handler
from bt_api_py.containers.exchanges.satoshitango_exchange_data import SatoshiTangoExchangeDataSpot
from bt_api_py.feeds.live_satoshitango.spot import SatoshiTangoRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_satoshitango():
    """Register SatoshiTango SPOT to global ExchangeRegistry."""
    # Register Feed class
    ExchangeRegistry.register_feed("SATOSHITANGO___SPOT", SatoshiTangoRequestDataSpot)

    # Register config class
    ExchangeRegistry.register_exchange_data("SATOSHITANGO___SPOT", SatoshiTangoExchangeDataSpot)

    # Register balance handler
    ExchangeRegistry.register_balance_handler("SATOSHITANGO___SPOT", _satoshitango_balance_handler)


# Auto-register on module import
register_satoshitango()
