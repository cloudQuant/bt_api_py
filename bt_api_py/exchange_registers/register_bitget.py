"""
Bitget 交易所注册模块
将 Bitget Spot/Swap 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.containers.exchanges.bitget_exchange_data import (
    BitgetExchangeDataSpot,
    BitgetExchangeDataSwap,
)
from bt_api_py.feeds.live_bitget import (
    BitgetAccountWssDataSpot,
    BitgetMarketWssDataSpot,
    BitgetRequestDataSpot,
    BitgetAccountWssDataSwap,
    BitgetMarketWssDataSwap,
    BitgetRequestDataSwap,
)
from bt_api_py.registry import ExchangeRegistry


def _bitget_spot_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Bitget SPOT 订阅处理函数"""
    exchange_data = BitgetExchangeDataSpot()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "bitget_market_data"
    kwargs["wss_url"] = "wss://ws.bitget.com/spot/v1/stream"
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    BitgetMarketWssDataSpot(data_queue, **kwargs).start()
    if not bt_api._subscription_flags.get("BITGET___SPOT_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "order"},
            {"topic": "trade"},
        ]
        BitgetAccountWssDataSpot(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["BITGET___SPOT_account"] = True


def _bitget_swap_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Bitget SWAP 订阅处理函数"""
    exchange_data = BitgetExchangeDataSwap()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "bitget_swap_market_data"
    kwargs["wss_url"] = "wss://ws.bitget.com/swap/v1/stream"
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    BitgetMarketWssDataSwap(data_queue, **kwargs).start()
    if not bt_api._subscription_flags.get("BITGET___SWAP_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "order"},
            {"topic": "trade"},
        ]
        BitgetAccountWssDataSwap(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["BITGET___SWAP_account"] = True


def register_bitget():
    """注册 Bitget Spot/Swap 到全局 ExchangeRegistry"""
    # Spot
    ExchangeRegistry.register_feed("BITGET___SPOT", BitgetRequestDataSpot)
    ExchangeRegistry.register_exchange_data("BITGET___SPOT", BitgetExchangeDataSpot)
    ExchangeRegistry.register_stream("BITGET___SPOT", "subscribe", _bitget_spot_subscribe_handler)
    # Swap
    ExchangeRegistry.register_feed("BITGET___SWAP", BitgetRequestDataSwap)
    ExchangeRegistry.register_exchange_data("BITGET___SWAP", BitgetExchangeDataSwap)
    ExchangeRegistry.register_stream("BITGET___SWAP", "subscribe", _bitget_swap_subscribe_handler)


# 模块导入时自动注册
register_bitget()
