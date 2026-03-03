"""
Bitrue 交易所注册模块
将 Bitrue Spot 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _bitrue_balance_handler
from bt_api_py.containers.exchanges.bitrue_exchange_data import BitrueExchangeDataSpot
from bt_api_py.feeds.live_bitrue.spot import BitrueRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bitrue():
    """注册 Bitrue SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BITRUE___SPOT", BitrueRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BITRUE___SPOT", BitrueExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BITRUE___SPOT", _bitrue_balance_handler)


# 模块导入时自动注册
register_bitrue()
