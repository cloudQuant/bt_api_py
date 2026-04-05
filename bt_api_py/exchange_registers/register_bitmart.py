"""
BitMart 交易所注册模块
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _bitmart_balance_handler
from bt_api_py.containers.exchanges.bitmart_exchange_data import BitmartExchangeDataSpot
from bt_api_py.feeds.live_bitmart.spot import BitmartRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bitmart():
    """注册 BitMart SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BITMART___SPOT", BitmartRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BITMART___SPOT", BitmartExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BITMART___SPOT", _bitmart_balance_handler)


# 模块导入时自动注册
register_bitmart()
