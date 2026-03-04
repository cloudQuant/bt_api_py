"""
Buda 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _buda_balance_handler
from bt_api_py.containers.exchanges.buda_exchange_data import BudaExchangeDataSpot
from bt_api_py.feeds.live_buda.spot import BudaRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_buda():
    """注册 Buda SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BUDA___SPOT", BudaRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BUDA___SPOT", BudaExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BUDA___SPOT", _buda_balance_handler)


# 模块导入时自动注册
register_buda()
