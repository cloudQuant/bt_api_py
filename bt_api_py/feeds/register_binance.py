"""
Binance 交易所注册模块
将 Binance Swap/Spot 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""
from bt_api_py.registry import ExchangeRegistry
from bt_api_py.feeds.live_binance_feed import (
    BinanceRequestDataSwap,
    BinanceRequestDataSpot,
    BinanceMarketWssDataSwap,
    BinanceMarketWssDataSpot,
    BinanceAccountWssDataSwap,
    BinanceAccountWssDataSpot,
)
from bt_api_py.containers.exchanges.binance_exchange_data import (
    BinanceExchangeDataSwap,
    BinanceExchangeDataSpot,
)


def _binance_balance_handler(account_list):
    """Binance 通用余额解析处理函数（Swap 和 Spot 逻辑相同）
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


def _binance_swap_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Binance SWAP 订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例 (用于访问共享状态)
    """
    exchange_data = BinanceExchangeDataSwap()
    kwargs = {key: v for key, v in exchange_params.items()}
    kwargs['wss_name'] = 'binance_market_data'
    kwargs["wss_url"] = 'wss://fstream.binance.com/ws'
    kwargs["exchange_data"] = exchange_data
    kwargs['topics'] = topics
    BinanceMarketWssDataSwap(data_queue, **kwargs).start()
    if not getattr(bt_api, '_binance_swap_account_subscribed', False):
        account_kwargs = {k: v for k, v in kwargs.items()}
        account_kwargs['topics'] = [
            {"topic": "account"},
            {"topic": "order"},
            {"topic": "trade"},
        ]
        BinanceAccountWssDataSwap(data_queue, **account_kwargs).start()
        bt_api._binance_swap_account_subscribed = True


def _binance_spot_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Binance SPOT 订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例
    """
    exchange_data = BinanceExchangeDataSpot()
    kwargs = {key: v for key, v in exchange_params.items()}
    kwargs['wss_name'] = 'binance_market_data'
    kwargs["wss_url"] = 'wss://fstream.binance.com/ws'
    kwargs["exchange_data"] = exchange_data
    kwargs['topics'] = topics
    BinanceMarketWssDataSpot(data_queue, **kwargs).start()
    if not getattr(bt_api, '_binance_spot_account_subscribed', False):
        account_kwargs = {k: v for k, v in kwargs.items()}
        account_kwargs['topics'] = [
            {"topic": "account"},
            {"topic": "order"},
            {"topic": "trade"},
        ]
        BinanceAccountWssDataSpot(data_queue, **account_kwargs).start()
        bt_api._binance_spot_account_subscribed = True


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
