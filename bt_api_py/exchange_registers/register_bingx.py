"""
BingX 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _bingx_balance_handler
from bt_api_py.containers.exchanges.bingx_exchange_data import BingXExchangeDataSpot
from bt_api_py.feeds.live_bingx.spot import BingXRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bingx() -> None:
    """注册 BingX SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BINGX___SPOT", BingXRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BINGX___SPOT", BingXExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BINGX___SPOT", _bingx_balance_handler)


# 模块导入时自动注册
register_bingx()
