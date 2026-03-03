"""
Zaif 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _zaif_balance_handler
from bt_api_py.containers.exchanges.zaif_exchange_data import ZaifExchangeDataSpot
from bt_api_py.feeds.live_zaif.spot import ZaifRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_zaif():
    """注册 Zaif SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("ZAIF___SPOT", ZaifRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("ZAIF___SPOT", ZaifExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("ZAIF___SPOT", _zaif_balance_handler)


# 模块导入时自动注册
register_zaif()
