"""Crypto.com 交易所注册模块
将 Crypto.com Spot 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册.
"""

from bt_api_py.balance_utils import simple_balance_handler as _cryptocom_balance_handler
from bt_api_py.containers.exchanges.cryptocom_exchange_data import CryptoComExchangeDataSpot
from bt_api_py.feeds.live_cryptocom.spot import CryptoComRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_cryptocom():
    """注册 Crypto.com Spot 到全局 ExchangeRegistry."""
    ExchangeRegistry.register_feed("CRYPTOCOM___SPOT", CryptoComRequestDataSpot)
    ExchangeRegistry.register_exchange_data("CRYPTOCOM___SPOT", CryptoComExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("CRYPTOCOM___SPOT", _cryptocom_balance_handler)


# 模块导入时自动注册
register_cryptocom()
