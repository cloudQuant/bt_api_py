"""
Coinone 交易所注册模块
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _coinone_balance_handler
from bt_api_py.containers.exchanges.coinone_exchange_data import CoinoneExchangeDataSpot
from bt_api_py.feeds.live_coinone.spot import CoinoneRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_coinone() -> None:
    """注册 Coinone SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("COINONE___SPOT", CoinoneRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("COINONE___SPOT", CoinoneExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("COINONE___SPOT", _coinone_balance_handler)


# 模块导入时自动注册
register_coinone()
