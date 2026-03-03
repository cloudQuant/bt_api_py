"""
Luno 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _luno_balance_handler
from bt_api_py.containers.exchanges.luno_exchange_data import LunoExchangeDataSpot
from bt_api_py.feeds.live_luno.spot import LunoRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_luno():
    """注册 Luno SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("LUNO___SPOT", LunoRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("LUNO___SPOT", LunoExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("LUNO___SPOT", _luno_balance_handler)


# 模块导入时自动注册
register_luno()
