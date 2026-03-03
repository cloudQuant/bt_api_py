"""
YoBit 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _yobit_balance_handler
from bt_api_py.containers.exchanges.yobit_exchange_data import YobitExchangeDataSpot
from bt_api_py.feeds.live_yobit.spot import YobitRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_yobit():
    """注册 YoBit SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("YOBIT___SPOT", YobitRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("YOBIT___SPOT", YobitExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("YOBIT___SPOT", _yobit_balance_handler)


# 模块导入时自动注册
register_yobit()
