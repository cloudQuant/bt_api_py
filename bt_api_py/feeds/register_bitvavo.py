"""
Bitvavo 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _bitvavo_balance_handler
from bt_api_py.containers.exchanges.bitvavo_exchange_data import BitvavoExchangeDataSpot
from bt_api_py.feeds.live_bitvavo.spot import BitvavoRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bitvavo():
    """注册 Bitvavo SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BITVAVO___SPOT", BitvavoRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BITVAVO___SPOT", BitvavoExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BITVAVO___SPOT", _bitvavo_balance_handler)


# 模块导入时自动注册
register_bitvavo()
