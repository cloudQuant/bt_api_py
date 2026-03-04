"""
Coinbase 交易所注册模块
将 Coinbase Spot 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _coinbase_balance_handler
from bt_api_py.containers.exchanges.coinbase_exchange_data import (
    CoinbaseExchangeDataSpot,
)
from bt_api_py.feeds.live_coinbase.spot import (
    CoinbaseRequestDataSpot,
)
from bt_api_py.registry import ExchangeRegistry


def register_coinbase():
    """注册 Coinbase Spot 到全局 ExchangeRegistry"""
    # Spot
    ExchangeRegistry.register_feed("COINBASE___SPOT", CoinbaseRequestDataSpot)
    ExchangeRegistry.register_exchange_data("COINBASE___SPOT", CoinbaseExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("COINBASE___SPOT", _coinbase_balance_handler)


# 模块导入时自动注册
register_coinbase()
