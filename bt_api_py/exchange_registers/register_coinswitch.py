"""
CoinSwitch 交易所注册模块
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _coinswitch_balance_handler
from bt_api_py.containers.exchanges.coinswitch_exchange_data import CoinSwitchExchangeDataSpot
from bt_api_py.feeds.live_coinswitch.spot import CoinSwitchRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_coinswitch():
    """注册 CoinSwitch SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("COINSWITCH___SPOT", CoinSwitchRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("COINSWITCH___SPOT", CoinSwitchExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("COINSWITCH___SPOT", _coinswitch_balance_handler)


# 模块导入时自动注册
register_coinswitch()
