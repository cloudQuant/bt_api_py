"""
Balancer 交易所注册模块
将 Balancer DEX 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.containers.exchanges.balancer_exchange_data import BalancerExchangeDataSpot
from bt_api_py.feeds.live_balancer.spot import BalancerRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def _balancer_balance_handler(account_list):
    """Balancer 余额处理函数

    Balancer 是 DEX，余额查询需要通过 Web3 直接查询链上合约。
    这里提供一个基础的处理函数框架。

    Args:
        account_list: 账户数据列表

    Returns:
        tuple: (value_result, cash_result)
    """
    value_result = []
    cash_result = []

    for account in account_list:
        # Balancer 余额数据需要特殊处理
        # 这里提供基础框架
        if isinstance(account, dict):
            value_result.append(account)
            cash_result.append(account)

    return value_result, cash_result


def register_balancer():
    """注册 Balancer DEX 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("BALANCER___DEX", BalancerRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("BALANCER___DEX", BalancerExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("BALANCER___DEX", _balancer_balance_handler)


# 模块导入时自动注册
register_balancer()
