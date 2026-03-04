"""
Phemex 交易所注册模块
将 Phemex 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _phemex_balance_handler
from bt_api_py.containers.exchanges.phemex_exchange_data import (
    PhemexExchangeDataPerpetual,
    PhemexExchangeDataSpot,
)
from bt_api_py.feeds.live_phemex.spot import PhemexRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_phemex():
    """注册 Phemex 到全局 ExchangeRegistry"""
    # Spot
    ExchangeRegistry.register_feed("PHEMEX___SPOT", PhemexRequestDataSpot)
    ExchangeRegistry.register_exchange_data("PHEMEX___SPOT", PhemexExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("PHEMEX___SPOT", _phemex_balance_handler)

    # Perpetual (placeholder for future implementation)
    # ExchangeRegistry.register_feed("PHEMEX___PERPETUAL", PhemexRequestDataPerpetual)
    # ExchangeRegistry.register_exchange_data("PHEMEX___PERPETUAL", PhemexExchangeDataPerpetual)
    # ExchangeRegistry.register_balance_handler("PHEMEX___PERPETUAL", _phemex_balance_handler)


# 模块导入时自动注册
register_phemex()
