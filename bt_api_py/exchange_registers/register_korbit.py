"""
Korbit 交易所注册模块
将 Korbit Spot 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _korbit_balance_handler
from bt_api_py.containers.exchanges.korbit_exchange_data import KorbitExchangeDataSpot
from bt_api_py.feeds.live_korbit import (
    KorbitAccountWssDataSpot,
    KorbitMarketWssDataSpot,
    KorbitRequestDataSpot,
)
from bt_api_py.registry import ExchangeRegistry


def _korbit_spot_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Korbit SPOT 订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例 (用于访问共享状态)
    """
    exchange_data = KorbitExchangeDataSpot()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "korbit_market_data"
    kwargs["wss_url"] = "wss://ws-api.korbit.co.kr/v2/public"
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    KorbitMarketWssDataSpot(data_queue, **kwargs).start()
    if not bt_api._subscription_flags.get("KORBIT___SPOT_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "order"},
            {"topic": "trade"},
        ]
        account_kwargs["wss_url"] = "wss://ws-api.korbit.co.kr/v2/private"
        KorbitAccountWssDataSpot(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["KORBIT___SPOT_account"] = True


def register_korbit():
    """注册 Korbit Spot 到全局 ExchangeRegistry"""
    # Spot
    ExchangeRegistry.register_feed("KORBIT___SPOT", KorbitRequestDataSpot)
    ExchangeRegistry.register_exchange_data("KORBIT___SPOT", KorbitExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("KORBIT___SPOT", _korbit_balance_handler)
    ExchangeRegistry.register_stream("KORBIT___SPOT", "subscribe", _korbit_spot_subscribe_handler)


# 模块导入时自动注册
register_korbit()
