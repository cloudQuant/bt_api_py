"""
CoinSpot 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _coinspot_balance_handler
from bt_api_py.containers.exchanges.coinspot_exchange_data import CoinSpotExchangeDataSpot
from bt_api_py.feeds.live_coinspot.spot import CoinSpotRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_coinspot():
    """注册 CoinSpot SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("COINSPOT___SPOT", CoinSpotRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("COINSPOT___SPOT", CoinSpotExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("COINSPOT___SPOT", _coinspot_balance_handler)


# 模块导入时自动注册
register_coinspot()
