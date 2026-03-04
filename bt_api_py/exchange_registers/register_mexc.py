"""
MEXC 交易所注册模块
将 MEXC Spot 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _mexc_balance_handler
from bt_api_py.containers.exchanges.mexc_exchange_data import MexcExchangeDataSpot
from bt_api_py.feeds.live_mexc import MexcRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_mexc():
    """注册 MEXC Spot 到全局 ExchangeRegistry"""
    ExchangeRegistry.register_feed("MEXC___SPOT", MexcRequestDataSpot)
    ExchangeRegistry.register_exchange_data("MEXC___SPOT", MexcExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("MEXC___SPOT", _mexc_balance_handler)


# 模块导入时自动注册
register_mexc()
