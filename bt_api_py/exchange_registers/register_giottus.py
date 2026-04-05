"""
Giottus 交易所注册模块
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _giottus_balance_handler
from bt_api_py.containers.exchanges.giottus_exchange_data import GiottusExchangeDataSpot
from bt_api_py.feeds.live_giottus.spot import GiottusRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_giottus():
    """注册 Giottus SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("GIOTTUS___SPOT", GiottusRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("GIOTTUS___SPOT", GiottusExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("GIOTTUS___SPOT", _giottus_balance_handler)


# 模块导入时自动注册
register_giottus()
