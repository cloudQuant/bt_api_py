"""
Crypto.com 交易所注册模块
将 Crypto.com Spot 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _cryptocom_balance_handler
from bt_api_py.containers.exchanges.cryptocom_exchange_data import CryptoComExchangeDataSpot
from bt_api_py.feeds.live_cryptocom.spot import CryptoComRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def _cryptocom_spot_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Crypto.com SPOT 订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例 (用于访问共享状态)
    """
    exchange_data = CryptoComExchangeDataSpot()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "cryptocom_market_data"
    kwargs["wss_url"] = "wss://stream.crypto.com/exchange/v1/market"
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    # TODO: Implement WebSocket feed for Crypto.com
    # CryptoComMarketWssDataSpot(data_queue, **kwargs).start()

    if not bt_api._subscription_flags.get("CRYPTOCOM___SPOT_account", False):
        # TODO: Implement account WebSocket feed
        # account_kwargs = dict(kwargs.items())
        # account_kwargs["topics"] = [
        #     {"topic": "account"},
        #     {"topic": "order"},
        #     {"topic": "trade"},
        # ]
        # CryptoComAccountWssDataSpot(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["CRYPTOCOM___SPOT_account"] = True


def register_cryptocom():
    """注册 Crypto.com Spot 到全局 ExchangeRegistry"""
    # Spot
    ExchangeRegistry.register_feed("CRYPTOCOM___SPOT", CryptoComRequestDataSpot)
    ExchangeRegistry.register_exchange_data("CRYPTOCOM___SPOT", CryptoComExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("CRYPTOCOM___SPOT", _cryptocom_balance_handler)
    ExchangeRegistry.register_stream("CRYPTOCOM___SPOT", "subscribe", _cryptocom_spot_subscribe_handler)


# 模块导入时自动注册
register_cryptocom()