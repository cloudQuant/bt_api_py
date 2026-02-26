"""
CTP 交易所注册模块
将 CTP Future 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册

依赖: pip install ctp-python
"""
from bt_api_py.registry import ExchangeRegistry
from bt_api_py.feeds.live_ctp_feed import (
    CtpRequestDataFuture,
    CtpMarketStream,
    CtpTradeStream,
)
from bt_api_py.containers.exchanges.ctp_exchange_data import CtpExchangeDataFuture


def _ctp_balance_handler(account_list):
    """CTP 余额解析处理函数
    :param account_list: list of AccountData
    :return: (value_result, cash_result)
    """
    value_result = {}
    cash_result = {}
    for account in account_list:
        account.init_data()
        currency = account.get_account_type()
        cash_result[currency] = {}
        cash_result[currency]["cash"] = account.get_available_margin()
        value_result[currency] = {}
        value_result[currency]["value"] = account.get_margin() + account.get_unrealized_profit()
    return value_result, cash_result


def _ctp_future_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """CTP FUTURE 订阅处理函数
    启动行情流（CtpMarketStream）和交易流（CtpTradeStream）

    :param data_queue: queue.Queue
    :param exchange_params: dict, 包含 broker_id, user_id, password, md_front, td_front 等
    :param topics: list of topic dicts, 如 [{"topic": "tick", "symbol": "IF2506"}, ...]
    :param bt_api: BtApi 实例
    """
    topic_list = [t.get('topic') for t in topics]

    # 启动行情流 — 订阅 tick/ticker/depth 数据
    has_tick = any(t in topic_list for t in ('tick', 'ticker', 'depth'))
    if has_tick:
        market_kwargs = {k: v for k, v in exchange_params.items()}
        market_kwargs['stream_name'] = 'ctp_market_stream'
        market_kwargs['topics'] = topics
        stream = CtpMarketStream(data_queue, **market_kwargs)
        stream.start()
        bt_api.log("CTP market stream started")

    # 启动交易流 — 接收订单/成交回报推送
    if not getattr(bt_api, '_ctp_future_account_subscribed', False):
        trade_kwargs = {k: v for k, v in exchange_params.items()}
        trade_kwargs['stream_name'] = 'ctp_trade_stream'
        stream = CtpTradeStream(data_queue, **trade_kwargs)
        stream.start()
        bt_api._ctp_future_account_subscribed = True
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
