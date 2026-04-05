"""
Kraken 交易所注册模块
将 Kraken 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from __future__ import annotations

from bt_api_py.balance_utils import nested_balance_handler as _kraken_balance_handler
from bt_api_py.containers.exchanges.kraken_exchange_data import (
    KrakenExchangeDataFutures,
    KrakenExchangeDataSpot,
)
from bt_api_py.feeds.live_kraken import (
    KrakenRequestDataFutures,
    KrakenRequestDataSpot,
)
from bt_api_py.registry import ExchangeRegistry


def _kraken_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Kraken 订阅处理函数 (placeholder)"""


def register_kraken():
    """注册 Kraken SPOT / FUTURES 到全局 ExchangeRegistry"""
    # ── SPOT ──
    ExchangeRegistry.register_feed("KRAKEN___SPOT", KrakenRequestDataSpot)
    ExchangeRegistry.register_exchange_data("KRAKEN___SPOT", KrakenExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("KRAKEN___SPOT", _kraken_balance_handler)
    ExchangeRegistry.register_stream("KRAKEN___SPOT", "subscribe", _kraken_subscribe_handler)

    # ── FUTURES ──
    ExchangeRegistry.register_feed("KRAKEN___FUTURES", KrakenRequestDataFutures)
    ExchangeRegistry.register_exchange_data("KRAKEN___FUTURES", KrakenExchangeDataFutures)
    ExchangeRegistry.register_balance_handler("KRAKEN___FUTURES", _kraken_balance_handler)
    ExchangeRegistry.register_stream("KRAKEN___FUTURES", "subscribe", _kraken_subscribe_handler)


# 模块导入时自动注册
register_kraken()
