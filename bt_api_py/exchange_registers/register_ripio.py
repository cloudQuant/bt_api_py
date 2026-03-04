"""
Ripio 交易所注册模块
将 Ripio 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _ripio_balance_handler
from bt_api_py.containers.exchanges.ripio_exchange_data import RipioExchangeDataSpot
from bt_api_py.feeds.live_ripio.spot import RipioRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_ripio():
    """注册 Ripio 到全局 ExchangeRegistry"""
    # Spot
    ExchangeRegistry.register_feed("RIPIO___SPOT", RipioRequestDataSpot)
    ExchangeRegistry.register_exchange_data("RIPIO___SPOT", RipioExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("RIPIO___SPOT", _ripio_balance_handler)


# 模块导入时自动注册
register_ripio()
