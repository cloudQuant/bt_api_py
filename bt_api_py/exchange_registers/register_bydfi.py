"""
BYDFi 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _bydfi_balance_handler
from bt_api_py.containers.exchanges.bydfi_exchange_data import BYDFiExchangeDataSpot
from bt_api_py.feeds.live_bydfi.spot import BYDFiRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bydfi():
    """注册 BYDFi SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BYDFI___SPOT", BYDFiRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BYDFI___SPOT", BYDFiExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BYDFI___SPOT", _bydfi_balance_handler)


# 模块导入时自动注册
register_bydfi()
