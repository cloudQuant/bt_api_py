"""
CoinDCX 交易所注册模块
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _coindcx_balance_handler
from bt_api_py.containers.exchanges.coindcx_exchange_data import CoinDCXExchangeDataSpot
from bt_api_py.feeds.live_coindcx.spot import CoinDCXRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_coindcx():
    """注册 CoinDCX SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("COINDCX___SPOT", CoinDCXRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("COINDCX___SPOT", CoinDCXExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("COINDCX___SPOT", _coindcx_balance_handler)


# 模块导入时自动注册
register_coindcx()
