"""
WazirX 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _wazirx_balance_handler
from bt_api_py.containers.exchanges.wazirx_exchange_data import WazirxExchangeDataSpot
from bt_api_py.feeds.live_wazirx.spot import WazirxRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_wazirx():
    """注册 WazirX SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("WAZIRX___SPOT", WazirxRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("WAZIRX___SPOT", WazirxExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("WAZIRX___SPOT", _wazirx_balance_handler)


# 模块导入时自动注册
register_wazirx()
