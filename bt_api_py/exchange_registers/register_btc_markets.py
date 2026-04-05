"""
BTC Markets 交易所注册模块
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _btc_markets_balance_handler
from bt_api_py.containers.exchanges.btc_markets_exchange_data import BtcMarketsExchangeDataSpot
from bt_api_py.feeds.live_btc_markets.spot import BtcMarketsRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_btc_markets():
    """注册 BTC Markets SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BTC_MARKETS___SPOT", BtcMarketsRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BTC_MARKETS___SPOT", BtcMarketsExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BTC_MARKETS___SPOT", _btc_markets_balance_handler)


# 模块导入时自动注册
register_btc_markets()
