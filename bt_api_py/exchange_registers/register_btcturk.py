"""
BTCTurk 交易所注册模块
"""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _btcturk_balance_handler
from bt_api_py.containers.exchanges.btcturk_exchange_data import BTCTurkExchangeDataSpot
from bt_api_py.feeds.live_btcturk.spot import BTCTurkRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_btcturk():
    """注册 BTCTurk SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BTCTURK___SPOT", BTCTurkRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BTCTURK___SPOT", BTCTurkExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BTCTURK___SPOT", _btcturk_balance_handler)


# 模块导入时自动注册
register_btcturk()
