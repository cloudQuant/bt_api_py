"""
bitFlyer 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _bitflyer_balance_handler
from bt_api_py.containers.exchanges.bitflyer_exchange_data import BitflyerExchangeDataSpot
from bt_api_py.feeds.live_bitflyer.spot import BitflyerRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bitflyer():
    """注册 bitFlyer SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BITFLYER___SPOT", BitflyerRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BITFLYER___SPOT", BitflyerExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BITFLYER___SPOT", _bitflyer_balance_handler)


# 模块导入时自动注册
register_bitflyer()
