"""
Independent Reserve 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _independent_reserve_balance_handler
from bt_api_py.containers.exchanges.independent_reserve_exchange_data import IndependentReserveExchangeDataSpot
from bt_api_py.feeds.live_independent_reserve.spot import IndependentReserveRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_independent_reserve():
    """注册 Independent Reserve SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("INDEPENDENT_RESERVE___SPOT", IndependentReserveRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("INDEPENDENT_RESERVE___SPOT", IndependentReserveExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("INDEPENDENT_RESERVE___SPOT", _independent_reserve_balance_handler)


# 模块导入时自动注册
register_independent_reserve()
