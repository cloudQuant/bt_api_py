"""
Bitunix 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _bitunix_balance_handler
from bt_api_py.containers.exchanges.bitunix_exchange_data import BitunixExchangeDataSpot
from bt_api_py.feeds.live_bitunix.spot import BitunixRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bitunix():
    """注册 Bitunix SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BITUNIX___SPOT", BitunixRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BITUNIX___SPOT", BitunixExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BITUNIX___SPOT", _bitunix_balance_handler)


# 模块导入时自动注册
register_bitunix()
