"""
Hyperliquid 交易所注册模块
将 Hyperliquid Swap/Spot 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _hyperliquid_balance_handler
from bt_api_py.containers.exchanges.hyperliquid_exchange_data import (
    HyperliquidExchangeDataSpot,
    HyperliquidExchangeDataSwap,
)
from bt_api_py.feeds.live_hyperliquid import (
    HyperliquidAccountWssDataSpot,
    HyperliquidMarketWssDataSpot,
    HyperliquidRequestData,
    HyperliquidRequestDataSpot,
)
from bt_api_py.registry import ExchangeRegistry


def _hyperliquid_swap_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Hyperliquid SWAP 订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例 (用于访问共享状态)
    """
    exchange_data = HyperliquidExchangeDataSwap()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "hyperliquid_market_data"
    kwargs["wss_url"] = "wss://api.hyperliquid.xyz/ws"
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    HyperliquidMarketWssDataSpot(data_queue, **kwargs).start()
    if not bt_api._subscription_flags.get("HYPERLIQUID___SWAP_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "order"},
            {"topic": "trade"},
        ]
        HyperliquidAccountWssDataSpot(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["HYPERLIQUID___SWAP_account"] = True


def _hyperliquid_spot_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Hyperliquid SPOT 订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例
    """
    exchange_data = HyperliquidExchangeDataSpot()
    kwargs = dict(exchange_params.items())
    kwargs["wss_name"] = "hyperliquid_market_data"
    kwargs["wss_url"] = "wss://api.hyperliquid.xyz/ws"
    kwargs["exchange_data"] = exchange_data
    kwargs["topics"] = topics
    HyperliquidMarketWssDataSpot(data_queue, **kwargs).start()
    if not bt_api._subscription_flags.get("HYPERLIQUID___SPOT_account", False):
        account_kwargs = dict(kwargs.items())
        account_kwargs["topics"] = [
            {"topic": "account"},
            {"topic": "order"},
            {"topic": "trade"},
        ]
        HyperliquidAccountWssDataSpot(data_queue, **account_kwargs).start()
        bt_api._subscription_flags["HYPERLIQUID___SPOT_account"] = True


def register_hyperliquid():
    """注册 Hyperliquid Swap 和 Spot 到全局 ExchangeRegistry"""
    # Swap
    ExchangeRegistry.register_feed("HYPERLIQUID___SWAP", HyperliquidRequestData)
    ExchangeRegistry.register_exchange_data("HYPERLIQUID___SWAP", HyperliquidExchangeDataSwap)
    ExchangeRegistry.register_balance_handler("HYPERLIQUID___SWAP", _hyperliquid_balance_handler)
    ExchangeRegistry.register_stream(
        "HYPERLIQUID___SWAP", "subscribe", _hyperliquid_swap_subscribe_handler
    )

    # Spot
    ExchangeRegistry.register_feed("HYPERLIQUID___SPOT", HyperliquidRequestDataSpot)
    ExchangeRegistry.register_exchange_data("HYPERLIQUID___SPOT", HyperliquidExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("HYPERLIQUID___SPOT", _hyperliquid_balance_handler)
    ExchangeRegistry.register_stream(
        "HYPERLIQUID___SPOT", "subscribe", _hyperliquid_spot_subscribe_handler
    )


# 模块导入时自动注册
register_hyperliquid()
