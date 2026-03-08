"""
HTX (Huobi) Exchange Registration Module
Registers HTX feed classes for all asset types with the global ExchangeRegistry.
Import this module to complete registration.

Supported asset types:
- SPOT: Spot trading
- MARGIN: Margin trading
- USDT_SWAP: USDT-M Linear Swap (Perpetual)
- COIN_SWAP: Coin-M Inverse Swap (Perpetual)
- OPTION: Options trading
"""

from bt_api_py.balance_utils import simple_balance_handler as _htx_balance_handler
from bt_api_py.containers.exchanges.htx_exchange_data import (
    HtxExchangeDataCoinSwap,
    HtxExchangeDataMargin,
    HtxExchangeDataOption,
    HtxExchangeDataSpot,
    HtxExchangeDataUsdtSwap,
)
from bt_api_py.feeds.live_htx import (
    HtxAccountWssDataCoinSwap,
    HtxAccountWssDataMargin,
    HtxAccountWssDataOption,
    HtxAccountWssDataSpot,
    HtxAccountWssDataUsdtSwap,
    HtxMarketWssDataCoinSwap,
    HtxMarketWssDataMargin,
    HtxMarketWssDataOption,
    HtxMarketWssDataSpot,
    HtxMarketWssDataUsdtSwap,
    HtxRequestDataCoinSwap,
    HtxRequestDataMargin,
    HtxRequestDataOption,
    HtxRequestDataSpot,
    HtxRequestDataUsdtSwap,
)
from bt_api_py.registry import ExchangeRegistry


def _htx_spot_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """HTX SPOT subscription handler."""
    exchange_data = HtxExchangeDataSpot()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "htx_market_data"
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    HtxMarketWssDataSpot(data_queue, **kwargs).start()
    if not bt_api._subscription_flags.get("HTX___SPOT_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "orders"},
        ]
        HtxAccountWssDataSpot(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["HTX___SPOT_account"] = True


def _htx_margin_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """HTX MARGIN subscription handler."""
    exchange_data = HtxExchangeDataMargin()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "htx_margin_market_data"
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    HtxMarketWssDataMargin(data_queue, **kwargs).start()
    if not bt_api._subscription_flags.get("HTX___MARGIN_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "orders"},
        ]
        HtxAccountWssDataMargin(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["HTX___MARGIN_account"] = True


def _htx_usdt_swap_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """HTX USDT_SWAP subscription handler."""
    exchange_data = HtxExchangeDataUsdtSwap()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "htx_usdt_swap_market_data"
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    HtxMarketWssDataUsdtSwap(data_queue, **kwargs).start()
    if not bt_api._subscription_flags.get("HTX___USDT_SWAP_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "orders"},
        ]
        HtxAccountWssDataUsdtSwap(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["HTX___USDT_SWAP_account"] = True


def _htx_coin_swap_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """HTX COIN_SWAP subscription handler."""
    exchange_data = HtxExchangeDataCoinSwap()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "htx_coin_swap_market_data"
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    HtxMarketWssDataCoinSwap(data_queue, **kwargs).start()
    if not bt_api._subscription_flags.get("HTX___COIN_SWAP_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "orders"},
        ]
        HtxAccountWssDataCoinSwap(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["HTX___COIN_SWAP_account"] = True


def _htx_option_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """HTX OPTION subscription handler."""
    exchange_data = HtxExchangeDataOption()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "htx_option_market_data"
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    HtxMarketWssDataOption(data_queue, **kwargs).start()
    if not bt_api._subscription_flags.get("HTX___OPTION_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "orders"},
        ]
        HtxAccountWssDataOption(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["HTX___OPTION_account"] = True


def register_htx():
    """Register all HTX asset types to global ExchangeRegistry"""
    # Spot
    ExchangeRegistry.register_feed("HTX___SPOT", HtxRequestDataSpot)
    ExchangeRegistry.register_exchange_data("HTX___SPOT", HtxExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("HTX___SPOT", _htx_balance_handler)
    ExchangeRegistry.register_stream("HTX___SPOT", "subscribe", _htx_spot_subscribe_handler)

    # Margin
    ExchangeRegistry.register_feed("HTX___MARGIN", HtxRequestDataMargin)
    ExchangeRegistry.register_exchange_data("HTX___MARGIN", HtxExchangeDataMargin)
    ExchangeRegistry.register_balance_handler("HTX___MARGIN", _htx_balance_handler)
    ExchangeRegistry.register_stream("HTX___MARGIN", "subscribe", _htx_margin_subscribe_handler)

    # USDT Swap (Linear Perpetual)
    ExchangeRegistry.register_feed("HTX___USDT_SWAP", HtxRequestDataUsdtSwap)
    ExchangeRegistry.register_exchange_data("HTX___USDT_SWAP", HtxExchangeDataUsdtSwap)
    ExchangeRegistry.register_balance_handler("HTX___USDT_SWAP", _htx_balance_handler)
    ExchangeRegistry.register_stream(
        "HTX___USDT_SWAP", "subscribe", _htx_usdt_swap_subscribe_handler
    )

    # Coin Swap (Inverse Perpetual)
    ExchangeRegistry.register_feed("HTX___COIN_SWAP", HtxRequestDataCoinSwap)
    ExchangeRegistry.register_exchange_data("HTX___COIN_SWAP", HtxExchangeDataCoinSwap)
    ExchangeRegistry.register_balance_handler("HTX___COIN_SWAP", _htx_balance_handler)
    ExchangeRegistry.register_stream(
        "HTX___COIN_SWAP", "subscribe", _htx_coin_swap_subscribe_handler
    )

    # Option
    ExchangeRegistry.register_feed("HTX___OPTION", HtxRequestDataOption)
    ExchangeRegistry.register_exchange_data("HTX___OPTION", HtxExchangeDataOption)
    ExchangeRegistry.register_balance_handler("HTX___OPTION", _htx_balance_handler)
    ExchangeRegistry.register_stream("HTX___OPTION", "subscribe", _htx_option_subscribe_handler)


# Auto-register on module import
register_htx()
