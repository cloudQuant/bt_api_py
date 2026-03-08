"""
Mercado Bitcoin 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _mercado_bitcoin_balance_handler
from bt_api_py.containers.exchanges.mercado_bitcoin_exchange_data import (
    MercadoBitcoinExchangeDataSpot,
)
from bt_api_py.feeds.live_mercado_bitcoin.spot import MercadoBitcoinRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_mercado_bitcoin():
    """注册 Mercado Bitcoin SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("MERCADO_BITCOIN___SPOT", MercadoBitcoinRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data(
        "MERCADO_BITCOIN___SPOT", MercadoBitcoinExchangeDataSpot
    )

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler(
        "MERCADO_BITCOIN___SPOT", _mercado_bitcoin_balance_handler
    )


# 模块导入时自动注册
register_mercado_bitcoin()
