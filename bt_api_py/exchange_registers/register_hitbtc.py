"""HitBTC 交易所注册模块
将 HitBTC 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册.
"""

from bt_api_py.balance_utils import simple_balance_handler as _hitbtc_balance_handler
from bt_api_py.containers.exchanges.hitbtc_exchange_data import (
    HitBtcExchangeDataSpot,
)
from bt_api_py.feeds.live_hitbtc.spot import HitBtcSpotRequestData
from bt_api_py.registry import ExchangeRegistry


def register_hitbtc():
    """注册 HitBTC 到全局 ExchangeRegistry."""
    # Spot
    ExchangeRegistry.register_feed("HITBTC___SPOT", HitBtcSpotRequestData)
    ExchangeRegistry.register_exchange_data("HITBTC___SPOT", HitBtcExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("HITBTC___SPOT", _hitbtc_balance_handler)


# 模块导入时自动注册
register_hitbtc()
