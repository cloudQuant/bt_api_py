"""
Raydium 交易所注册模块
将 Raydium DEX 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.balance_utils import simple_balance_handler as _raydium_balance_handler
from bt_api_py.containers.exchanges.raydium_exchange_data import RaydiumExchangeDataSpot
from bt_api_py.feeds.live_raydium.spot import RaydiumRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_raydium():
    """注册 Raydium DEX 到全局 ExchangeRegistry"""
    # DEX (Solana)
    ExchangeRegistry.register_feed("RAYDIUM___DEX", RaydiumRequestDataSpot)
    ExchangeRegistry.register_exchange_data("RAYDIUM___DEX", RaydiumExchangeDataSpot)
    ExchangeRegistry.register_balance_handler("RAYDIUM___DEX", _raydium_balance_handler)


# 模块导入时自动注册
register_raydium()
