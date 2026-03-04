"""
CTP 交易所注册模块
将 CTP Future 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册

依赖: pip install ctp-python
"""

from bt_api_py.balance_utils import simple_balance_handler as _ctp_balance_handler
from bt_api_py.containers.exchanges.ctp_exchange_data import CtpExchangeDataFuture
from bt_api_py.feeds.live_ctp_feed import CtpMarketStream, CtpRequestDataFuture, CtpTradeStream
from bt_api_py.registry import ExchangeRegistry


def _ctp_future_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """CTP FUTURE 订阅处理函数
    启动行情流（CtpMarketStream）和交易流（CtpTradeStream）

    :param data_queue: queue.Queue
    :param exchange_params: dict, 包含 broker_id, user_id, password, md_front, td_front 等
    :param topics: list of topic dicts, 如 [{"topic": "tick", "symbol": "IF2506"}, ...]
    :param bt_api: BtApi 实例
    """
    topic_list = [t.get("topic") for t in topics]

    # 启动行情流 — 订阅 tick/ticker/depth 数据
    has_tick = any(t in topic_list for t in ("tick", "ticker", "depth"))
    if has_tick:
        market_kwargs = dict(exchange_params.items())
        market_kwargs["stream_name"] = "ctp_market_stream"
        market_kwargs["topics"] = topics
        stream = CtpMarketStream(data_queue, **market_kwargs)
        stream.start()
        bt_api.log("CTP market stream started")

    # 启动交易流 — 接收订单/成交回报推送
    if not bt_api._subscription_flags.get("CTP___FUTURE_account", False):
        trade_kwargs = dict(exchange_params.items())
        trade_kwargs["stream_name"] = "ctp_trade_stream"
        stream = CtpTradeStream(data_queue, **trade_kwargs)
        stream.start()
        bt_api._subscription_flags["CTP___FUTURE_account"] = True
        bt_api.log("CTP trade stream started")


def register_ctp():
    """注册 CTP Future 到全局 ExchangeRegistry"""
    ExchangeRegistry.register_feed("CTP___FUTURE", CtpRequestDataFuture)
    ExchangeRegistry.register_exchange_data("CTP___FUTURE", CtpExchangeDataFuture)
    ExchangeRegistry.register_balance_handler("CTP___FUTURE", _ctp_balance_handler)
    ExchangeRegistry.register_stream("CTP___FUTURE", "subscribe", _ctp_future_subscribe_handler)
    ExchangeRegistry.register_stream("CTP___FUTURE", "market", CtpMarketStream)
    ExchangeRegistry.register_stream("CTP___FUTURE", "account", CtpTradeStream)


# 模块导入时自动注册
register_ctp()
