"""
Poloniex 交易所注册模块
将 Poloniex 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import nested_balance_handler as _poloniex_balance_handler
from bt_api_py.containers.exchanges.poloniex_exchange_data import (
    PoloniexExchangeDataSpot,
)
from bt_api_py.feeds.live_poloniex import PoloniexRequestData, PoloniexRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def _poloniex_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Poloniex 订阅处理函数

    Args:
        data_queue: queue.Queue
        exchange_params: dict
        topics: list of topic dicts
        bt_api: BtApi 实例
    """
    # TODO: 实现 WebSocket 订阅逻辑
    topic_list = [i["topic"] for i in topics]

    if "ticker" in topic_list or "depth" in topic_list:
        # 订阅市场数据
        pass

    if "account" in topic_list or "orders" in topic_list:
        # 订阅账户数据
        pass


def _poloniex_spot_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Poloniex SPOT 订阅处理函数"""
    _poloniex_subscribe_handler(data_queue, exchange_params, topics, bt_api)


def register_poloniex():
    """注册 Poloniex SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("POLONIEX___SPOT", PoloniexRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("POLONIEX___SPOT", PoloniexExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("POLONIEX___SPOT", _poloniex_balance_handler)

    # 注册流处理器
    ExchangeRegistry.register_stream("POLONIEX___SPOT", "subscribe", _poloniex_spot_subscribe_handler)


# 模块导入时自动注册
register_poloniex()
