"""BeQuant 交易所注册模块."""

from __future__ import annotations

from bt_api_py.balance_utils import simple_balance_handler as _bequant_balance_handler
from bt_api_py.containers.exchanges.bequant_exchange_data import BeQuantExchangeDataSpot
from bt_api_py.feeds.live_bequant.spot import BeQuantRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bequant():
    """注册 BeQuant SPOT 到全局 ExchangeRegistry."""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BEQUANT___SPOT", BeQuantRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BEQUANT___SPOT", BeQuantExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BEQUANT___SPOT", _bequant_balance_handler)


# 模块导入时自动注册
register_bequant()
