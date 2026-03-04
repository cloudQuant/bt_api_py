"""
Bitso 交易所注册模块
将 Bitso Spot 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _bitso_balance_handler
from bt_api_py.containers.exchanges.bitso_exchange_data import BitsoExchangeDataSpot
from bt_api_py.feeds.live_bitso.spot import BitsoRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bitso():
    """注册 Bitso SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BITSO___SPOT", BitsoRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BITSO___SPOT", BitsoExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BITSO___SPOT", _bitso_balance_handler)


# 模块导入时自动注册
register_bitso()
