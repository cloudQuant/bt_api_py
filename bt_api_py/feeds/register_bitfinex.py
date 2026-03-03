"""
Bitfinex 交易所注册模块
将 Bitfinex Spot 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _bitfinex_balance_handler
from bt_api_py.containers.exchanges.bitfinex_exchange_data import BitfinexExchangeDataSpot
from bt_api_py.feeds.live_bitfinex import (
    BitfinexAccountWssDataSpot,
    BitfinexMarketWssDataSpot,
    BitfinexRequestDataSpot,
)
from bt_api_py.registry import ExchangeRegistry


def _bitfinex_spot_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Bitfinex SPOT 订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例 (用于访问共享状态)
    """
    exchange_data = BitfinexExchangeDataSpot()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "bitfinex_market_data"
    kwargs["wss_url"] = "wss://api-pub.bitfinex.com/ws/2"
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    BitfinexMarketWssDataSpot(data_queue, **kwargs).start()
    if not bt_api._subscription_flags.get("BITFINEX___SPOT_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "order"},
            {"topic": "trade"},
        ]
        BitfinexAccountWssDataSpot(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["BITFINEX___SPOT_account"] = True


def register_bitfinex():
    """注册 Bitfinex Spot 到全局 ExchangeRegistry"""
    # Spot
    ExchangeRegistry.register_feed("BITFINEX___SPOT", BitfinexRequestDataSpot)
    ExchangeRegistry.register_exchange_data("BITFINEX___SPOT", BitfinexExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("BITFINEX___SPOT", _bitfinex_balance_handler)
    ExchangeRegistry.register_stream("BITFINEX___SPOT", "subscribe", _bitfinex_spot_subscribe_handler)


# 模块导入时自动注册
register_bitfinex()