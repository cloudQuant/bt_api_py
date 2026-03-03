"""
CoW Swap 交易所注册模块
"""

from bt_api_py.balance_utils import simple_balance_handler as _cow_swap_balance_handler
from bt_api_py.containers.exchanges.cow_swap_exchange_data import CowSwapExchangeDataSpot
from bt_api_py.feeds.live_cow_swap.spot import CowSwapRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def register_cow_swap():
    """注册 CoW Swap SPOT 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("COW_SWAP___SPOT", CowSwapRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("COW_SWAP___SPOT", CowSwapExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("COW_SWAP___SPOT", _cow_swap_balance_handler)


# 模块导入时自动注册
register_cow_swap()
