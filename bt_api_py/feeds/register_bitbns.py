"""
Bitbns 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _bitbns_balance_handler
from bt_api_py.containers.exchanges.bitbns_exchange_data import BitbnsExchangeDataSpot
from bt_api_py.feeds.live_bitbns.spot import BitbnsRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_bitbns():
    """注册 Bitbns SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BITBNS___SPOT", BitbnsRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BITBNS___SPOT", BitbnsExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BITBNS___SPOT", _bitbns_balance_handler)


# 模块导入时自动注册
register_bitbns()
