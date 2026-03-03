"""
Bithumb 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _bithumb_balance_handler
from bt_api_py.containers.exchanges.bithumb_exchange_data import BithumbExchangeDataSpot
from bt_api_py.feeds.live_bithumb.spot import BithumbRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bithumb():
    """注册 Bithumb SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BITHUMB___SPOT", BithumbRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BITHUMB___SPOT", BithumbExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BITHUMB___SPOT", _bithumb_balance_handler)


# 模块导入时自动注册
register_bithumb()
