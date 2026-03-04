"""
Bitstamp 交易所注册模块
将 Bitstamp Spot 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _bitstamp_balance_handler
from bt_api_py.containers.exchanges.bitstamp_exchange_data import BitstampExchangeDataSpot
from bt_api_py.feeds.live_bitstamp.spot import BitstampRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bitstamp():
    """注册 Bitstamp SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BITSTAMP___SPOT", BitstampRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BITSTAMP___SPOT", BitstampExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BITSTAMP___SPOT", _bitstamp_balance_handler)


# 模块导入时自动注册
register_bitstamp()
