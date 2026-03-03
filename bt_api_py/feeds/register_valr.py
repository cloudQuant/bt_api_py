"""
VALR 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _valr_balance_handler
from bt_api_py.containers.exchanges.valr_exchange_data import ValrExchangeDataSpot
from bt_api_py.feeds.live_valr.spot import ValrRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_valr():
    """注册 VALR SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("VALR___SPOT", ValrRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("VALR___SPOT", ValrExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("VALR___SPOT", _valr_balance_handler)


# 模块导入时自动注册
register_valr()
