"""
dYdX 交易所注册模块
将 dYdX 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from __future__ import annotations

from bt_api_py.balance_utils import nested_balance_handler as _dydx_balance_handler
from bt_api_py.containers.exchanges.dydx_exchange_data import (
    DydxExchangeDataSwap,
)
from bt_api_py.feeds.live_dydx.spot import DydxRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def _dydx_subscribe_handler(
    data_queue,
    exchange_params,
    topics,
    bt_api,
    exchange_data_cls,
    # market_wss_cls,
    # account_wss_cls,
    # kline_wss_cls,
):
    """dYdX 通用订阅处理函数
    :param data_queue: queue.Queue
    :param exchange_params: dict
    :param topics: list of topic dicts
    :param bt_api: BtApi 实例
    :param exchange_data_cls: ExchangeData 类
    """
    topic_list = [i["topic"] for i in topics]

    # Note: WebSocket support for dYdX would need to be implemented
    # Currently only REST API is supported

    if "ticker" in topic_list or "depth" in topic_list or "trades" in topic_list:
        # Market data subscription would need WebSocket implementation
        bt_api.log("Market data subscription requested but WebSocket not implemented yet")

    if "account" in topic_list or "orders" in topic_list or "positions" in topic_list:
        # Account data subscription would need WebSocket implementation
        bt_api.log("Account data subscription requested but WebSocket not implemented yet")

    # For now, only REST API calls are available
    bt_api.log("dYdX integration registered. REST API available.")


def _dydx_swap_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """dYdX SWAP 订阅处理函数"""
    _dydx_subscribe_handler(
        data_queue,
        exchange_params,
        topics,
        bt_api,
        DydxExchangeDataSwap,
    )


def _dydx_spot_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """dYdX SPOT 订阅处理函数"""
    _dydx_subscribe_handler(
        data_queue,
        exchange_params,
        topics,
        bt_api,
        DydxExchangeDataSwap,
    )


def register_dydx():
    """注册 dYdX Swap 到全局 ExchangeRegistry"""
    # Swap
    ExchangeRegistry.register_feed("DYDX___SWAP", DydxRequestDataSpot)
    ExchangeRegistry.register_exchange_data("DYDX___SWAP", DydxExchangeDataSwap)
    ExchangeRegistry.register_balance_handler("DYDX___SWAP", _dydx_balance_handler)
    ExchangeRegistry.register_stream("DYDX___SWAP", "subscribe", _dydx_swap_subscribe_handler)

    # Spot (dYdX primarily offers perpetual contracts, but we can register spot functionality)
    ExchangeRegistry.register_feed("DYDX___SPOT", DydxRequestDataSpot)
    ExchangeRegistry.register_exchange_data("DYDX___SPOT", DydxExchangeDataSwap)
    ExchangeRegistry.register_balance_handler("DYDX___SPOT", _dydx_balance_handler)
    ExchangeRegistry.register_stream("DYDX___SPOT", "subscribe", _dydx_spot_subscribe_handler)


# 模块导入时自动注册
register_dydx()
