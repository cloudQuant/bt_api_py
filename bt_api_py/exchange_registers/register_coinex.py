"""
CoinEx 交易所注册模块
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _coinex_balance_handler
from bt_api_py.containers.exchanges.coinex_exchange_data import CoinExExchangeDataSpot
from bt_api_py.feeds.live_coinex.spot import CoinExRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_coinex():
    """注册 Coinex SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("COINEX___SPOT", CoinExRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("COINEX___SPOT", CoinExExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("COINEX___SPOT", _coinex_balance_handler)


# 模块导入时自动注册
register_coinex()
