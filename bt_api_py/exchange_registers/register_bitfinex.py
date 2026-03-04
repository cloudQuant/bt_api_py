"""
Bitfinex 交易所注册模块
将 Bitfinex Spot 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _bitfinex_balance_handler
from bt_api_py.containers.exchanges.bitfinex_exchange_data import BitfinexExchangeDataSpot
from bt_api_py.feeds.live_bitfinex import (
    BitfinexRequestDataSpot,
)
from bt_api_py.registry import ExchangeRegistry


def register_bitfinex():
    """注册 Bitfinex Spot 到全局 ExchangeRegistry"""
    # Spot
    ExchangeRegistry.register_feed("BITFINEX___SPOT", BitfinexRequestDataSpot)
    ExchangeRegistry.register_exchange_data("BITFINEX___SPOT", BitfinexExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("BITFINEX___SPOT", _bitfinex_balance_handler)


# 模块导入时自动注册
register_bitfinex()
