"""
Bitinka 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _bitinka_balance_handler
from bt_api_py.containers.exchanges.bitinka_exchange_data import BitinkaExchangeDataSpot
from bt_api_py.feeds.live_bitinka.spot import BitinkaRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bitinka():
    """注册 Bitinka SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BITINKA___SPOT", BitinkaRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BITINKA___SPOT", BitinkaExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BITINKA___SPOT", _bitinka_balance_handler)


# 模块导入时自动注册
register_bitinka()
