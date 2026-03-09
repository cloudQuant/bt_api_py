"""LocalBitcoins 交易所注册模块
将 LocalBitcoins Spot 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册.
"""

from bt_api_py.balance_utils import simple_balance_handler as _localbitcoins_balance_handler
from bt_api_py.containers.exchanges.localbitcoins_exchange_data import LocalBitcoinsExchangeDataSpot
from bt_api_py.feeds.live_localbitcoins import (
    LocalBitcoinsRequestDataSpot,
)
from bt_api_py.registry import ExchangeRegistry


def _localbitcoins_spot_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """LocalBitcoins SPOT 订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例 (用于访问共享状态).
    """
    exchange_data = LocalBitcoinsExchangeDataSpot()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "localbitcoins_market_data"
    kwargs["wss_url"] = ""  # LocalBitcoins does not support WebSocket
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    # Note: WebSocket not supported for LocalBitcoins
    if not bt_api._subscription_flags.get("LOCALBITCOINS___SPOT_account", False):
        bt_api._subscription_flags["LOCALBITCOINS___SPOT_account"] = True


def register_localbitcoins():
    """注册 LocalBitcoins Spot 到全局 ExchangeRegistry."""
    # Spot
    ExchangeRegistry.register_feed("LOCALBITCOINS___SPOT", LocalBitcoinsRequestDataSpot)
    ExchangeRegistry.register_exchange_data("LOCALBITCOINS___SPOT", LocalBitcoinsExchangeDataSpot)
    ExchangeRegistry.register_balance_handler(
        "LOCALBITCOINS___SPOT", _localbitcoins_balance_handler
    )
    ExchangeRegistry.register_stream(
        "LOCALBITCOINS___SPOT", "subscribe", _localbitcoins_spot_subscribe_handler
    )


# 模块导入时自动注册
register_localbitcoins()
