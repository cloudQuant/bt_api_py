"""
BigONE 交易所注册模块
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _bigone_balance_handler
from bt_api_py.containers.exchanges.bigone_exchange_data import BigONEExchangeDataSpot
from bt_api_py.feeds.live_bigone.spot import BigONERequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bigone():
    """注册 BigONE SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BIGONE___SPOT", BigONERequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BIGONE___SPOT", BigONEExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BIGONE___SPOT", _bigone_balance_handler)


# 模块导入时自动注册
register_bigone()
