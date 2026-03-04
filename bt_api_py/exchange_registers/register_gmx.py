"""
GMX 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _gmx_balance_handler
from bt_api_py.containers.exchanges.gmx_exchange_data import GmxExchangeDataSpot
from bt_api_py.feeds.live_gmx.spot import GmxRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_gmx():
    """注册 GMX DEX 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("GMX___DEX", GmxRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("GMX___DEX", GmxExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("GMX___DEX", _gmx_balance_handler)


# 模块导入时自动注册
register_gmx()
