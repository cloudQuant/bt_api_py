"""
Uniswap 交易所注册模块
将 Uniswap DEX 的 feed 类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.containers.exchanges.uniswap_exchange_data import UniswapExchangeDataSpot
from bt_api_py.feeds.live_uniswap.spot import UniswapRequestDataSpot
from bt_api_py.registry import ExchangeRegistry


def _uniswap_balance_handler(account_list):
    """Uniswap 余额处理函数

    Uniswap 是 DEX，余额查询需要通过 Web3 直接查询链上合约。
    这里提供一个基础的处理函数框架。

    Args:
        account_list: 账户数据列表

    Returns:
        tuple: (value_result, cash_result)
    """
    value_result = []
    cash_result = []

    for account in account_list:
        # Uniswap 余额数据需要特殊处理
        # 这里提供基础框架
        if isinstance(account, dict):
            value_result.append(account)
            cash_result.append(account)

    return value_result, cash_result


def _uniswap_order_handler(order_data):
    """Uniswap 订单处理函数

    Uniswap 使用不同的订单格式，需要适配订单数据。

    Args:
        order_data: 订单数据

    Returns:
        处理后的订单数据
    """
    # Uniswap 订单格式适配
    if isinstance(order_data, dict):
        # 添加 Uniswap 特有的字段
        order_data["exchange_type"] = "DEX"
        order_data["protocol"] = "Uniswap"

        # 如果是限价单，添加相关字段
        if order_data.get("type") == "limit":
            order_data["slippage_tolerance"] = order_data.get("slippage_tolerance", 0.5)

    return order_data


def register_uniswap():
    """注册 Uniswap DEX 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("UNISWAP___DEX", UniswapRequestDataSpot)

    # 注册配置类
    ExchangeRegistry.register_exchange_data("UNISWAP___DEX", UniswapExchangeDataSpot)

    # 注册余额处理器
    ExchangeRegistry.register_balance_handler("UNISWAP___DEX", _uniswap_balance_handler)

    # Note: Order handler is not registered in ExchangeRegistry as there's no
    # register_order_handler method. The _uniswap_order_handler function is
    # available for use directly if needed.


# 模块导入时自动注册
register_uniswap()
