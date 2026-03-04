"""
Coincheck 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _coincheck_balance_handler
from bt_api_py.containers.exchanges.coincheck_exchange_data import CoincheckExchangeDataSpot
from bt_api_py.feeds.live_coincheck.spot import CoincheckRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_coincheck():
    """注册 Coincheck SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("COINCHECK___SPOT", CoincheckRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("COINCHECK___SPOT", CoincheckExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("COINCHECK___SPOT", _coincheck_balance_handler)


# 模块导入时自动注册
register_coincheck()
