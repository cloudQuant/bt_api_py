"""
IB Web API 交易所注册模块
将 IB Web API Stock/Future 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _ib_web_balance_handler
from bt_api_py.containers.exchanges.ib_web_exchange_data import (
    IbWebExchangeDataFuture,
    IbWebExchangeDataStock,
)
from bt_api_py.feeds.live_ib_web_feed import IbWebRequestDataFuture, IbWebRequestDataStock
from bt_api_py.feeds.live_ib_web_stream import IbWebAccountStream, IbWebDataStream
from bt_api_py.registry import ExchangeRegistry


def _ib_web_stk_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """IB Web API 股票订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例
    """
    exchange_data = IbWebExchangeDataStock()
    kwargs = dict(exchange_params.items())
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics

    # 启动市场数据流
    IbWebDataStream(data_queue, **kwargs).start()

    # 启动账户数据流 (每个资产类型只启动一次)
    if not bt_api._subscription_flags.get("IB_WEB___STK_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "order"},
            {"topic": "trade"},
        ]
        IbWebAccountStream(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["IB_WEB___STK_account"] = True


def _ib_web_fut_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """IB Web API 期货订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例
    """
    exchange_data = IbWebExchangeDataFuture()
    kwargs = dict(exchange_params.items())
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics

    IbWebDataStream(data_queue, **kwargs).start()

    if not bt_api._subscription_flags.get("IB_WEB___FUT_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "order"},
            {"topic": "trade"},
        ]
        IbWebAccountStream(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["IB_WEB___FUT_account"] = True


def register_ib_web():
    """注册 IB Web API Stock 和 Future 到全局 ExchangeRegistry"""
    # Stock
    ExchangeRegistry.register_feed("IB_WEB___STK", IbWebRequestDataStock)
    ExchangeRegistry.register_exchange_data("IB_WEB___STK", IbWebExchangeDataStock)
    ExchangeRegistry.register_balance_handler("IB_WEB___STK", _ib_web_balance_handler)
    ExchangeRegistry.register_stream("IB_WEB___STK", "subscribe", _ib_web_stk_subscribe_handler)

    # Future
    ExchangeRegistry.register_feed("IB_WEB___FUT", IbWebRequestDataFuture)
    ExchangeRegistry.register_exchange_data("IB_WEB___FUT", IbWebExchangeDataFuture)
    ExchangeRegistry.register_balance_handler("IB_WEB___FUT", _ib_web_balance_handler)
    ExchangeRegistry.register_stream("IB_WEB___FUT", "subscribe", _ib_web_fut_subscribe_handler)


# 模块导入时自动注册
register_ib_web()
