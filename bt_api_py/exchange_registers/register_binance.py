"""
Binance 交易所注册模块
将 Binance Swap/Spot 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _binance_balance_handler
from bt_api_py.containers.exchanges.binance_exchange_data import (
    BinanceExchangeDataSpot,
    BinanceExchangeDataSwap,
)
from bt_api_py.feeds.live_binance_feed import (
    BinanceAccountWssDataSpot,
    BinanceAccountWssDataSwap,
    BinanceMarketWssDataSpot,
    BinanceMarketWssDataSwap,
    BinanceRequestDataSpot,
    BinanceRequestDataSwap,
)
from bt_api_py.registry import ExchangeRegistry


def _binance_swap_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Binance SWAP 订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例 (用于访问共享状态)
    """
    exchange_data = BinanceExchangeDataSwap()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "binance_market_data"
    kwargs["wss_url"] = "wss://fstream.binance.com/ws"
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    if not bt_api._subscription_flags.get("BINANCE___SWAP_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "order"},
            {"topic": "trade"},
        ]
        BinanceAccountWssDataSwap(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["BINANCE___SWAP_account"] = True


def _binance_spot_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Binance SPOT 订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例
    """
    exchange_data = BinanceExchangeDataSpot()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "binance_market_data"
    kwargs["wss_url"] = "wss://stream.binance.com:9443/ws"
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    BinanceMarketWssDataSpot(data_queue, **kwargs).start()
    if not bt_api._subscription_flags.get("BINANCE___SPOT_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "order"},
            {"topic": "trade"},
        ]
        BinanceAccountWssDataSpot(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["BINANCE___SPOT_account"] = True


def register_binance():
    """注册 Binance Swap 和 Spot 到全局 ExchangeRegistry"""
    # Swap
    ExchangeRegistry.register_feed("BINANCE___SWAP", BinanceRequestDataSwap)
    ExchangeRegistry.register_exchange_data("BINANCE___SWAP", BinanceExchangeDataSwap)
    ExchangeRegistry.register_balance_handler("BINANCE___SWAP", _binance_balance_handler)
    ExchangeRegistry.register_stream("BINANCE___SWAP", "subscribe", _binance_swap_subscribe_handler)

    # Spot
    ExchangeRegistry.register_feed("BINANCE___SPOT", BinanceRequestDataSpot)
    ExchangeRegistry.register_exchange_data("BINANCE___SPOT", BinanceExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("BINANCE___SPOT", _binance_balance_handler)
    ExchangeRegistry.register_stream("BINANCE___SPOT", "subscribe", _binance_spot_subscribe_handler)


# 模块导入时自动注册
register_binance()
