"""
Latoken 交易所注册模块
将 Latoken Spot 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _latoken_balance_handler
from bt_api_py.containers.exchanges.latoken_exchange_data import LatokenExchangeDataSpot
from bt_api_py.feeds.live_latoken import (
    LatokenRequestDataSpot,
)
from bt_api_py.registry import ExchangeRegistry


def _latoken_spot_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Latoken SPOT 订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例 (用于访问共享状态)
    """
    exchange_data = LatokenExchangeDataSpot()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "latoken_market_data"
    kwargs["wss_url"] = ""  # Latoken WebSocket not fully documented
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    # Note: WebSocket not fully supported for Latoken
    # LatokenMarketWssDataSpot(data_queue, **kwargs).start()
    if not bt_api._subscription_flags.get("LATOKEN___SPOT_account", False):
        bt_api._subscription_flags["LATOKEN___SPOT_account"] = True


def register_latoken():
    """注册 Latoken Spot 到全局 ExchangeRegistry"""
    # Spot
    ExchangeRegistry.register_feed("LATOKEN___SPOT", LatokenRequestDataSpot)
    ExchangeRegistry.register_exchange_data("LATOKEN___SPOT", LatokenExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("LATOKEN___SPOT", _latoken_balance_handler)
    ExchangeRegistry.register_stream("LATOKEN___SPOT", "subscribe", _latoken_spot_subscribe_handler)


# 模块导入时自动注册
register_latoken()
