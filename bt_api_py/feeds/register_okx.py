"""
OKX 交易所注册模块
将 OKX Swap/Spot 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import nested_balance_handler as _okx_balance_handler
from bt_api_py.containers.exchanges.okx_exchange_data import (
    OkxExchangeDataSpot,
    OkxExchangeDataSwap,
)
from bt_api_py.feeds.live_okx_feed import (
    OkxAccountWssDataSpot,
    OkxAccountWssDataSwap,
    OkxKlineWssDataSpot,
    OkxKlineWssDataSwap,
    OkxMarketWssDataSpot,
    OkxMarketWssDataSwap,
    OkxRequestDataSpot,
    OkxRequestDataSwap,
)
from bt_api_py.registry import ExchangeRegistry


def _okx_subscribe_handler(
    data_queue,
    exchange_params,
    topics,
    bt_api,
    exchange_data_cls,
    market_wss_cls,
    account_wss_cls,
    kline_wss_cls,
):
    """OKX 通用订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例
    :param exchange_data_cls: ExchangeData 类
    :param market_wss_cls: Market WebSocket 类
    :param account_wss_cls: Account WebSocket 类
    :param kline_wss_cls: Kline WebSocket 类
    """
    topic_list = [i["topic"] for i in topics]

    if "kline" in topic_list:
        kline_kwargs = dict(exchange_params.items())
        kline_kwargs["wss_name"] = "okx_kline_data"
        kline_kwargs["wss_url"] = "wss://ws.okx.com:8443/ws/v5/business"
        kline_kwargs["exchange_data"] = exchange_data_cls()
        kline_topics = [i for i in topics if i["topic"] == "kline"]
        kline_kwargs["topics"] = kline_topics
        kline_wss_cls(data_queue, **kline_kwargs).start()
        bt_api.log("kline start")

    ticker_true = "ticker" in topic_list
    depth_true = "depth" in topic_list
    funding_rate_true = "funding_rate" in topic_list
    mark_price_true = "mark_price" in topic_list
    if ticker_true or depth_true or funding_rate_true or mark_price_true:
        market_kwargs = dict(exchange_params.items())
        market_kwargs["wss_name"] = "okx_market_data"
        market_kwargs["wss_url"] = "wss://ws.okx.com:8443/ws/v5/public"
        market_kwargs["exchange_data"] = exchange_data_cls()
        market_topics = [i for i in topics if i["topic"] != "kline"]
        market_kwargs["topics"] = market_topics
        market_wss_cls(data_queue, **market_kwargs).start()
        bt_api.log("market start")

    account_kwargs = dict(exchange_params.items())
    account_topics = [
        i
        for i in topics
        if (i["topic"] == "account" or i["topic"] == "orders" or i["topic"] == "positions")
    ]
    account_kwargs["topics"] = account_topics
    account_kwargs["exchange_data"] = exchange_data_cls()
    account_wss_cls(data_queue, **account_kwargs).start()
    bt_api.log("account start")


def _okx_swap_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """OKX SWAP 订阅处理函数"""
    _okx_subscribe_handler(
        data_queue,
        exchange_params,
        topics,
        bt_api,
        OkxExchangeDataSwap,
        OkxMarketWssDataSwap,
        OkxAccountWssDataSwap,
        OkxKlineWssDataSwap,
    )


def _okx_spot_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """OKX SPOT 订阅处理函数"""
    _okx_subscribe_handler(
        data_queue,
        exchange_params,
        topics,
        bt_api,
        OkxExchangeDataSpot,
        OkxMarketWssDataSpot,
        OkxAccountWssDataSpot,
        OkxKlineWssDataSpot,
    )


def register_okx():
    """注册 OKX Swap 和 Spot 到全局 ExchangeRegistry"""
    # Swap
    ExchangeRegistry.register_feed("OKX___SWAP", OkxRequestDataSwap)
    ExchangeRegistry.register_exchange_data("OKX___SWAP", OkxExchangeDataSwap)
    ExchangeRegistry.register_balance_handler("OKX___SWAP", _okx_balance_handler)
    ExchangeRegistry.register_stream("OKX___SWAP", "subscribe", _okx_swap_subscribe_handler)

    # Spot
    ExchangeRegistry.register_feed("OKX___SPOT", OkxRequestDataSpot)
    ExchangeRegistry.register_exchange_data("OKX___SPOT", OkxExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("OKX___SPOT", _okx_balance_handler)
    ExchangeRegistry.register_stream("OKX___SPOT", "subscribe", _okx_spot_subscribe_handler)


# 模块导入时自动注册
register_okx()
