"""
CTP 交易所注册模块
将 CTP Future 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册

依赖: pip install ctp-python
"""

from typing import Any

from bt_api_py.balance_utils import simple_balance_handler as _ctp_balance_handler
from bt_api_py.containers.exchanges.ctp_exchange_data import CtpExchangeDataFuture
from bt_api_py.feeds.live_ctp_feed import (
    CtpMarketStream,
    CtpRequestDataFuture,
    CtpTradeStream,
)
from bt_api_py.registry import ExchangeRegistry


def _ctp_future_subscribe_handler(
    data_queue: Any,
    exchange_params: dict[str, Any],
    topics: list[dict[str, Any]],
    bt_api: Any,
) -> None:
    """CTP FUTURE 订阅处理函数，启动行情流和交易流.

    Args:
        data_queue: Queue for data transmission.
        exchange_params: Dict containing broker_id, user_id, password, md_front, td_front, etc.
        topics: List of topic dicts, e.g. [{"topic": "tick", "symbol": "IF2506"}, ...].
        bt_api: BtApi instance.
    """
    topic_list = [t.get("topic") for t in topics]

    has_tick = any(t in topic_list for t in ("tick", "ticker", "depth"))
    if has_tick:
        market_kwargs = dict(exchange_params.items())
        market_kwargs["stream_name"] = "ctp_market_stream"
        market_kwargs["topics"] = topics
        stream = CtpMarketStream(data_queue, **market_kwargs)
        stream.start()
        bt_api.log("CTP market stream started")

    if not bt_api._subscription_flags.get("CTP___FUTURE_account", False):
        trade_kwargs = dict(exchange_params.items())
        trade_kwargs["stream_name"] = "ctp_trade_stream"
        stream = CtpTradeStream(data_queue, **trade_kwargs)
        stream.start()
        bt_api._subscription_flags["CTP___FUTURE_account"] = True
        bt_api.log("CTP trade stream started")


def register_ctp() -> None:
    """Register CTP Future to global ExchangeRegistry.

    This function registers:
    - Feed class for market data
    - Exchange data configuration
    - Balance handler for account management
    - Subscribe handler for stream management
    - Market stream for tick/depth data
    - Account stream for order/trade data
    """
    ExchangeRegistry.register_feed("CTP___FUTURE", CtpRequestDataFuture)
    ExchangeRegistry.register_exchange_data("CTP___FUTURE", CtpExchangeDataFuture)
    ExchangeRegistry.register_balance_handler("CTP___FUTURE", _ctp_balance_handler)
    ExchangeRegistry.register_stream("CTP___FUTURE", "subscribe", _ctp_future_subscribe_handler)
    ExchangeRegistry.register_stream("CTP___FUTURE", "market", CtpMarketStream)
    ExchangeRegistry.register_stream("CTP___FUTURE", "account", CtpTradeStream)


register_ctp()
